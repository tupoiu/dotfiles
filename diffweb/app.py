"""FastAPI routes for diffweb."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import forge, gitio, profiling, rust_tests, skeleton
from .config import DiffwebConfig, load_config
from .gitio import INDEX, UPSTREAM, WORKTREE, Worktree
from .hud import routes as hud_routes
from .state import State

HERE = Path(__file__).parent

app = FastAPI(title="diffweb")


@app.exception_handler(gitio.GitError)
@app.exception_handler(ValueError)
def _bad_ref_handler(request: Request, exc: Exception) -> JSONResponse:
    """A ref the user typed is bad input, not a server fault."""
    return JSONResponse({"error": str(exc)}, status_code=400)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=[HERE / "templates", hud_routes.TEMPLATES])


def asset(path: str) -> str:
    """A /static URL stamped with the file's mtime.

    Without this the browser keeps serving a stale `worktree.js` after an edit:
    StaticFiles sends no Cache-Control, so the browser is free to guess a
    freshness lifetime and never revalidate. Editing a file changes the URL.
    """
    try:
        stamp = int((HERE / "static" / path).stat().st_mtime)
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={stamp}"


templates.env.globals["asset"] = asset
# The shared header links to the HUD on every page when it is on.
templates.env.globals["hud_enabled"] = lambda: get_config().hud_enabled()

_config: DiffwebConfig | None = None
_state: State | None = None
_pending_profiles = profiling.Pending()


def get_config() -> DiffwebConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_state() -> State:
    global _state
    if _state is None:
        _state = State(get_config().state_db_path())
    return _state


def reset_for_tests() -> None:
    """Drop cached config/state so a test can swap DIFFWEB_CONFIG."""
    global _config, _state
    _config = _state = None
    _diff_cache.clear()
    _pending_profiles.clear()
    gitio.clear_caches()


app.include_router(hud_routes.make_router(get_config, get_state, templates))


def find_worktree(wt_id: str, config: DiffwebConfig) -> Worktree:
    for wt in gitio.discover(config):
        if wt.id == wt_id:
            return wt
    raise HTTPException(404, f"unknown worktree {wt_id}")


# Resolved diffs are immutable once both ends are commits, so they cache well.
_diff_cache: dict[tuple[str, str, str, bool, tuple[str, ...]], str] = {}


def hide_rust_tests(path: str, start: str, end: str, diff: str) -> tuple[str, dict[str, rust_tests.Hidden]]:
    """`diff` without the test region of each `.rs` file (see rust_tests)."""

    def read(side: str, file: str) -> str | None:
        try:
            data = gitio.blob_bytes(path, start if side == "old" else end, file)
        except ValueError:
            return None
        return data.decode("utf-8", "replace") if data is not None else None

    return rust_tests.hide(diff, read)


_TEST_KINDS = {
    ".rs": "Rust", ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".go": "Go",
}


def tests_summary(
    test_rows: list[dict[str, object]], in_file: dict[str, rust_tests.Hidden], hidden: bool
) -> dict[str, object]:
    """One diffstat for every test line in the range, and a breakdown by kind.

    `test_rows` are the numstat rows of the test files; `in_file` the Rust
    tests inside the other files. The page shows the total, and the
    breakdown when you hover it.
    """
    kinds: dict[str, dict[str, int]] = {}

    def add(kind: str, added: int, removed: int) -> None:
        k = kinds.setdefault(kind, {"files": 0, "added": 0, "removed": 0})
        k["files"] += 1
        k["added"] += added
        k["removed"] += removed

    for row in test_rows:
        path = gitio.rename_target(str(row["path"]))
        ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        add(_TEST_KINDS.get(ext, "Other"), int(row["added"] or 0), int(row["removed"] or 0))
    for info in in_file.values():
        add("Rust, inside files", info.added, info.removed)
    order = sorted(kinds.items(), key=lambda kv: -(kv[1]["added"] + kv[1]["removed"]))
    return {
        "hidden": hidden,
        "files": sum(k["files"] for k in kinds.values()),
        "added": sum(k["added"] for k in kinds.values()),
        "removed": sum(k["removed"] for k in kinds.values()),
        "kinds": [{"kind": name, **k} for name, k in order],
    }


def tests_from(rust: dict[str, rust_tests.Hidden]) -> dict[str, int]:
    """Where each file's tests start, so the page's expanders stop before them."""
    return {path: info.new_start for path, info in rust.items() if info.new_start}


def cached_diff(
    path: str, start: str, end: str, max_cacheable: int, remerge: bool = False, spec: list[str] | None = None
) -> str:
    """Diff text, memoised while both ends name commits.

    Oversized diffs are computed but not kept: those are the ones the page
    loads file-by-file anyway, so caching them would pin megabytes per range.
    """
    if end in (WORKTREE, INDEX):
        # Both ends move under our feet, so neither is safe to memoise.
        return gitio.diff_text(path, start, end, spec=spec or ())
    key = (path, start, end, remerge, tuple(spec or ()))
    if key in _diff_cache:
        return _diff_cache[key]
    text = (
        gitio.remerge_diff_text(path, end, spec=spec or ())
        if remerge
        else gitio.diff_text(path, start, end, spec=spec or ())
    )
    if len(text) <= max_cacheable:
        if len(_diff_cache) > 32:
            _diff_cache.clear()
        _diff_cache[key] = text
    return text


def _is_single_merge(path: str, start: str, end: str) -> bool:
    """True when the range is one merge commit: its first parent up to itself."""
    ps = gitio.parents(path, end)
    return len(ps) > 1 and ps[0] == start


def _range(wt: Worktree, base: str, start: str | None, end: str | None) -> tuple[str, str]:
    # Only the end of a range may be the worktree or the index.
    if start in (WORKTREE, INDEX):
        raise ValueError(f"{start} is not a valid start of a range")
    try:
        resolved_start = gitio.resolve_ref(wt.path, start) if start else gitio.merge_base(wt.path, base)
    except gitio.GitError:
        # An unset upstream is an ordinary state of a local branch, not a typo.
        if start == UPSTREAM:
            raise ValueError(f"{wt.branch} has no upstream branch, so nothing is pushed") from None
        raise
    resolved_end = gitio.resolve_ref(wt.path, end) if end else WORKTREE
    return resolved_start, resolved_end


def _attach_pull_requests(summaries: list[gitio.Summary], config: DiffwebConfig) -> None:
    """Look PRs up in parallel; a first, uncached lookup is slow enough to feel."""
    state = get_state()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(
                forge.status,
                state,
                s.worktree.id,
                s.worktree.path,
                s.worktree.branch,
                config.tools.gh_path,
                config.tools.gh_timeout_seconds,
            ): s
            for s in summaries
        }
        for future, summary in futures.items():
            summary.pr = future.result()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, config: DiffwebConfig = Depends(get_config)) -> Any:
    state = get_state()
    timer = profiling.Timer(enabled=config.features.profiling)
    with timer.stage("discover"):
        worktrees = gitio.discover(config)
    with timer.stage("summarise"):
        summaries = gitio.summarise_all(worktrees, state.all_base_refs())
    with timer.stage("prs"):
        if config.features.pr_links:
            _attach_pull_requests(summaries, config)
    with timer.stage("reviewed"):
        for summary in summaries:
            # Cheap approximation: how many files we have ever ticked off here.
            # The exact per-range figure is computed on the diff page itself.
            summary.reviewed = len(state.reviewed(summary.worktree.id))
    with timer.stage("template"):
        response = templates.TemplateResponse(
            request, "index.html", {"summaries": summaries, "features": config.features}
        )
    if timer.enabled:
        # Server-rendered, so the server's time is nearly all of the wait and
        # the record is complete without hearing back from the page.
        slowest = sorted(summaries, key=lambda s: s.elapsed_ms, reverse=True)[:3]
        profiling.append(
            config.catalog_profile_log_path(),
            {
                "kind": "catalog",
                "at": time.time(),
                "worktrees": len(summaries),
                "stages": {k: round(v, 2) for k, v in timer.stages.items()},
                "server_ms": round(timer.total_ms, 2),
                "total_ms": round(timer.total_ms, 2),
                "slowest": [{"worktree": s.worktree.name, "ms": round(s.elapsed_ms, 2)} for s in slowest],
            },
            config.profile_keep,
        )
    return response


@app.get("/w/{wt_id}", response_class=HTMLResponse)
def worktree_page(
    request: Request,
    wt_id: str,
    base: str | None = None,
    start: str | None = None,
    end: str | None = None,
    config: DiffwebConfig = Depends(get_config),
) -> Any:
    wt = find_worktree(wt_id, config)
    state = get_state()
    candidates = gitio.base_candidates(wt.path)
    base = base or state.base_ref(wt_id) or (candidates[0].ref if candidates else gitio.default_base(wt.path))
    return templates.TemplateResponse(
        request,
        "worktree.html",
        {
            "wt": wt,
            "base": base,
            "base_candidates": candidates,
            "base_listed": any(c.ref == base for c in candidates),
            "start": start or "",
            "end": end or "",
            "features": config.features,
            "difft_available": bool(config.tools.difft_path or shutil.which("difft")),
            "has_test_paths": bool(config.tests.paths),
            "max_highlight_bytes": config.limits.max_highlight_bytes,
            "pr": forge.status(
                get_state(),
                wt.id,
                wt.path,
                wt.branch,
                config.tools.gh_path,
                config.tools.gh_timeout_seconds,
            )
            if config.features.pr_links
            else None,
        },
    )


@app.get("/api/w/{wt_id}/commits")
def api_commits(wt_id: str, base: str | None = None, config: DiffwebConfig = Depends(get_config)) -> Any:
    wt = find_worktree(wt_id, config)
    base = base or get_state().base_ref(wt_id) or gitio.auto_base(wt.path)
    mb = gitio.merge_base(wt.path, base)
    return {"base": base, "merge_base": mb, "commits": gitio.commits(wt.path, mb, "HEAD")}


@app.get("/api/w/{wt_id}/diff")
def api_diff(
    wt_id: str,
    base: str | None = None,
    start: str | None = None,
    end: str | None = None,
    renderer: str = "line",
    remerge: bool | None = None,
    tests: bool = True,
    files: list[str] | None = Query(default=None),
    config: DiffwebConfig = Depends(get_config),
) -> Any:
    wt = find_worktree(wt_id, config)
    state = get_state()
    # Per-file lazy loads are follow-up requests, not the wait we are measuring.
    timer = profiling.Timer(enabled=config.features.profiling and not files)

    with timer.stage("refs"):
        base = base or state.base_ref(wt_id) or gitio.auto_base(wt.path)
        s, e = _range(wt, base, start, end)
        # Exactly one merge commit (first parent -> merge) has two readings: the
        # first-parent diff, which is mostly the other branch, and the remerge
        # diff, which is only what the merger resolved by hand. The latter is the
        # default; `remerge=false` asks for the former. Anything else is a range.
        merge = e not in (WORKTREE, INDEX) and _is_single_merge(wt.path, s, e)
        remerge = merge and remerge is not False and renderer != "structural"

    # `tests=false` drops test files from every view of the range. git does the
    # filtering, so the counts, review state and patch cannot disagree.
    test_patterns = config.tests.paths
    patterns = test_patterns if not tests else []
    spec = gitio.test_pathspecs(patterns, exclude=True)

    if files and renderer == "line":
        # A lazy load of some files. The page has the range's stats, shas and
        # review state from its first request and reads only `diff` here, so
        # none of that whole-range work is done again for each file.
        diff = gitio.remerge_diff_text(wt.path, e, files, spec) if remerge else gitio.diff_text(wt.path, s, e, files, spec)
        rust: dict[str, rust_tests.Hidden] = {}
        if patterns and not remerge:
            diff, rust = hide_rust_tests(wt.path, s, e, diff)
        return {
            "renderer": renderer, "start": s, "end": e, "base": base, "merge": merge, "remerge": remerge,
            "files": [], "tests": None, "review": {}, "shas": {}, "lazy": False, "diff": diff,
            "tests_from": tests_from(rust),
        }

    def range_numstat(pathspec: list[str]) -> list[dict[str, object]]:
        if remerge:
            return gitio.remerge_numstat(wt.path, e, pathspec)
        return gitio.numstat(wt.path, s, e, pathspec)

    with timer.stage("numstat"):
        stats = range_numstat(spec)
        # The test files, hidden or not, so the page can say how much of the
        # range is tests (and, when they are hidden, what was left out).
        test_rows = range_numstat(gitio.test_pathspecs(test_patterns, exclude=False)) if test_patterns else None
        for row in stats:
            row["noisy"] = gitio.is_noisy(str(row["path"]), config.noise.collapse_by_default)
            row["emphasis"] = gitio.emphasis_for(gitio.rename_target(str(row["path"])), config.emphasis)

    with timer.stage("shas"):
        changes = (
            gitio.remerge_raw_changes(wt.path, e, spec) if remerge else gitio.raw_changes(wt.path, s, e, spec)
        )
        shas = {name: c["sha"] for name, c in changes.items()}
        review = state.review_status(wt_id, shas) if config.features.reviewed_state else {}
        # The page fetches an image's two sides by path, so a rename needs to
        # know what the file was called before.
        for row in stats:
            change = changes.get(gitio.rename_target(str(row["path"])), {})
            row["status"] = change.get("status", "")
            row["old_path"] = change.get("old_path", "")

    rust: dict[str, rust_tests.Hidden] = {}
    in_file: dict[str, rust_tests.Hidden] = {}  # Rust tests inside the other files
    if renderer == "structural":
        if not config.features.structural:
            raise HTTPException(400, "structural diff is disabled in config")
        # difftastic's verdict, spliced into git's patch as ordinary unified
        # diff text, so the page draws it exactly like a line diff.
        with timer.stage("diff"):
            try:
                diff = gitio.structural_diff(wt.path, s, e, config.tools.difft_path, files, spec)
            except FileNotFoundError:
                return JSONResponse(
                    {"error": "difft not found. Install it with: cargo binstall difftastic"},
                    status_code=503,
                )
        lazy = False
    else:
        with timer.stage("diff"):
            # One `git diff`. Sizing it with a second, identical `git diff` whose
            # output was thrown away doubled the most expensive stage of the
            # request, and saved no memory either - the subprocess buffers the
            # whole output regardless.
            diff = cached_diff(wt.path, s, e, config.limits.max_inline_diff_bytes, remerge, spec)
            if patterns and not remerge:
                # Rust keeps unit tests in the file they test: cut those out too,
                # and take them off the counts, so the page agrees with the patch.
                diff, rust = hide_rust_tests(wt.path, s, e, diff)
                gone = {path for path, info in rust.items() if info.whole_file}
                for row in stats:
                    info = rust.get(gitio.rename_target(str(row["path"])))
                    if info and row["added"] is not None:
                        row["added"] = int(row["added"]) - info.added
                        row["removed"] = int(row["removed"] or 0) - info.removed
                stats = [row for row in stats if gitio.rename_target(str(row["path"])) not in gone]
                shas = {k: v for k, v in shas.items() if k not in gone}
                review = {k: v for k, v in review.items() if k not in gone}
                in_file = {p: i for p, i in rust.items() if i.added or i.removed}
            elif test_rows is not None and not remerge:
                # Shown: count them, but leave the patch and the expanders alone.
                # A test file's own tests are already counted as a test file.
                test_paths = {gitio.rename_target(str(r["path"])) for r in test_rows}
                _, counted = hide_rust_tests(wt.path, s, e, diff)
                in_file = {p: i for p, i in counted.items() if (i.added or i.removed) and p not in test_paths}
            lazy = (
                len(diff.encode()) > config.limits.max_inline_diff_bytes
                or len(stats) > config.limits.max_inline_files
            )
            if lazy:
                diff = ""

    payload = {
        "renderer": renderer,
        "start": s,
        "end": e,
        "base": base,
        "merge": merge,
        "remerge": remerge,
        "files": stats,
        "tests": tests_summary(test_rows, in_file, hidden=bool(patterns)) if test_rows is not None else None,
        "review": review,
        "shas": shas,
        "lazy": lazy,
        "diff": diff,
        "tests_from": tests_from(rust),
    }
    if not timer.enabled:
        return payload

    # Serialising is a real cost centre on a big diff, so hand back an already
    # encoded body rather than letting FastAPI re-encode it unmeasured.
    key = profiling.Pending.new_key()
    payload["profile_id"] = key
    with timer.stage("serialise"):
        body = json.dumps(payload).encode()
    _pending_profiles.add(
        key,
        {
            "worktree": wt.name,
            "branch": wt.branch,
            "files_count": len(stats),
            "diff_bytes": len(diff.encode()),
            "response_bytes": len(body),
            "stages": {k: round(v, 2) for k, v in timer.stages.items()},
            "server_ms": round(timer.total_ms, 2),
        },
    )
    return Response(content=body, media_type="application/json")


@app.post("/api/profile")
async def api_profile(request: Request, config: DiffwebConfig = Depends(get_config)) -> Any:
    """The page reporting the half of the wait only it can see."""
    if not config.features.profiling:
        raise HTTPException(404, "profiling is disabled in config")
    sent = await request.json()
    record = _pending_profiles.take(str(sent.get("profile_id", "")))
    if record is None:
        # Stale id: the server was restarted, or the entry aged out.
        return {"ok": False}
    fetch_ms = float(sent.get("fetch_ms") or 0)
    render_ms = float(sent.get("render_ms") or 0)
    record |= {
        "at": time.time(),
        "fetch_ms": round(fetch_ms, 2),
        "render_ms": round(render_ms, 2),
        "total_ms": round(fetch_ms + render_ms, 2),
    }
    profiling.append(config.profile_log_path(), record, config.profile_keep)
    return {"ok": True}


@app.get("/profiles", response_class=HTMLResponse)
def profiles_page(request: Request, config: DiffwebConfig = Depends(get_config)) -> Any:
    if not config.features.profiling:
        raise HTTPException(404, "profiling is disabled in config")
    diffs = profiling.read(config.profile_log_path())
    catalogs = profiling.read(config.catalog_profile_log_path())
    return templates.TemplateResponse(
        request,
        "profiles.html",
        {
            "records": [r | {"segments": profiling.segments(r)} for r in diffs],
            "aggregates": profiling.aggregates(diffs),
            "catalogs": [r | {"segments": profiling.segments(r)} for r in catalogs],
            "catalog_aggregates": profiling.aggregates(catalogs),
            "log_path": str(config.profile_log_path()),
        },
    )


@app.post("/api/w/{wt_id}/pr/refresh")
def api_refresh_pr(wt_id: str, config: DiffwebConfig = Depends(get_config)) -> Any:
    """Re-ask gh now. Backs the refresh control on each chip."""
    if not config.features.pr_links:
        raise HTTPException(404, "pr links are disabled in config")
    wt = find_worktree(wt_id, config)
    found = forge.status(
        get_state(),
        wt.id,
        wt.path,
        wt.branch,
        config.tools.gh_path,
        config.tools.gh_timeout_seconds,
        force=True,
    )
    return {
        "pr": found.pr.as_dict() | {"label": found.pr.label, "status": found.pr.status,
                                    "opened_age": found.pr.opened_age}
        if found.pr
        else None,
        "checked_age": found.checked_age,
    }


@app.post("/api/w/{wt_id}/base")
def api_set_base(wt_id: str, base: str, config: DiffwebConfig = Depends(get_config)) -> Any:
    wt = find_worktree(wt_id, config)
    gitio.resolve_ref(wt.path, base)  # never persist a base we cannot resolve
    get_state().set_base_ref(wt_id, base)
    return {"ok": True, "base": base}


@app.get("/api/w/{wt_id}/blob")
def api_blob(wt_id: str, rev: str, path: str, config: DiffwebConfig = Depends(get_config)) -> Any:
    """One side of an image diff: `path` as it is at `rev`.

    `rev` is a commit, the index or the worktree - the same ends a range has.
    Only images are served, so this is not a generic file-read endpoint.
    """
    wt = find_worktree(wt_id, config)
    kind, _ = mimetypes.guess_type(path)
    if not kind or not kind.startswith("image/"):
        raise HTTPException(415, f"{path} is not an image")
    body = gitio.blob_bytes(wt.path, rev, path)
    if body is None:
        raise HTTPException(404, f"{path} does not exist at {rev}")
    # Only a full sha names something that can never change; HEAD, the index
    # and the worktree all move.
    pinned = len(rev) == 40 and all(c in "0123456789abcdef" for c in rev)
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable" if pinned else "no-store",
        "X-Content-Type-Options": "nosniff",
        # An SVG is a document: sandbox it so nothing in it can run.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }
    return Response(content=body, media_type=kind, headers=headers)


@app.get("/api/w/{wt_id}/skeleton")
def api_skeleton(
    wt_id: str,
    base: str | None = None,
    start: str | None = None,
    end: str | None = None,
    tests: bool = True,
    config: DiffwebConfig = Depends(get_config),
) -> Any:
    """The public names the range adds, per file, with signature and docstring
    (see skeleton.py). Takes the same range as /diff; `tests=false` leaves the
    test files out, as it does there."""
    wt = find_worktree(wt_id, config)
    base = base or get_state().base_ref(wt_id) or gitio.auto_base(wt.path)
    s, e = _range(wt, base, start, end)
    spec = gitio.test_pathspecs(config.tests.paths, exclude=True) if not tests else []
    changes = {
        path: c
        for path, c in gitio.raw_changes(wt.path, s, e, spec).items()
        if c["status"] != "D" and skeleton.supported(path)
    }
    paths = list(changes)
    olds = {p: (c["old_path"] or p) for p, c in changes.items()}
    after = gitio.blobs_text(wt.path, e, paths)
    before = gitio.blobs_text(wt.path, s, sorted({o for p, o in olds.items() if changes[p]["status"] != "A"}))
    found = [
        skeleton.added_names(path, before.get(olds[path]), text)
        for path in paths
        if (text := after.get(path)) is not None
    ]
    found = [f for f in found if f.names or f.error]
    names = [n for f in found for n in f.names]
    return {
        "start": s, "end": e, "base": base,
        "files": [f.as_json() for f in found],
        "names": len(names),
        "documented": sum(1 for n in names if n.doc),
    }


@app.get("/api/w/{wt_id}/text", response_class=PlainTextResponse)
def api_text(wt_id: str, rev: str, path: str, config: DiffwebConfig = Depends(get_config)) -> Any:
    """The whole text of `path` at `rev`, for highlighting and the expanders.

    `rev` is a commit, the index or the worktree. Any text file git can read
    there is served: the server only listens on localhost and never writes.
    """
    wt = find_worktree(wt_id, config)
    body = gitio.blob_bytes(wt.path, rev, path)
    if body is None:
        raise HTTPException(404, f"{path} does not exist at {rev}")
    # git's own test for binary content.
    if b"\0" in body[:8000]:
        raise HTTPException(415, f"{path} is binary")
    pinned = len(rev) == 40 and all(c in "0123456789abcdef" for c in rev)
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable" if pinned else "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=body.decode("utf-8", errors="replace"), media_type="text/plain; charset=utf-8", headers=headers)


@app.post("/api/w/{wt_id}/reviewed")
def api_reviewed(wt_id: str, path: str, blob_sha: str, reviewed: bool = True,
                 config: DiffwebConfig = Depends(get_config)) -> Any:
    find_worktree(wt_id, config)
    state = get_state()
    if reviewed:
        state.mark_reviewed(wt_id, path, blob_sha)
    else:
        state.unmark_reviewed(wt_id, path)
    return {"ok": True}


def _worktree_fingerprint(path: str) -> str:
    head = gitio._try_git(path, "rev-parse", "HEAD") or ""
    status = gitio._try_git(path, "status", "--porcelain=v2") or ""
    return hashlib.sha1((head + status).encode()).hexdigest()


@app.get("/events/{wt_id}")
async def events(wt_id: str, config: DiffwebConfig = Depends(get_config)) -> Any:
    if not config.features.live_reload:
        raise HTTPException(404, "live reload is disabled in config")
    wt = find_worktree(wt_id, config)

    async def stream():
        last = await asyncio.to_thread(_worktree_fingerprint, wt.path)
        while True:
            await asyncio.sleep(2)
            current = await asyncio.to_thread(_worktree_fingerprint, wt.path)
            if current != last:
                last = current
                yield "event: changed\ndata: {}\n\n"
            else:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Any:
    return RedirectResponse("/static/favicon.svg", status_code=301)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


def main() -> None:
    import uvicorn

    cfg = load_config()
    uvicorn.run("diffweb.app:app", host=cfg.server.host, port=cfg.server.port)
