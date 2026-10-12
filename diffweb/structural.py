"""Turn difftastic's JSON into a unified diff that diff2html can draw.

difftastic knows which lines changed *syntactically* and how the two sides line
up, but it does not know how to draw a web page, and diff2html knows how to draw
a web page but only reads unified diffs. This module is the adapter: git's own
patch is the base (so renames, mode changes and binaries keep their headers),
and for every text file difftastic looked at the hunks are rebuilt from its
verdict. A line difftastic calls unchanged - a reformat, a whitespace shuffle -
becomes context, shown in its *after* spelling; a line it calls changed becomes
a `-`/`+` pair, and diff2html's word highlighting does the rest.

difftastic's `--display json` is behind `DFT_UNSTABLE=yes` and emits, per file:

    {"path", "language", "status": changed|unchanged|created|deleted,
     "aligned_lines": [[lhs_line|null, rhs_line|null], ...],   # 0-based, whole file
     "chunks": [[{"lhs": {"line_number", "changes": [...]}, "rhs": {...}}, ...], ...]}

`aligned_lines` is the side-by-side layout: every line of each side appears
exactly once, in order, paired with its counterpart or with null. `chunks` list
only the lines that carry a change. Files with nothing to say (created, deleted,
binary) come with neither, and git's patch for them is kept as it is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

CONTEXT = 3

Fetch = Callable[[str, str], bytes | None]  # (rev, path) -> contents, or None if absent

_DIFF_GIT = re.compile(r"^diff --git a/(.*) b/(.*)$")


@dataclass
class Patch:
    """One file's slice of a `git diff`: header lines, then the hunks."""

    header: list[str]
    hunks: list[str] = field(default_factory=list)

    @property
    def old_path(self) -> str | None:
        return _path_from(self.header, "--- a/") or _diff_git_paths(self.header)[0]

    @property
    def new_path(self) -> str | None:
        return _path_from(self.header, "+++ b/") or _diff_git_paths(self.header)[1]

    def text(self) -> str:
        return "".join(line + "\n" for line in [*self.header, *self.hunks])


def _path_from(header: list[str], prefix: str) -> str | None:
    for line in header:
        if line.startswith(prefix):
            return line[len(prefix) :].split("\t")[0]
    return None


def _diff_git_paths(header: list[str]) -> tuple[str | None, str | None]:
    m = _DIFF_GIT.match(header[0]) if header else None
    return (m.group(1), m.group(2)) if m else (None, None)


def split_patches(diff: str) -> list[Patch]:
    """git's diff, one Patch per file. Everything up to the first `@@` is header."""
    patches: list[Patch] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            patches.append(Patch(header=[line]))
            continue
        if not patches:
            continue  # noise before the first file header
        cur = patches[-1]
        if cur.hunks or line.startswith("@@"):
            cur.hunks.append(line)
        else:
            cur.header.append(line)
    return patches


def parse_difft(out: str) -> list[dict]:
    """difft under `git diff --ext-diff` prints one JSON document per file."""
    entries = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue  # a warning, or a file difft gave up on
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _lines(blob: bytes) -> list[str]:
    """Split the way difftastic numbers lines: a trailing newline yields a final
    empty "line", which is dropped here so it never becomes a phantom row."""
    text = blob.decode("utf-8", errors="replace")
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _changed_lines(chunks: list[list[dict]]) -> tuple[set[int], set[int]]:
    lhs: set[int] = set()
    rhs: set[int] = set()
    for chunk in chunks:
        for entry in chunk:
            if "lhs" in entry:
                lhs.add(entry["lhs"]["line_number"])
            if "rhs" in entry:
                rhs.add(entry["rhs"]["line_number"])
    return lhs, rhs


# A row of the synthesised diff: kind is " ", "-" or "+", text is the line.
Row = tuple[str, str]


