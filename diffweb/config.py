"""Configuration: a YAML file deserialised into pydantic models.

Everything the app looks at on disk is derived from here - notably `roots`,
so tests can point the whole app at a temp directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ENV_VAR = "DIFFWEB_CONFIG"
DEFAULT_CONFIG_PATH = "~/.config/diffweb.yaml"


class Server(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class Features(BaseModel):
    # Off by default: needs the `difft` binary, which uv sync cannot provide.
    structural: bool = False
    reviewed_state: bool = True
    live_reload: bool = True


class Limits(BaseModel):
    """Above these, the diff page lists files and loads each one lazily."""

    max_inline_diff_bytes: int = 1_500_000
    max_inline_files: int = 300


class Tools(BaseModel):
    difft_path: str | None = None


class DiffwebConfig(BaseModel):
    model_config = {"extra": "forbid"}

    roots: list[str] = Field(default_factory=lambda: ["~/code", "~/code.*", "~/worktrees/*/*"])
    server: Server = Field(default_factory=Server)
    features: Features = Field(default_factory=Features)
    limits: Limits = Field(default_factory=Limits)
    tools: Tools = Field(default_factory=Tools)
    state_db: str = "~/.local/state/diffweb/state.db"

    def expanded_roots(self) -> list[str]:
        return [os.path.expanduser(r) for r in self.roots]

    def state_db_path(self) -> Path:
        return Path(os.path.expanduser(self.state_db))


def config_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.path.expanduser(os.environ.get(ENV_VAR) or DEFAULT_CONFIG_PATH))


def load_config(path: str | os.PathLike[str] | None = None) -> DiffwebConfig:
    """Load config, falling back to defaults when the file is absent.

    A malformed file is a startup error rather than something we paper over.
    """
    resolved = config_path(path)
    if not resolved.exists():
        return DiffwebConfig()
    raw = yaml.safe_load(resolved.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{resolved}: expected a YAML mapping, got {type(raw).__name__}")
    return DiffwebConfig.model_validate(raw)
