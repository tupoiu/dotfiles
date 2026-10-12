"""Stage timings for the diff-to-HTML path, appended to a JSONL file.

The question this answers is "what is the user actually waiting for", so a
profile is only complete once the page reports its own fetch and render times
back: the server cannot see either, and on a heavy branch they are not small.
The server therefore parks its half in `pending` and writes the record when the
client posts the other half.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Stages in the order they happen, which is also the order the bar is drawn in.
SERVER_STAGES = ("refs", "numstat", "shas", "diff", "serialise")


class Timer:
    """Accumulates named stage durations in milliseconds.

    Disabled timers do no work beyond a branch, so `features.profiling: false`
    costs nothing at all.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - started) * 1000

    @property
    def total_ms(self) -> float:
        return sum(self.stages.values())


class Pending:
    """Server halves waiting for their client half, newest last and bounded.

    A page that is closed mid-load simply never posts; its entry ages out
    rather than leaking.
    """

    def __init__(self, limit: int = 64):
        self.limit = limit
        self._items: OrderedDict[str, dict] = OrderedDict()

    def add(self, key: str, record: dict) -> None:
        self._items[key] = record
        while len(self._items) > self.limit:
            self._items.popitem(last=False)

    @staticmethod
    def new_key() -> str:
        return uuid.uuid4().hex

    def take(self, key: str) -> dict | None:
        return self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()


def append(path: Path, record: dict, keep: int = 500) -> None:
    """Append one profile, trimming the file back to the most recent `keep`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    lines = path.read_text().splitlines()
    if len(lines) > keep:
        path.write_text("\n".join(lines[-keep:]) + "\n")


def read(path: Path, limit: int = 200) -> list[dict]:
    """Most recent profiles first. A half-written or hand-edited line is skipped."""
    if not path.exists():
        return []
    out = []
    for line in reversed(path.read_text().splitlines()):
        if len(out) >= limit:
            break
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile - with a handful of samples, interpolation is noise."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def aggregates(records: list[dict]) -> dict[str, float | int]:
    totals = [float(r.get("total_ms") or 0) for r in records]
    return {
        "count": len(records),
        "median_ms": quantile(totals, 0.5),
        "p95_ms": quantile(totals, 0.95),
    }


def segments(record: dict) -> list[dict]:
    """The stage split as percentages, for the inline bar on the profiles page.

    `network` is whatever the client waited for that the server did not spend
    working - transfer plus queueing - so the bar adds up to the real wait.
    """
    total = float(record.get("total_ms") or 0)
    if total <= 0:
        return []
    stages = record.get("stages") or {}
    parts = [(name, float(stages.get(name) or 0)) for name in SERVER_STAGES]
    server = sum(ms for _, ms in parts)
    fetch = float(record.get("fetch_ms") or 0)
    parts.append(("network", max(0.0, fetch - server)))
    parts.append(("render", float(record.get("render_ms") or 0)))
    return [
        {"name": name, "ms": ms, "pct": 100 * ms / total} for name, ms in parts if ms > 0
    ]
