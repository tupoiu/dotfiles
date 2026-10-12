"""The HUD's data, cached in the state DB.

Each request names how fresh it needs the answer to be:

- `cached`: whatever is stored, however old. Only the very first call waits on
  GitHub. This is what the first paint uses.
- `fresh`: ask GitHub again if the stored answer is older than
  REFRESH_AFTER_SECONDS. The page calls this straight after a stale first
  paint, and when it polls.
- `force`: ask GitHub now. The refresh button.

A failed refresh keeps the stored answer and reports the error next to it, so
the page shows old PRs with an honest age instead of nothing.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import date
from typing import Any, Callable, Literal

from .. import gitio
from ..config import DiffwebConfig
from ..forge import REFRESH_AFTER_SECONDS
from ..state import State
from . import github

Mode = Literal["cached", "fresh", "force"]

# One lock per cache key, so a burst of polls asks GitHub once, not once each.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _stored(state: State, key: str, scope: str) -> tuple[Any, float] | None:
    """The stored payload for `key` and when it was fetched.

    `scope` says what question the payload answers (repos, cutoff, PR ids). A
    stored payload for another scope is no payload at all. There is one row per
    key, so old answers do not pile up in the table.
    """
    found = state.cached(key)
    if found is None:
        return None
    wrapped, checked_at = found
    if not isinstance(wrapped, dict) or wrapped.get("scope") != scope:
        return None
    return wrapped["value"], checked_at


def _load(state: State, key: str, scope: str, mode: Mode, fetch: Callable[[], Any]) -> dict[str, Any]:
    """The payload for `key`, refreshed as `mode` asks."""

    def stored() -> tuple[Any, float] | None:
        return _stored(state, key, scope)

    def wanted(record: tuple[Any, float] | None) -> bool:
        if record is None or mode == "force":
            return True
        return mode == "fresh" and time.time() - record[1] > REFRESH_AFTER_SECONDS

    record = stored()
    error = None
    if wanted(record):
        asked_at = time.time()
        with _lock_for(key):
            # Another request may have refreshed it while we waited.
            record = stored()
            if wanted(record) and not (record and record[1] >= asked_at):
                try:
                    payload = fetch()
                except github.GitHubError as exc:
                    error = str(exc)
                else:
                    record = (payload, time.time())
                    state.save_cached(key, {"scope": scope, "value": payload}, record[1])
    if record is None:
        return {"data": None, "checked_at": None, "stale": True, "error": error}
    payload, checked_at = record
    return {
        "data": payload,
        "checked_at": checked_at,
        "stale": time.time() - checked_at > REFRESH_AFTER_SECONDS,
        "error": error,
    }


def _scope(*parts: object) -> str:
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


def _overview_scope(config: DiffwebConfig, today: date) -> tuple[str, date]:
    hud = config.hud
    cutoff = github.workday_cutoff(today, hud.closed_workdays)
    return _scope(sorted(hud.repos), cutoff.isoformat(), hud.limit), cutoff


_GITHUB_REMOTE = re.compile(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$")


def repo_of(url: str | None) -> str | None:
    """owner/name from a GitHub remote URL, ssh or https."""
    if not url:
        return None
    found = _GITHUB_REMOTE.search(url.strip())
    return found.group(1) if found else None


def _local_worktrees(config: DiffwebConfig) -> dict[tuple[str, str], dict[str, str]]:
    """(owner/name lowercased, branch) -> the worktree checked out on it."""
    out = {}
    for wt in gitio.discover(config):
        repo = repo_of(gitio.remote_url(wt.path))
        if repo:
            out.setdefault((repo.lower(), wt.branch), {"id": wt.id, "name": wt.name})
    return out


def overview(state: State, config: DiffwebConfig, mode: Mode = "cached", today: date | None = None) -> dict[str, Any]:
    hud = config.hud
    scope, cutoff = _overview_scope(config, today or date.today())
    result = _load(
        state,
        "hud:overview",
        scope,
        mode,
        lambda: github.overview(hud.repos, cutoff, hud.limit, config.tools.gh_path, hud.timeout_seconds),
    )
    # Worktrees come and go between refreshes, so they are matched on every
    # request rather than stored with the PRs.
    if result["data"]:
        local = _local_worktrees(config)
        for section in result["data"]["sections"].values():
            for pr in section["prs"]:
                pr["worktree"] = local.get((pr["repo"].lower(), pr["branch"]))
    return result


def details(state: State, config: DiffwebConfig, mode: Mode = "cached", today: date | None = None) -> dict[str, Any]:
    """Details for the PRs in the stored overview. Never fetches the overview."""
    scope, _ = _overview_scope(config, today or date.today())
    found = _stored(state, "hud:overview", scope)
    if found is None:
        return {"data": None, "checked_at": None, "stale": True, "error": "no overview yet"}
    data = found[0]
    ids = sorted({pr["id"] for s in data["sections"].values() for pr in s["prs"]})
    viewer = data.get("viewer")
    return _load(
        state,
        "hud:details",
        _scope(ids, viewer),
        mode,
        lambda: github.details(ids, viewer, config.tools.gh_path, config.hud.timeout_seconds),
    )
