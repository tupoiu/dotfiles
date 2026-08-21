"""Thin, argv-only wrappers around git, plus worktree discovery."""

from __future__ import annotations

import concurrent.futures
import glob
import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import DiffwebConfig

# The sentinel end-of-range meaning "whatever is on disk right now".
WORKTREE = "WORKTREE"

# Refs reach us from query strings. Anything outside this alphabet - and
# anything that could be read as an option - never makes it to argv.
_REF_RE = re.compile(r"^[A-Za-z0-9._/~^{}@+-]+$")


class GitError(RuntimeError):
    pass


def run_git(cwd: str | os.PathLike[str], *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()}")
    return proc.stdout


def _try_git(cwd: str | os.PathLike[str], *args: str) -> str | None:
    try:
        return run_git(cwd, *args)
    except GitError:
        return None


def validate_ref(ref: str) -> str:
    if ref.startswith("-") or not _REF_RE.match(ref):
        raise ValueError(f"refusing suspicious ref: {ref!r}")
    return ref


def resolve_ref(path: str | os.PathLike[str], ref: str) -> str:
    """Validate a caller-supplied ref and return the commit sha it names."""
    if ref == WORKTREE:
        return WORKTREE
    validate_ref(ref)
    out = _try_git(path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if not out:
        raise GitError(f"unknown ref {ref!r} in {path}")
    return out.strip()


@dataclass(frozen=True)
class Worktree:
    path: str
    name: str
    branch: str

    @property
    def id(self) -> str:
        return hashlib.sha1(self.path.encode()).hexdigest()[:12]


def _is_git_dir(p: Path) -> bool:
    # Worktrees have a .git *file* pointing at the parent's gitdir.
    return (p / ".git").exists()


def _branch_of(path: str) -> str:
    name = (_try_git(path, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if not name or name == "HEAD":
        sha = (_try_git(path, "rev-parse", "--short", "HEAD") or "").strip()
        return f"detached @ {sha}" if sha else "detached"
    return name


def _linked_worktrees(path: str) -> list[str]:
    out = _try_git(path, "worktree", "list", "--porcelain") or ""
    return [line[len("worktree ") :].strip() for line in out.splitlines() if line.startswith("worktree ")]


def discover(config: DiffwebConfig) -> list[Worktree]:
    """Union of the configured root globs and every worktree git knows about."""
    candidates: list[str] = []
    for root in config.expanded_roots():
        for hit in glob.glob(root):
            p = Path(hit)
            if p.is_dir() and _is_git_dir(p):
                candidates.append(str(p))

    # Each hit can tell us about siblings the globs never reached.
    for base in list(candidates):
        candidates.extend(_linked_worktrees(base))

    paths: list[str] = []
    for cand in candidates:
        real = os.path.realpath(cand)
        if real not in paths and _is_git_dir(Path(real)):
            paths.append(real)

    names = _disambiguate(paths)
    return sorted(
        (Worktree(path=p, name=names[p], branch=_branch_of(p)) for p in paths),
        key=lambda w: w.name,
    )


def _home_relative(path: str) -> str:
    home = os.path.expanduser("~")
    return f"~/{os.path.relpath(path, home)}" if path.startswith(home + os.sep) else path


def _disambiguate(paths: list[str]) -> dict[str, str]:
    """Basenames collide - several worktrees are called `code` - so anything
    ambiguous falls back to its full (home-relative) path."""
    names = {p: Path(p).name for p in paths}
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    return {p: (n if counts[n] == 1 else _home_relative(p)) for p, n in names.items()}


def default_base(path: str) -> str:
    """origin/HEAD if the repo has one, else the first plausible main branch."""
    head = _try_git(path, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if head and head.strip() and head.strip() != "origin/HEAD":
        return head.strip()
    for candidate in ("origin/master", "origin/main", "master", "main"):
        if _try_git(path, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    return "HEAD"


def merge_base(path: str, base: str, head: str = "HEAD") -> str:
    base_sha = resolve_ref(path, base)
    head_sha = resolve_ref(path, head)
    out = _try_git(path, "merge-base", base_sha, head_sha)
    # Unrelated histories: fall back to the base itself so the page still renders.
    return out.strip() if out else base_sha


def commits(path: str, start: str, end: str = "HEAD", limit: int = 500) -> list[dict[str, str]]:
    start_sha = resolve_ref(path, start)
    end_sha = resolve_ref(path, "HEAD" if end == WORKTREE else end)
    fmt = "%H%x1f%h%x1f%an%x1f%ar%x1f%s"
    out = _try_git(path, "log", f"--max-count={limit}", f"--format={fmt}", f"{start_sha}..{end_sha}") or ""
    rows = []
    for line in out.splitlines():
        sha, short, author, when, subject = line.split("\x1f")
        rows.append({"sha": sha, "short": short, "author": author, "when": when, "subject": subject})
    return rows


def _diff_args(start: str, end: str) -> list[str]:
    # Ending at the worktree means a one-sided diff, which git reads as
    # "start vs the files on disk" and so includes staged and unstaged work.
    return [start] if end == WORKTREE else [start, end]


def diff_text(path: str, start: str, end: str, files: list[str] | None = None) -> str:
    args = ["diff", "--no-color", "-M", "-C", *_diff_args(start, end)]
    if files:
        args += ["--", *files]
    return _try_git(path, *args) or ""


def numstat(path: str, start: str, end: str) -> list[dict[str, object]]:
    out = _try_git(path, "diff", "--numstat", "-M", "-C", *_diff_args(start, end)) or ""
    rows: list[dict[str, object]] = []
    for line in out.splitlines():
        added, removed, name = line.split("\t", 2)
        rows.append(
            {
                "path": name,
                "added": None if added == "-" else int(added),
                "removed": None if removed == "-" else int(removed),
                "binary": added == "-",
            }
        )
    return rows


def diff_size_bytes(path: str, start: str, end: str) -> int:
    # Cheaper than materialising the diff when we only need to decide on
    # lazy loading; git still does the work but we do not hold it in memory.
    proc = subprocess.run(
        ["git", "diff", "--no-color", "-M", "-C", *_diff_args(start, end)],
        cwd=path,
        capture_output=True,
    )
    return len(proc.stdout)


def blob_shas(path: str, start: str, end: str) -> dict[str, str]:
    """Post-image blob sha per file, used to tell 'reviewed' from 'changed since'."""
    out = _try_git(path, "diff", "--raw", "-M", "-C", *_diff_args(start, end)) or ""
    shas: dict[str, str] = {}
    for line in out.splitlines():
        meta, _, names = line.partition("\t")
        if not names:
            continue
        name = names.split("\t")[-1]
        parts = meta.split()
        dst = parts[3] if len(parts) > 3 else ""
        if set(dst) <= {"0"}:
            # Uncommitted: git reports zeros, so hash what is on disk.
            dst = (_try_git(path, "hash-object", "--", name) or "").strip() or "worktree"
        shas[name] = dst
    return shas


def structural_diff(path: str, start: str, end: str, difft: str | None = None) -> str:
    """difftastic's side-by-side output, still ANSI-coloured."""
    binary = difft or shutil.which("difft")
    # A configured-but-missing path must read as "not installed", not as a git failure.
    if not binary or not os.access(binary, os.X_OK):
        raise FileNotFoundError(f"difft not usable: {binary or 'not on PATH'}")
    env = {
        "GIT_EXTERNAL_DIFF": binary,
        "DFT_COLOR": "always",
        "DFT_DISPLAY": "side-by-side",
        "DFT_WIDTH": "180",
    }
    return run_git(path, "diff", "--ext-diff", *_diff_args(start, end), env=env)


def ahead_behind(path: str, base: str, head: str = "HEAD") -> tuple[int, int]:
    """Commits on head not in base, and vice versa - the second half tells you
    the branch has fallen behind and the diff may be misleading."""
    base_sha = resolve_ref(path, base)
    head_sha = resolve_ref(path, head)
    out = _try_git(path, "rev-list", "--left-right", "--count", f"{base_sha}...{head_sha}")
    if not out:
        return (0, 0)
    behind, ahead = out.split()
    return (int(ahead), int(behind))


def dirty_count(path: str) -> int:
    """Files with uncommitted changes, staged or not."""
    out = _try_git(path, "status", "--porcelain") or ""
    return sum(1 for line in out.splitlines() if line.strip())


@dataclass
class Summary:
    worktree: Worktree
    base: str
    merge_base: str
    ahead: int
    files: int
    added: int
    removed: int
    last_commit: str
    reviewed: int = 0
    behind: int = 0
    dirty: int = 0
    pr: object | None = None
    error: str | None = None


def summarise(wt: Worktree, base: str) -> Summary:
    try:
        mb = merge_base(wt.path, base)
        stats = numstat(wt.path, mb, WORKTREE)
        ahead, behind = ahead_behind(wt.path, base)
        return Summary(
            worktree=wt,
            base=base,
            merge_base=mb,
            ahead=ahead,
            files=len(stats),
            added=sum(int(r["added"] or 0) for r in stats),
            removed=sum(int(r["removed"] or 0) for r in stats),
            last_commit=(_try_git(wt.path, "log", "-1", "--format=%ar") or "").strip(),
            behind=behind,
            dirty=dirty_count(wt.path),
        )
    except (GitError, ValueError) as exc:
        return Summary(wt, base, "", 0, 0, 0, 0, "", error=str(exc))


def summarise_all(worktrees: list[Worktree], base_for: dict[str, str]) -> list[Summary]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda w: summarise(w, base_for.get(w.id) or default_base(w.path)), worktrees))
