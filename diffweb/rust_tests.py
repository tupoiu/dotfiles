"""Rust tests that sit in the file they test, for "hide tests".

A test *file* is left out by a git pathspec. A Rust file keeps its unit tests
at the bottom, so "hide tests" also cuts each `.rs` file at its test region:
from the first `#[cfg(test)]` or `mod tests` line to the end of the file. This
is the same rule as the `loc` command, so the two agree on what is a test.

The old and new sides of a file are cut separately: an added or context line
is judged by the new file, and a removed line by the old one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

TEST_START = re.compile(r"^\s*(#\[cfg\(test\)\]|mod tests\b)")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def test_start(text: str | None) -> int | None:
    """The first line (from 1) of the test region, or None if there is none."""
    if text is None:
        return None
    for number, line in enumerate(text.splitlines(), start=1):
        if TEST_START.match(line):
            return number
    return None


@dataclass
class Hidden:
    added: int = 0
    removed: int = 0
    # Every change in the file was a test: it is gone from the patch.
    whole_file: bool = False
    # The new side's test start, so the page does not expand into it.
    new_start: int | None = None


def _side_paths(header: list[str]) -> tuple[str | None, str | None]:
    """The old and new paths from a file's `---`/`+++` lines. None for /dev/null.

    A quoted path (git quotes unusual names) is not unquoted: such a file is
    left as it is.
    """
    old = new = None
    for line in header:
        if line.startswith("--- "):
            old = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ "):
            new = line[6:] if line.startswith("+++ b/") else None
    return old, new


def _cut_hunk(header: str, body: list[str], old_start: int | None, new_start: int | None) -> tuple[list[str], int, int]:
    """The hunk with its test lines dropped, as zero or more hunks.

    Returns the new lines (headers included) and how many added and removed
    lines were dropped.
    """
    m = _HUNK.match(header)
    if not m:
        return [header, *body], 0, 0
    o, n, label = int(m.group(1)), int(m.group(3)), m.group(5)
    # Each kept line with the old and new line numbers it sits at.
    runs: list[list[tuple[str, int, int]]] = [[]]
    dropped_add = dropped_del = 0
    kept_last = False
    for line in body:
        kind = line[:1]
        if kind == "\\":  # "\ No newline at end of file" goes with the line before
            if kept_last:
                runs[-1].append((line, o, n))
            continue
        in_new = new_start is not None and n >= new_start
        in_old = old_start is not None and o >= old_start
        test = (kind == "+" and in_new) or (kind == "-" and in_old) or (kind == " " and (in_new or in_old))
        if test:
            dropped_add += kind == "+"
            dropped_del += kind == "-"
            if runs[-1]:
                runs.append([])
        else:
            runs[-1].append((line, o, n))
        kept_last = not test
        o += kind in " -"
        n += kind in " +"

    out: list[str] = []
    for i, run in enumerate(r for r in runs if any(line[:1] in "+-" for line, _, _ in r)):
        olds = [lo for line, lo, _ in run if line[:1] in " -"]
        news = [ln for line, _, ln in run if line[:1] in " +"]
        # With no lines on a side, git's start is the line before the change.
        o_start = olds[0] if olds else run[0][1] - 1
        n_start = news[0] if news else run[0][2] - 1
        out.append(f"@@ -{o_start},{len(olds)} +{n_start},{len(news)} @@{label if i == 0 else ''}")
        out.extend(line for line, _, _ in run)
    return out, dropped_add, dropped_del


def hide(diff: str, read: Callable[[str, str], str | None]) -> tuple[str, dict[str, Hidden]]:
    """`diff` with the test region of each `.rs` file left out.

    `read(side, path)` gives a file's text on the "old" or "new" side, or None.
    The dict has an entry for each `.rs` file with a test region on either
    side, keyed by its new path (its old path when it was deleted): what was
    taken out, and where the new side's tests start.
    """
    if ".rs" not in diff:
        return diff, {}
    out: list[str] = []
    hidden: dict[str, Hidden] = {}
    ends_with_newline = diff.endswith("\n")
    lines = (diff[:-1] if ends_with_newline else diff).split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
    out.extend(lines[: starts[0]] if starts else lines)
    for k, begin in enumerate(starts):
        chunk = lines[begin : starts[k + 1] if k + 1 < len(starts) else len(lines)]
        first_hunk = next((i for i, line in enumerate(chunk) if line.startswith("@@")), len(chunk))
        old, new = _side_paths(chunk[:first_hunk])
        if not any(p and p.endswith(".rs") for p in (old, new)):
            out.extend(chunk)
            continue
        old_start = test_start(read("old", old)) if old else None
        new_start = test_start(read("new", new)) if new else None
        if old_start is None and new_start is None:
            out.extend(chunk)
            continue
        info = Hidden(new_start=new_start)
        body: list[str] = []
        hunk_header: str | None = None
        hunk_body: list[str] = []

        def flush() -> None:
            if hunk_header is None:
                return
            cut, add, rem = _cut_hunk(hunk_header, hunk_body, old_start, new_start)
            body.extend(cut)
            info.added += add
            info.removed += rem

        for line in chunk[first_hunk:]:
            if line.startswith("@@"):
                flush()
                hunk_header, hunk_body = line, []
            else:
                hunk_body.append(line)
        flush()
        hidden[new or old or ""] = info
        if (info.added or info.removed) and not body:
            info.whole_file = True
            continue
        out.extend(chunk[:first_hunk] + body)
    text = "\n".join(out)
    return text + "\n" if ends_with_newline and text else text, hidden
