"""SQLite-backed review state and per-worktree preferences."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviewed (
    worktree TEXT NOT NULL,
    path     TEXT NOT NULL,
    blob_sha TEXT NOT NULL,
    seen_at  REAL NOT NULL,
    PRIMARY KEY (worktree, path)
);
CREATE TABLE IF NOT EXISTS settings (
    worktree TEXT PRIMARY KEY,
    base_ref TEXT
);
CREATE TABLE IF NOT EXISTS pr_cache (
    worktree   TEXT PRIMARY KEY,
    branch     TEXT NOT NULL,
    payload    TEXT,          -- JSON for the PR, or NULL meaning "looked, found none"
    checked_at REAL NOT NULL  -- unix time, so the age survives a restart
);
"""


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        # check_same_thread=False: FastAPI serves sync handlers from a pool.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Background PR refreshes write from other threads.
        self._write_lock = threading.Lock()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def base_ref(self, worktree: str) -> str | None:
        row = self._conn.execute("SELECT base_ref FROM settings WHERE worktree = ?", (worktree,)).fetchone()
        return row["base_ref"] if row else None

    def all_base_refs(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT worktree, base_ref FROM settings").fetchall()
        return {r["worktree"]: r["base_ref"] for r in rows if r["base_ref"]}

    def set_base_ref(self, worktree: str, base_ref: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (worktree, base_ref) VALUES (?, ?) "
            "ON CONFLICT(worktree) DO UPDATE SET base_ref = excluded.base_ref",
            (worktree, base_ref),
        )
        self._conn.commit()

    def pr_record(self, worktree: str, branch: str) -> tuple[dict | None, float] | None:
        """The stored PR lookup for this worktree, or None if we have not looked.

        A record for a different branch is no record at all - the worktree has
        been switched since.
        """
        row = self._conn.execute(
            "SELECT branch, payload, checked_at FROM pr_cache WHERE worktree = ?", (worktree,)
        ).fetchone()
        if row is None or row["branch"] != branch:
            return None
        return (json.loads(row["payload"]) if row["payload"] else None, row["checked_at"])

    def save_pr(self, worktree: str, branch: str, payload: dict | None, checked_at: float) -> None:
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO pr_cache (worktree, branch, payload, checked_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(worktree) DO UPDATE SET branch = excluded.branch, "
                "payload = excluded.payload, checked_at = excluded.checked_at",
                (worktree, branch, json.dumps(payload) if payload is not None else None, checked_at),
            )
            self._conn.commit()

    def reviewed(self, worktree: str) -> dict[str, str]:
        rows = self._conn.execute("SELECT path, blob_sha FROM reviewed WHERE worktree = ?", (worktree,)).fetchall()
        return {r["path"]: r["blob_sha"] for r in rows}

    def mark_reviewed(self, worktree: str, path: str, blob_sha: str) -> None:
        self._conn.execute(
            "INSERT INTO reviewed (worktree, path, blob_sha, seen_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(worktree, path) DO UPDATE SET blob_sha = excluded.blob_sha, seen_at = excluded.seen_at",
            (worktree, path, blob_sha, time.time()),
        )
        self._conn.commit()

    def unmark_reviewed(self, worktree: str, path: str) -> None:
        self._conn.execute("DELETE FROM reviewed WHERE worktree = ? AND path = ?", (worktree, path))
        self._conn.commit()

    def review_status(self, worktree: str, current: dict[str, str]) -> dict[str, str]:
        """Per-file: 'reviewed' (sha matches), 'changed' (moved on), or 'new'."""
        seen = self.reviewed(worktree)
        out = {}
        for path, sha in current.items():
            if path not in seen:
                out[path] = "new"
            elif seen[path] == sha:
                out[path] = "reviewed"
            else:
                out[path] = "changed"
        return out
