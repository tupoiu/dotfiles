"""diffweb: files that match an `emphasis` rule are marked for the page."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from diffweb import gitio
from diffweb.config import DiffwebConfig, EmphasisRule, load_config

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("a.proto", "proto"),
        ("proto/scheduler/v1/scheduler.proto", "proto"),
        ("pyproject.toml", "dependencies"),
        ("projects/scheduler/Cargo.toml", "dependencies"),
        ("docs/API_SPEC.md", "spec"),
        ("SPEC", "spec"),
        ("specs/a.md", "spec"),             # straight in specs/, no folder between
        ("specs/deep/b.md", "spec"),
        ("docs/specs/run.md", "spec"),
        ("docs/specs/deep/b.md", "spec"),
        ("docs/specs/a.py", None),          # only .md files in specs/
        ("myspecs/a.md", None),             # a folder that only ends in "specs"
        ("docs/SPECS/a.py", None),          # SPEC in a folder, not the name
        ("web/a.spec.ts", None),            # case-sensitive: a test, not a spec
        ("diffweb/INTENT-hud.md", "intent"),
        ("INTENT.md", "intent"),
        ("docs/INTENT/a.md", None),         # INTENT in a folder, not the name
        ("src/intent.py", None),            # case-sensitive
        ("src/main.rs", None),
    ],
)
def test_default_rules(path: str, label: str | None) -> None:
    found = gitio.emphasis_for(path, DiffwebConfig().emphasis)
    assert (found or {}).get("label") == label


def test_globs_are_git_style() -> None:
    assert gitio.glob_match("specs/a.md", "specs/**/*.md")
    assert not gitio.glob_match("docs/specs/a.md", "specs/**/*.md")  # anchored at the top
    assert gitio.glob_match("a/b.md", "*.md")  # no `/`: the name, in any folder
    assert not gitio.glob_match("a/b/c.md", "a/*.md")  # `*` stays in one folder
    assert gitio.glob_match("vendor/x/y.go", "vendor/**")


def test_first_rule_wins_and_the_page_gets_what_it_draws() -> None:
    rules = [
        EmphasisRule(label="first", paths=["*.toml"], color="pink"),
        EmphasisRule(label="second", paths=["Cargo.toml"], color="teal", header="filled"),
    ]
    assert gitio.emphasis_for("Cargo.toml", rules) == {"label": "first", "color": "pink", "header": "tinted"}


def test_config_replaces_the_defaults_and_checks_colours() -> None:
    assert DiffwebConfig(emphasis=[]).emphasis == []
    with pytest.raises(ValidationError):
        DiffwebConfig.model_validate({"emphasis": [{"paths": ["*.md"], "color": "red"}]})
    with pytest.raises(ValidationError):
        DiffwebConfig.model_validate({"emphasis": [{"paths": ["*.md"], "color": "teal", "style": "x"}]})


def test_api_rows_carry_their_emphasis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env={**os.environ, **GIT_ENV})

    repo = tmp_path / "proj"
    repo.mkdir()
    git("init", "-q", "-b", "master")
    (repo / "a.txt").write_text("a\n")
    git("add", ".")
    git("commit", "-qm", "base")
    (repo / "Cargo.toml").write_text("[package]\n")
    (repo / "api.proto").write_text("syntax = \"proto3\";\n")
    (repo / "a.txt").write_text("b\n")
    git("add", ".")
    git("commit", "-qm", "change")
    cfg = tmp_path / "diffweb.yaml"
    cfg.write_text(yaml.safe_dump({
        "roots": [str(repo)],
        "state_db": str(tmp_path / "state.db"),
        "profile_log": str(tmp_path / "profiles.jsonl"),
        "features": {"live_reload": False, "pr_links": False},
    }))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    wid = next(w.id for w in gitio.discover(load_config()))
    with TestClient(app_module.app) as c:
        rows = c.get(f"/api/w/{wid}/diff", params={"start": "HEAD~1", "end": "HEAD"}).json()["files"]
    app_module.reset_for_tests()
    by_path = {r["path"]: r["emphasis"] for r in rows}
    assert by_path == {
        "Cargo.toml": {"label": "dependencies", "color": "orange", "header": "tinted"},
        "api.proto": {"label": "proto", "color": "purple", "header": "filled"},
        "a.txt": None,
    }
