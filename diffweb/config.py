"""Configuration: a YAML file deserialised into pydantic models.

Everything the app looks at on disk is derived from here - notably `roots`,
so tests can point the whole app at a temp directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

ENV_VAR = "DIFFWEB_CONFIG"
# Set by the command line (`diffweb --port/--root`) and read here, so the
# uvicorn worker, which loads the config again, sees them too.
PORT_ENV_VAR = "DIFFWEB_PORT"
ROOTS_ENV_VAR = "DIFFWEB_ROOTS"  # globs joined with os.pathsep
DEFAULT_CONFIG_PATH = "~/.config/diffweb.yaml"


class Server(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class Features(BaseModel):
    # Off by default: needs the `difft` binary, which uv sync cannot provide.
    structural: bool = False
    reviewed_state: bool = True
    live_reload: bool = True
    # Shells out to `gh`, which hits the network; harmless but opt-out-able.
    pr_links: bool = True
    # Records stage timings for the diff path; off means no timing work at all.
    profiling: bool = True
    # The PR dashboard at /hud. None means "on when hud.repos is set".
    hud: bool | None = None


class Limits(BaseModel):
    """Above these, the diff page lists files and loads each one lazily."""

    max_inline_diff_bytes: int = 1_500_000
    max_inline_files: int = 300


class Noise(BaseModel):
    """Files that are real changes but almost never worth reading."""

    # fnmatch patterns, matched against the path as git reports it.
    collapse_by_default: list[str] = Field(
        default_factory=lambda: [
            "*.lock",
            "*.sum",
            "**/generated/**",
            "**/*_pb2.py",
            "**/*.pb.go",
            "**/__snapshots__/**",
        ]
    )


class Tests(BaseModel):
    """Test files, which the diff page can leave out to show only the code."""

    # git glob pathspecs (`*` stops at `/`, a leading `**/` matches at any
    # depth), so git itself does the filtering.
    paths: list[str] = Field(
        default_factory=lambda: [
            "**/test_*.py",
            "**/*_test.*",
            "**/*.test.*",
            "**/*.spec.*",
            "**/conftest.py",
            "**/tests/**",
            "**/test/**",
            "**/__tests__/**",
            "**/testdata/**",
        ]
    )


_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class Hud(BaseModel):
    """The PR dashboard: which repos it looks at, and how far back."""

    # owner/name. They go into a GitHub search string, so the format is strict.
    repos: list[str] = Field(default_factory=list)
    # "Closed or merged recently" means within this many workdays (Mon-Fri).
    closed_workdays: int = 3
    # Most PRs shown per section; GitHub search pages are at most 100.
    limit: int = Field(default=50, ge=1, le=100)
    # A search over several repos is slower than one `gh pr list`.
    timeout_seconds: float = 20.0

    @field_validator("repos")
    @classmethod
    def _check_repos(cls, repos: list[str]) -> list[str]:
        for repo in repos:
            if not _REPO.fullmatch(repo):
                raise ValueError(f"hud.repos: {repo!r} is not owner/name")
        return repos


class Tools(BaseModel):
    difft_path: str | None = None
    gh_path: str | None = None
    gh_timeout_seconds: float = 5.0


class DiffwebConfig(BaseModel):
    model_config = {"extra": "forbid"}

    roots: list[str] = Field(default_factory=lambda: ["~/code", "~/code.*", "~/worktrees/*/*"])
    server: Server = Field(default_factory=Server)
    features: Features = Field(default_factory=Features)
    limits: Limits = Field(default_factory=Limits)
    noise: Noise = Field(default_factory=Noise)
    tests: Tests = Field(default_factory=Tests)
    tools: Tools = Field(default_factory=Tools)
    hud: Hud = Field(default_factory=Hud)
    state_db: str = "~/.local/state/diffweb/state.db"
    profile_log: str = "~/.local/state/diffweb/profiles.jsonl"
    # Older profiles are dropped so the log cannot grow without bound.
    profile_keep: int = 500

    def hud_enabled(self) -> bool:
        if self.features.hud is None:
            return bool(self.hud.repos)
        return self.features.hud

    def expanded_roots(self) -> list[str]:
        return [os.path.expanduser(r) for r in self.roots]

    def state_db_path(self) -> Path:
        return Path(os.path.expanduser(self.state_db))

    def profile_log_path(self) -> Path:
        return Path(os.path.expanduser(self.profile_log))


def config_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.path.expanduser(os.environ.get(ENV_VAR) or DEFAULT_CONFIG_PATH))


def load_config(path: str | os.PathLike[str] | None = None) -> DiffwebConfig:
    """Load config, falling back to defaults when the file is absent.

    A malformed file is a startup error rather than something we paper over.
    $DIFFWEB_PORT and $DIFFWEB_ROOTS override the file.
    """
    resolved = config_path(path)
    raw = (yaml.safe_load(resolved.read_text()) or {}) if resolved.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError(f"{resolved}: expected a YAML mapping, got {type(raw).__name__}")
    if port := os.environ.get(PORT_ENV_VAR):
        raw["server"] = {**(raw.get("server") or {}), "port": int(port)}
    if roots := os.environ.get(ROOTS_ENV_VAR):
        raw["roots"] = roots.split(os.pathsep)
    return DiffwebConfig.model_validate(raw)
