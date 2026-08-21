"""Pull request lookup via the `gh` CLI.

Strictly best-effort: gh may be missing, unauthenticated, offline, or looking at
a repo with no PR for this branch. None of that may stop a page rendering, so
every failure here collapses to "no PR known".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

# gh hits the network, so results are cached briefly. A PR's state changes on
# human timescales; re-asking on every page load would just add latency.
_TTL_SECONDS = 120
_cache: dict[str, tuple[float, "PullRequest | None"]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    state: str
    draft: bool

    @property
    def label(self) -> str:
        return f"#{self.number}"

    @property
    def status(self) -> str:
        if self.draft and self.state == "OPEN":
            return "draft"
        return self.state.lower()


def available(gh_path: str | None = None) -> bool:
    return bool(gh_path or shutil.which("gh"))


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _query(path: str, branch: str, gh: str, timeout: float) -> PullRequest | None:
    proc = subprocess.run(
        [gh, "pr", "list", "--head", branch, "--state", "all", "--limit", "1",
         "--json", "number,title,url,state,isDraft"],
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
    )


def pull_request(
    path: str, branch: str, gh_path: str | None = None, timeout: float = 5.0
) -> PullRequest | None:
    """The PR whose head is `branch`, or None for any reason at all."""
    gh = gh_path or shutil.which("gh")
    if not gh or not branch or branch.startswith("detached"):
        return None

    key = f"{path}\x00{branch}"
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]

    try:
        found = _query(path, branch, gh, timeout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError):
        found = None

    with _lock:
        _cache[key] = (time.monotonic(), found)
    return found
