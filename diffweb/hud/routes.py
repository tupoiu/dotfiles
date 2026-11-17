"""HUD routes: the page shell, the three JSON endpoints it fills itself from, and mutes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import DiffwebConfig
from ..state import State
from . import service

TEMPLATES = Path(__file__).parent / "templates"


def make_router(
    get_config: Callable[[], DiffwebConfig],
    get_state: Callable[[], State],
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    def enabled(config: DiffwebConfig = Depends(get_config)) -> DiffwebConfig:
        if not config.hud_enabled():
            raise HTTPException(404, "the HUD is disabled in config")
        return config

    @router.get("/hud", response_class=HTMLResponse)
    def hud_page(request: Request, config: DiffwebConfig = Depends(enabled)) -> Any:
        # Rows are drawn by the page from /api/hud, so polling and the first
        # paint share one renderer.
        return templates.TemplateResponse(request, "hud.html", {"hud": config.hud})

    @router.get("/api/hud")
    def api_overview(mode: service.Mode = "cached", config: DiffwebConfig = Depends(enabled)) -> Any:
        return service.overview(get_state(), config, mode)

    @router.get("/api/hud/details")
    def api_details(mode: service.Mode = "cached", config: DiffwebConfig = Depends(enabled)) -> Any:
        return service.details(get_state(), config, mode)

    @router.get("/api/hud/checks")
    def api_checks(mode: service.Mode = "cached", config: DiffwebConfig = Depends(enabled)) -> Any:
        return service.checks(get_state(), config, mode)

    @router.post("/api/hud/mute")
    def api_mute(pr: str = Query(min_length=1), seconds: int = Query(ge=0, le=service.MAX_MUTE_SECONDS),
                 config: DiffwebConfig = Depends(enabled)) -> Any:
        return {"muted": service.mute(get_state(), pr, seconds)}

    return router
