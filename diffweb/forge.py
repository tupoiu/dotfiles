"""Pull request lookup via the `gh` CLI.

Strictly best-effort: gh may be missing, unauthenticated, offline, or looking at
a repo with no PR for this branch. None of that may stop a page rendering, so
every failure here collapses to "no PR known".

Results are cached in the state DB rather than in memory, so the catalog can say
how long ago it last managed to look - which is the honest thing to show when gh
has been failing, and survives a restart.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime

# How old a stored lookup may get before we refresh it in the background.
REFRESH_AFTER_SECONDS = 120

_GH_FIELDS = "number,title,url,state,isDraft,createdAt"

# Worktrees with a refresh already in flight, so a burst of page loads does not
# start a thread each.
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def relative_age(seconds: float) -> str:
    """A compact age: 45s, 3m, 5h, 2d, 6w, 1y."""
    seconds = max(0.0, seconds)
    for limit, divisor, suffix in (
        (60, 1, "s"),
        (3600, 60, "m"),
        (86400, 3600, "h"),
        (7 * 86400, 86400, "d"),
        (365 * 86400, 7 * 86400, "w"),
    ):
        if seconds < limit:
            return f"{int(seconds // divisor)}{suffix}"
    return f"{int(seconds // (365 * 86400))}y"


def _parse_created(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    state: str
    draft: bool
    created_at: float | None = None

    @property
    def label(self) -> str:
        return f"#{self.number}"

    @property
    def status(self) -> str:
        if self.draft and self.state == "OPEN":
            return "draft"
        return self.state.lower()

    @property
    def opened_age(self) -> str | None:
        """How long ago the PR was raised, e.g. "3d"."""
        if self.created_at is None:
            return None
        return relative_age(time.time() - self.created_at)

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "draft": self.draft,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "PullRequest":
        return cls(
            number=raw["number"],
            title=raw["title"],
            url=raw["url"],
            state=raw["state"],
            draft=bool(raw.get("draft")),
            created_at=raw.get("created_at"),
        )


@dataclass(frozen=True)
class PrStatus:
    """What we know about a worktree's PR, and when we last managed to look."""

    pr: PullRequest | None = None
    checked_at: float | None = None

    @property
    def checked_age(self) -> str | None:
        if self.checked_at is None:
            return None
        return relative_age(time.time() - self.checked_at)


def available(gh_path: str | None = None) -> bool:
    return bool(gh_path or shutil.which("gh"))


def query(path: str, branch: str, gh_path: str | None = None, timeout: float = 5.0) -> PullRequest | None:
    """Ask gh directly. Returns None for "no PR" and for every failure alike."""
    gh = gh_path or shutil.which("gh")
    if not gh or not branch or branch.startswith("detached"):
        return None
    try:
        proc = subprocess.run(
            [gh, "pr", "list", "--head", branch, "--state", "all", "--limit", "1",
             "--json", _GH_FIELDS],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return None
        rows = json.loads(proc.stdout or "[]")
        if not rows:
            return None
        row = rows[0]
        return PullRequest(
            number=row["number"],
            title=row["title"],
            url=row["url"],
            state=row["state"],
            draft=bool(row.get("isDraft")),
            created_at=_parse_created(row.get("createdAt")),
        )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError):
        return None


def _refresh(state, wt_id: str, path: str, branch: str, gh_path: str | None, timeout: float) -> PrStatus:
    pr = query(path, branch, gh_path, timeout)
    # gh failing and "there is genuinely no PR" are indistinguishable from here,
    # so both are recorded: the age is what tells you how fresh the answer is.
    checked_at = time.time()
    state.save_pr(wt_id, branch, pr.as_dict() if pr else None, checked_at)
    return PrStatus(pr, checked_at)


def _refresh_in_background(state, wt_id: str, path: str, branch: str, gh_path, timeout) -> None:
    with _in_flight_lock:
        if wt_id in _in_flight:
            return
        _in_flight.add(wt_id)

    def run() -> None:
        try:
            _refresh(state, wt_id, path, branch, gh_path, timeout)
        finally:
            with _in_flight_lock:
                _in_flight.discard(wt_id)

    threading.Thread(target=run, daemon=True).start()


def status(
    state,
    wt_id: str,
    path: str,
    branch: str,
    gh_path: str | None = None,
    timeout: float = 5.0,
    force: bool = False,
) -> PrStatus:
    """The PR for this branch, from the store, refreshed when stale.

    The first ever lookup blocks; after that the stored answer is returned
    immediately and any refresh happens behind the page.
    """
    if force:
        return _refresh(state, wt_id, path, branch, gh_path, timeout)

    record = state.pr_record(wt_id, branch)
    if record is None:
        return _refresh(state, wt_id, path, branch, gh_path, timeout)

    payload, checked_at = record
    if time.time() - checked_at > REFRESH_AFTER_SECONDS:
        _refresh_in_background(state, wt_id, path, branch, gh_path, timeout)
    return PrStatus(PullRequest.from_dict(payload) if payload else None, checked_at)