def rows_from_alignment(
    lhs: list[str],
    rhs: list[str],
    aligned: list[list[int | None]],
    lhs_changed: set[int],
    rhs_changed: set[int],
) -> list[Row]:
    """Walk difftastic's alignment and emit unified-diff rows.

    Every real line of each side comes out exactly once, so the line counts a
    reader (diff2html) derives from the hunk headers are exact. Removals and
    additions between two unchanged pairs are buffered and flushed as all the
    `-` lines then all the `+` lines, which is how git writes a hunk too.
    """
    rows: list[Row] = []
    dels: list[str] = []
    adds: list[str] = []

    def flush() -> None:
        rows.extend(("-", t) for t in dels)
        rows.extend(("+", t) for t in adds)
        dels.clear()
        adds.clear()

    for l, r in aligned:
        has_l = l is not None and l < len(lhs)
        has_r = r is not None and r < len(rhs)
        if has_l and has_r and l not in lhs_changed and r not in rhs_changed:
            flush()
            # Context shows the *after* text: when difftastic says a line is
            # unchanged but the bytes differ, that is exactly the noise
            # (reindentation, a rewrapped comment) the structural view exists to hide.
            rows.append((" ", rhs[r]))
            continue
        if has_l:
            dels.append(lhs[l])
        if has_r:
            adds.append(rhs[r])
    flush()
    return rows


def hunks_from_rows(rows: list[Row], context: int = CONTEXT) -> list[str]:
    """Group changed rows into hunks with `context` lines either side, git-style."""
    changed = [i for i, (kind, _) in enumerate(rows) if kind != " "]
    if not changed:
        return []

    # Running old/new line numbers *before* each row, so a hunk can be headed.
    old_at = [0] * (len(rows) + 1)
    new_at = [0] * (len(rows) + 1)
    for i, (kind, _) in enumerate(rows):
        old_at[i + 1] = old_at[i] + (kind != "+")
        new_at[i + 1] = new_at[i] + (kind != "-")

    # Two changes closer than 2*context share a hunk.
    spans: list[list[int]] = []
    for i in changed:
        if spans and i - spans[-1][1] <= 2 * context:
            spans[-1][1] = i
        else:
            spans.append([i, i])

    out: list[str] = []
    for first, last in spans:
        s = max(0, first - context)
        e = min(len(rows), last + context + 1)
        old_n = old_at[e] - old_at[s]
        new_n = new_at[e] - new_at[s]
        # git heads an empty side with the line *before* the hunk.
        old_start = old_at[s] + 1 if old_n else old_at[s]
        new_start = new_at[s] + 1 if new_n else new_at[s]
        out.append(f"@@ -{old_start},{old_n} +{new_start},{new_n} @@")
        out.extend(kind + text for kind, text in rows[s:e])
    return out


def structural_patch(
    patch: Patch, entry: dict, fetch: Fetch, start: str, end: str
) -> Patch:
    """git's patch for one file, with its hunks replaced by difftastic's reading.

    Anything difftastic had no alignment for - a new, deleted or binary file -
    keeps git's hunks. A file it calls unchanged (a pure reformat) keeps its
    header and loses its hunks, so it is still listed but has nothing to read.
    """
    status = entry.get("status")
    if status == "unchanged":
        return Patch(header=patch.header)
    aligned = entry.get("aligned_lines")
    if status != "changed" or not aligned or entry.get("language") == "Binary":
        return patch
    old_path, new_path = patch.old_path, patch.new_path
    if not old_path or not new_path:
        return patch
    old = fetch(start, old_path)
    new = fetch(end, new_path)
    if old is None or new is None:
        return patch
    lhs_changed, rhs_changed = _changed_lines(entry.get("chunks", []))
    rows = rows_from_alignment(_lines(old), _lines(new), aligned, lhs_changed, rhs_changed)
    return Patch(header=patch.header, hunks=hunks_from_rows(rows))


def merge(git_diff: str, difft_out: str, fetch: Fetch, start: str, end: str) -> str:
    """The whole range as a unified diff with difftastic's hunks where it had a view."""
    patches = split_patches(git_diff)
    by_path: dict[str, Patch] = {}
    for p in patches:
        for key in (p.old_path, p.new_path):
            if key:
                by_path.setdefault(key, p)
    replaced: dict[int, Patch] = {}
    for entry in parse_difft(difft_out):
        p = by_path.get(entry.get("path", ""))
        if p is None:
            continue
        replaced[id(p)] = structural_patch(p, entry, fetch, start, end)
    return "".join(replaced.get(id(p), p).text() for p in patches)
