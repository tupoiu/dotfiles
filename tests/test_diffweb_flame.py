"""diffweb: the flame map's data - projects, kinds - and narrowing the diff to a scope."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from diffweb import gitio, projects
from diffweb.config import load_config
from diffweb.gitio import INDEX, WORKTREE

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def write(repo: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(body)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Two projects, one inside the other's folder tree, and loose files at the root."""
    r = tmp_path / "proj"
    git(tmp_path, "init", "-q", "-b", "master", str(r))
    write(r, {
        "README.md": "top\n",
        "projects/api/pyproject.toml": "[project]\n",
        "projects/api/src/app.py": "x = 1\n",
        "projects/api/tests/test_app.py": "def test(): ...\n",
        "projects/web/package.json": "{}\n",
        "projects/web/src/deep/page.ts": "export {}\n",
        "projects/web/src/deep/crate/Cargo.toml": "[package]\n",
        "projects/web/src/deep/crate/lib.rs": "fn f() {}\n",
        "scripts/run.sh": "echo\n",
    })
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


FILES = [
    "README.md",
    "projects/api/src/app.py",
    "projects/api/tests/test_app.py",
    "projects/web/src/deep/page.ts",
    "projects/web/src/deep/crate/lib.rs",
    "scripts/run.sh",
]
WANT = {
    "README.md": "",
    "projects/api/src/app.py": "projects/api",
    "projects/api/tests/test_app.py": "projects/api",
    "projects/web/src/deep/page.ts": "projects/web",
    "projects/web/src/deep/crate/lib.rs": "projects/web/src/deep/crate",  # the nearest manifest wins
    "scripts/run.sh": "",
}


@pytest.mark.parametrize("rev", ["HEAD", INDEX, WORKTREE])
def test_each_file_belongs_to_its_nearest_project(repo: Path, rev: str) -> None:
    assert projects.project_of(str(repo), rev, FILES) == WANT


def test_projects_are_read_on_the_end_side(repo: Path) -> None:
    # A manifest added on disk only counts when the range ends at the worktree.
    write(repo, {"scripts/package.json": "{}\n"})
    assert projects.project_of(str(repo), WORKTREE, ["scripts/run.sh"]) == {"scripts/run.sh": "scripts"}
    assert projects.project_of(str(repo), "HEAD", ["scripts/run.sh"]) == {"scripts/run.sh": ""}


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("projects/api/tests/test_app.py", "test"),  # in the tests set
        ("README.md", "docs"),
        ("docs/guide.rst", "docs"),
        ("notes.TXT", "docs"),
        ("Cargo.lock", "docs"),
        ("diffweb/bun.lock", "docs"),
        ("web/package-lock.json", "docs"),
        ("src/app.py", "code"),
        ("package.json", "code"),
    ],
)
def test_kinds(path: str, kind: str) -> None:
    assert projects.kind_of(path, {"projects/api/tests/test_app.py"}) == kind


# --- the diff endpoint -------------------------------------------------------


@pytest.fixture
def client(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, {
        "projects/api/src/app.py": "x = 2\ny = 3\n",
        "projects/api/tests/test_app.py": "def test(): assert True\n",
        "projects/web/src/deep/page.ts": "export function a(): void {}\n",
        "README.md": "top\nmore\n",
    })
    git(repo, "commit", "-q", "-am", "change")
    cfg = tmp_path / "diffweb.yaml"
    cfg.write_text(yaml.safe_dump({
        "roots": [str(repo)], "state_db": str(tmp_path / "state.db"),
        "profile_log": str(tmp_path / "profiles.jsonl"), "tests": {"paths": ["**/tests/**"]},
    }))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        c.wt = gitio.discover(load_config())[0].id  # type: ignore[attr-defined]
        yield c
    app_module.reset_for_tests()


def diff(client: TestClient, **params: str) -> dict:
    r = client.get(f"/api/w/{client.wt}/diff", params={"base": "master", **params})  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text
    return r.json()


def test_diff_carries_the_whole_map_and_a_scope_narrows_the_rest(client: TestClient) -> None:
    whole = diff(client)
    by_path = {m["path"]: m for m in whole["map"]}
    assert by_path["projects/api/src/app.py"] == {
        "path": "projects/api/src/app.py", "added": 2, "removed": 1, "kind": "code", "project": "projects/api",
    }
    assert by_path["projects/api/tests/test_app.py"]["kind"] == "test"
    assert by_path["README.md"]["kind"] == "docs" and by_path["README.md"]["project"] == ""
    assert whole["scope"] == ""

    narrowed = diff(client, scope="projects/api/")
    assert narrowed["scope"] == "projects/api"
    assert sorted(f["path"] for f in narrowed["files"]) == ["projects/api/src/app.py", "projects/api/tests/test_app.py"]
    assert set(narrowed["shas"]) == {"projects/api/src/app.py", "projects/api/tests/test_app.py"}
    assert "page.ts" not in narrowed["diff"]
    assert narrowed["tests"]["files"] == 1  # the tests stat follows the scope too
    assert narrowed["map"] == whole["map"]  # the map stays whole, to click back out of

    # hide tests: test files leave the map as well as the diff.
    hidden = diff(client, tests="false", scope="projects/api")
    assert [f["path"] for f in hidden["files"]] == ["projects/api/src/app.py"]
    assert "projects/api/tests/test_app.py" not in {m["path"] for m in hidden["map"]}


@pytest.mark.parametrize("bad", ["../etc", "-x", ":(glob)*", "a//b"])
def test_a_suspicious_scope_is_refused(client: TestClient, bad: str) -> None:
    r = client.get(f"/api/w/{client.wt}/diff", params={"base": "master", "scope": bad})  # type: ignore[attr-defined]
    assert r.status_code == 400


def test_the_skeleton_follows_the_scope(client: TestClient) -> None:
    r = client.get(f"/api/w/{client.wt}/skeleton", params={"base": "master", "scope": "projects/web"})  # type: ignore[attr-defined]
    assert [f["path"] for f in r.json()["files"]] == ["projects/web/src/deep/page.ts"]
