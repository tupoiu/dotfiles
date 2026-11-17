"""Which project each changed file belongs to, and what kind of change it is,
for the flame map on the diff page.

A project is the nearest folder, walking up from the file, that holds a
`pyproject.toml`, `package.json` or `Cargo.toml` on the end side of the range.
A file under none belongs to the repo root, spelled "". Kinds are `test` (the
files `tests.paths` matches - the "hide tests" rule), `docs` (prose and
lockfiles) and `code`, which is everything else.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

from .gitio import INDEX, WORKTREE, resolve_ref

MANIFESTS = ("pyproject.toml", "package.json", "Cargo.toml")

DOC_SUFFIXES = {".md", ".rst", ".txt"}
LOCKFILES = {
    "Cargo.lock", "uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lock", "bun.lockb", "go.sum", "flake.lock", "Gemfile.lock",
}


def kind_of(path: str, tests: set[str]) -> str:
    """`tests` is the set of paths the range's test pathspec matched."""
    if path in tests:
        return "test"
    p = PurePosixPath(path)
    if p.suffix.lower() in DOC_SUFFIXES or p.name in LOCKFILES or p.suffix == ".lock":
        return "docs"
    return "code"


def _ancestors(path: str) -> Iterable[str]:
    """`a/b/c.py` -> `a/b`, `a`, and "" for the root; nearest first."""
    parent = PurePosixPath(path).parent
    while str(parent) != ".":
        yield str(parent)
        parent = parent.parent
    yield ""


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _existing(path: str, rev: str, candidates: Sequence[str]) -> set[str]:
    """Which of `candidates` (repo-relative file paths) exist at `rev`."""
    if rev == WORKTREE:
        root = Path(path)
        return {c for c in candidates if (root / c).is_file()}
    prefix = ":" if rev == INDEX else f"{resolve_ref(path, rev)}:"
    asked = [c for c in candidates if "\n" not in c]
    # One `cat-file --batch-check` answers them all: "<name> missing" or
    # "<sha> <type> <size>", one line per question, in order.
    proc = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=path,
        input="".join(f"{prefix}{c}\n" for c in asked).encode(),
        capture_output=True,
    )
    answers = proc.stdout.decode(errors="replace").splitlines()
    return {c for c, a in zip(asked, answers) if a.split()[1:2] == ["blob"]}


def project_of(path: str, rev: str, files: Iterable[str]) -> dict[str, str]:
    """Each file's project folder at `rev` ("" for the repo root)."""
    files = list(files)
    folders = {folder for f in files for folder in _ancestors(f) if folder}
    candidates = sorted(_join(folder, m) for folder in folders for m in MANIFESTS)
    found = _existing(path, rev, candidates) if candidates else set()
    projects = {os.path.dirname(c) for c in found}
    return {f: next((d for d in _ancestors(f) if d in projects), "") for f in files}
