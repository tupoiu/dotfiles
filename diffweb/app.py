"""FastAPI routes for diffweb."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import Any

from ansi2html import Ansi2HTMLConverter
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import gitio
from .config import DiffwebConfig, load_config
from .gitio import WORKTREE, Worktree
from .state import State

HERE = Path(__file__).parent

app = FastAPI(title="diffweb")


@app.exception_handler(gitio.GitError)
@app.exception_handler(ValueError)
def _bad_ref_handler(request: Request, exc: Exception) -> JSONResponse:
    """A ref the user typed is bad input, not a server fault."""
    return JSONResponse({"error": str(exc)}, status_code=400)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=str(HERE / "templates"))

_config: DiffwebConfig | None = None
_state: State | None = None


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


def find_worktree(wt_id: str, config: DiffwebConfig) -> Worktree:
    for wt in gitio.discover(config):
        if wt.id == wt_id:
            return wt
    raise HTTPException(404, f"unknown worktree {wt_id}")


# Resolved diffs are immutable once both ends are commits, so they cache well.
_diff_cache: dict[tuple[str, str, str], str] = {}


def cached_diff(path: str, start: str, end: str) -> str:
    if end == WORKTREE:
        return gitio.diff_text(path, start, end)
    key = (path, start, end)
    if key not in _diff_cache:
        if len(_diff_cache) > 32:
            _diff_cache.clear()
        _diff_cache[key] = gitio.diff_text(path, start, end)
    return _diff_cache[key]


def _range(wt: Worktree, base: str, start: str | None, end: str | None) -> tuple[str, str]:
    resolved_start = gitio.resolve_ref(wt.path, start) if start else gitio.merge_base(wt.path, base)
    resolved_end = gitio.resolve_ref(wt.path, end) if end else WORKTREE
    return resolved_start, resolved_end


@app.get("/", response_class=HTMLResponse)
def index(request: Request, config: DiffwebConfig = Depends(get_config)) -> Any:
    state = get_state()
    worktrees = gitio.discover(config)
    summaries = gitio.summarise_all(worktrees, state.all_base_refs())
    for summary in summaries:
        # Cheap approximation: how many files we have ever ticked off here.
        # The exact per-range figure is computed on the diff page itself.
        summary.reviewed = len(state.reviewed(summary.worktree.id))
    return templates.TemplateResponse(
        request, "index.html", {"summaries": summaries, "features": config.features}
    )


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
    base = base or state.base_ref(wt_id) or gitio.default_base(wt.path)
    return templates.TemplateResponse(
        request,
        "worktree.html",
        {
            "wt": wt,
            "base": base,
            "start": start or "",
            "end": end or "",
            "features": config.features,
            "difft_available": bool(config.tools.difft_path or shutil.which("difft")),
        },
    )


@app.get("/api/w/{wt_id}/commits")
def api_commits(wt_id: str, base: str | None = None, config: DiffwebConfig = Depends(get_config)) -> Any:
    wt = find_worktree(wt_id, config)
    base = base or get_state().base_ref(wt_id) or gitio.default_base(wt.path)
    mb = gitio.merge_base(wt.path, base)
    return {"base": base, "merge_base": mb, "commits": gitio.commits(wt.path, mb, "HEAD")}


@app.get("/api/w/{wt_id}/diff")
def api_diff(
    wt_id: str,
    base: str | None = None,
    start: str | None = None,
    end: str | None = None,
    renderer: str = "line",
    files: list[str] | None = Query(default=None),
    config: DiffwebConfig = Depends(get_config),
) -> Any:
    wt = find_worktree(wt_id, config)
    state = get_state()
    base = base or state.base_ref(wt_id) or gitio.default_base(wt.path)
    s, e = _range(wt, base, start, end)

    stats = gitio.numstat(wt.path, s, e)
    for row in stats:
        row["noisy"] = gitio.is_noisy(str(row["path"]), config.noise.collapse_by_default)
    shas = gitio.blob_shas(wt.path, s, e)
    review = state.review_status(wt_id, shas) if config.features.reviewed_state else {}

    if renderer == "structural":
        if not config.features.structural:
            raise HTTPException(400, "structural diff is disabled in config")
        try:
            ansi = gitio.structural_diff(wt.path, s, e, config.tools.difft_path)
        except FileNotFoundError:
            return JSONResponse(
                {"error": "difft not found. Install it with: cargo binstall difftastic"}, status_code=503
            )
        html = Ansi2HTMLConverter(inline=True, dark_bg=True).convert(ansi, full=False)
        return {"renderer": "structural", "html": html, "files": stats, "review": review, "shas": shas}

    size = gitio.diff_size_bytes(wt.path, s, e) if not files else 0
    lazy = not files and (size > config.limits.max_inline_diff_bytes or len(stats) > config.limits.max_inline_files)
    return {
        "renderer": "line",
        "start": s,
        "end": e,
        "base": base,
        "files": stats,
        "review": review,
        "shas": shas,
        "lazy": lazy,
        "diff": "" if lazy else cached_diff(wt.path, s, e) if not files else gitio.diff_text(wt.path, s, e, files),
    }


@app.post("/api/w/{wt_id}/base")
def api_set_base(wt_id: str, base: str, config: DiffwebConfig = Depends(get_config)) -> Any:
    wt = find_worktree(wt_id, config)
    gitio.resolve_ref(wt.path, base)  # never persist a base we cannot resolve
    get_state().set_base_ref(wt_id, base)
    return {"ok": True, "base": base}


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


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


def main() -> None:
    import uvicorn

    cfg = load_config()
    uvicorn.run("diffweb.app:app", host=cfg.server.host, port=cfg.server.port)
