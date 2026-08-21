"""diffweb tests.

Everything runs against a throwaway git world under tmp_path; nothing here
reads the developer's real repos, and the suite must pass on a machine with
no ~/code at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from diffweb import gitio
from diffweb.config import DiffwebConfig, load_config
from diffweb.gitio import WORKTREE
from diffweb.state import State

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV}
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


def commit(repo: Path, name: str, body: str, message: str) -> None:
    (repo / name).write_text(body)
    git(repo, "add", name)
    git(repo, "commit", "-m", message)


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    """A bare origin, a main repo on a feature branch, and a linked worktree."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "master", str(origin))

    repo = tmp_path / "proj"
    git(tmp_path, "init", "-b", "master", str(repo))
    commit(repo, "a.txt", "one\ntwo\nthree\n", "initial")
    commit(repo, "b.txt", "beta\n", "add b")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-u", "origin", "master")
    git(repo, "remote", "set-head", "origin", "master")

    git(repo, "checkout", "-b", "feature")
    commit(repo, "a.txt", "one\nTWO\nthree\n", "change a")
    commit(repo, "c.txt", "gamma\n", "add c")
    (repo / "b.txt").write_text("beta\nuncommitted\n")  # dirty working tree

    linked = tmp_path / "proj.other"
    git(repo, "worktree", "add", "-b", "other", str(linked))

    return {"root": tmp_path, "repo": repo, "linked": linked, "origin": origin}


@pytest.fixture
def config(world: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DiffwebConfig:
    cfg_path = tmp_path / "diffweb.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "roots": [f"{world['root']}/proj", f"{world['root']}/proj.*"],
                "state_db": str(tmp_path / "state.db"),
                "features": {"structural": False, "reviewed_state": True, "live_reload": True},
            }
        )
    )
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg_path))
    return load_config()


@pytest.fixture
def client(config: DiffwebConfig) -> TestClient:
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        yield c
    app_module.reset_for_tests()


# --- config ---------------------------------------------------------------


def test_defaults_when_file_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIFFWEB_CONFIG", str(tmp_path / "nope.yaml"))
    cfg = load_config()
    assert cfg.server.port == 8765
    assert cfg.features.structural is False


def test_config_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"roots": ["~/x"], "server": {"port": 9999}}))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(p))
    cfg = load_config()
    assert cfg.server.port == 9999
    assert cfg.expanded_roots() == [os.path.expanduser("~/x")]


def test_invalid_config_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"server": {"port": "not-a-port"}}))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(p))
    with pytest.raises(Exception):
        load_config()


def test_unknown_key_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"rootz": ["~/x"]}))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(p))
    with pytest.raises(Exception):
        load_config()


# --- discovery ------------------------------------------------------------


def test_discovers_repo_and_linked_worktree(config: DiffwebConfig) -> None:
    names = {w.name for w in gitio.discover(config)}
    assert names == {"proj", "proj.other"}


def test_discovery_survives_missing_and_non_git_roots(
    world: dict[str, Path], tmp_path: Path
) -> None:
    (tmp_path / "plain").mkdir()
    cfg = DiffwebConfig(roots=[str(tmp_path / "does-not-exist"), str(tmp_path / "plain"), str(world["repo"])])
    assert {w.name for w in gitio.discover(cfg)} == {"proj", "proj.other"}


def test_worktree_found_outside_configured_roots(world: dict[str, Path], tmp_path: Path) -> None:
    # Only the main repo is globbed; the linked worktree comes from `git worktree list`.
    cfg = DiffwebConfig(roots=[str(world["repo"])])
    assert {w.name for w in gitio.discover(cfg)} == {"proj", "proj.other"}


def test_detached_head_labelled(world: dict[str, Path]) -> None:
    repo = world["repo"]
    sha = git(repo, "rev-parse", "HEAD~1").strip()
    git(repo, "checkout", "--detach", sha)
    cfg = DiffwebConfig(roots=[str(repo)])
    wt = next(w for w in gitio.discover(cfg) if w.name == "proj")
    assert wt.branch.startswith("detached")


def test_ids_are_stable_and_distinct(config: DiffwebConfig) -> None:
    first = {w.name: w.id for w in gitio.discover(config)}
    second = {w.name: w.id for w in gitio.discover(config)}
    assert first == second
    assert len(set(first.values())) == 2


# --- refs and diffs -------------------------------------------------------


def test_default_base_uses_origin_head(world: dict[str, Path]) -> None:
    assert gitio.default_base(str(world["repo"])) == "origin/master"


def test_default_base_without_origin(tmp_path: Path) -> None:
    solo = tmp_path / "solo"
    git(tmp_path, "init", "-b", "master", str(solo))
    commit(solo, "f.txt", "x\n", "init")
    assert gitio.default_base(str(solo)) == "master"


@pytest.mark.parametrize("bad", ["--upload-pack=touch /tmp/pwn", "-x", "a b", "a;b", "$(id)", "a\nb"])
def test_ref_validation_rejects_injection(world: dict[str, Path], bad: str) -> None:
    with pytest.raises(ValueError):
        gitio.resolve_ref(str(world["repo"]), bad)


def test_unknown_ref_errors(world: dict[str, Path]) -> None:
    with pytest.raises(gitio.GitError):
        gitio.resolve_ref(str(world["repo"]), "no-such-branch")


def test_merge_base_matches_git(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    assert gitio.merge_base(repo, "origin/master") == git(world["repo"], "merge-base", "origin/master", "HEAD").strip()


def test_commits_in_range(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    mb = gitio.merge_base(repo, "origin/master")
    subjects = [c["subject"] for c in gitio.commits(repo, mb, "HEAD")]
    assert subjects == ["add c", "change a"]  # git log order: newest first


def test_diff_to_worktree_includes_uncommitted(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    mb = gitio.merge_base(repo, "origin/master")
    paths = {r["path"] for r in gitio.numstat(repo, mb, WORKTREE)}
    assert paths == {"a.txt", "b.txt", "c.txt"}
    assert "uncommitted" in gitio.diff_text(repo, mb, WORKTREE)


def test_diff_between_commits_excludes_uncommitted(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    mb = gitio.merge_base(repo, "origin/master")
    head = gitio.resolve_ref(repo, "HEAD")
    paths = {r["path"] for r in gitio.numstat(repo, mb, head)}
    assert paths == {"a.txt", "c.txt"}


def test_numstat_counts(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    rows = {r["path"]: r for r in gitio.numstat(repo, gitio.merge_base(repo, "origin/master"), "HEAD")}
    assert rows["c.txt"]["added"] == 1 and rows["c.txt"]["removed"] == 0
    assert rows["a.txt"]["added"] == 1 and rows["a.txt"]["removed"] == 1


def test_diff_limited_to_files(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    mb = gitio.merge_base(repo, "origin/master")
    out = gitio.diff_text(repo, mb, "HEAD", files=["c.txt"])
    assert "c.txt" in out and "a.txt" not in out


def test_blob_shas_cover_worktree_changes(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    shas = gitio.blob_shas(repo, gitio.merge_base(repo, "origin/master"), WORKTREE)
    assert set(shas) == {"a.txt", "b.txt", "c.txt"}
    assert all(v and set(v) != {"0"} for v in shas.values())


def test_summarise(world: dict[str, Path]) -> None:
    wt = next(w for w in gitio.discover(DiffwebConfig(roots=[str(world["repo"])])) if w.name == "proj")
    s = gitio.summarise(wt, "origin/master")
    assert s.error is None
    assert s.ahead == 2
    assert s.files == 3
    assert s.added > 0


def test_summarise_reports_bad_base_instead_of_raising(world: dict[str, Path]) -> None:
    wt = next(w for w in gitio.discover(DiffwebConfig(roots=[str(world["repo"])])) if w.name == "proj")
    assert gitio.summarise(wt, "no-such-ref").error


# --- review state ---------------------------------------------------------


def test_review_status_transitions(tmp_path: Path) -> None:
    st = State(tmp_path / "s.db")
    assert st.review_status("wt", {"a.txt": "sha1"}) == {"a.txt": "new"}
    st.mark_reviewed("wt", "a.txt", "sha1")
    assert st.review_status("wt", {"a.txt": "sha1"}) == {"a.txt": "reviewed"}
    assert st.review_status("wt", {"a.txt": "sha2"}) == {"a.txt": "changed"}
    st.unmark_reviewed("wt", "a.txt")
    assert st.review_status("wt", {"a.txt": "sha1"}) == {"a.txt": "new"}


def test_base_ref_preference_persists(tmp_path: Path) -> None:
    st = State(tmp_path / "s.db")
    assert st.base_ref("wt") is None
    st.set_base_ref("wt", "origin/main")
    st.set_base_ref("wt", "origin/develop")
    assert st.base_ref("wt") == "origin/develop"
    assert st.all_base_refs() == {"wt": "origin/develop"}


# --- routes ---------------------------------------------------------------


def wt_id(config: DiffwebConfig, name: str) -> str:
    return next(w.id for w in gitio.discover(config) if w.name == name)


def test_index_lists_worktrees(client: TestClient, config: DiffwebConfig) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "proj.other" in r.text and "feature" in r.text


def test_worktree_page_renders(client: TestClient, config: DiffwebConfig) -> None:
    r = client.get(f"/w/{wt_id(config, 'proj')}")
    assert r.status_code == 200
    assert "origin/master" in r.text


def test_unknown_worktree_404s(client: TestClient) -> None:
    assert client.get("/w/deadbeef").status_code == 404


def test_commits_endpoint(client: TestClient, config: DiffwebConfig) -> None:
    r = client.get(f"/api/w/{wt_id(config, 'proj')}/commits")
    assert r.status_code == 200
    assert [c["subject"] for c in r.json()["commits"]] == ["add c", "change a"]


def test_diff_endpoint_defaults_to_merge_base_and_worktree(
    client: TestClient, config: DiffwebConfig
) -> None:
    r = client.get(f"/api/w/{wt_id(config, 'proj')}/diff")
    data = r.json()
    assert {f["path"] for f in data["files"]} == {"a.txt", "b.txt", "c.txt"}
    assert data["lazy"] is False
    assert "uncommitted" in data["diff"]
    assert data["review"] == {"a.txt": "new", "b.txt": "new", "c.txt": "new"}


def test_diff_endpoint_rejects_bad_ref(client: TestClient, config: DiffwebConfig) -> None:
    r = client.get(f"/api/w/{wt_id(config, 'proj')}/diff", params={"start": "--upload-pack=x"})
    assert r.status_code == 400


def test_diff_endpoint_lazy_when_over_limit(
    client: TestClient, config: DiffwebConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data["limits"] = {"max_inline_files": 1}
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    r = client.get(f"/api/w/{wt_id(config, 'proj')}/diff")
    body = r.json()
    assert body["lazy"] is True and body["diff"] == ""


def test_structural_disabled_by_default(client: TestClient, config: DiffwebConfig) -> None:
    r = client.get(f"/api/w/{wt_id(config, 'proj')}/diff", params={"renderer": "structural"})
    assert r.status_code == 400


def test_set_base_and_review_round_trip(client: TestClient, config: DiffwebConfig) -> None:
    wid = wt_id(config, "proj")
    assert client.post(f"/api/w/{wid}/base", params={"base": "no-such"}).status_code == 400
    assert client.post(f"/api/w/{wid}/base", params={"base": "origin/master"}).status_code == 200

    diff = client.get(f"/api/w/{wid}/diff").json()
    sha = diff["shas"]["c.txt"]
    client.post(f"/api/w/{wid}/reviewed", params={"path": "c.txt", "blob_sha": sha, "reviewed": True})
    assert client.get(f"/api/w/{wid}/diff").json()["review"]["c.txt"] == "reviewed"

    client.post(f"/api/w/{wid}/reviewed", params={"path": "c.txt", "blob_sha": "stale", "reviewed": True})
    assert client.get(f"/api/w/{wid}/diff").json()["review"]["c.txt"] == "changed"


def test_live_reload_disabled_404s(
    client: TestClient, config: DiffwebConfig, tmp_path: Path
) -> None:
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data["features"]["live_reload"] = False
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    assert client.get(f"/events/{wt_id(config, 'proj')}").status_code == 404


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").text == "ok"


def test_colliding_basenames_get_distinct_names(world: dict[str, Path], tmp_path: Path) -> None:
    # Two worktrees both called "code" is the real-world case this guards.
    nested = tmp_path / "nest"
    nested.mkdir()
    git(world["repo"], "worktree", "add", "-b", "third", str(nested / "proj"))
    cfg = DiffwebConfig(roots=[str(world["repo"])])
    names = [w.name for w in gitio.discover(cfg)]
    assert len(names) == len(set(names)) == 3


def test_bad_base_returns_400_with_message(client: TestClient, config: DiffwebConfig) -> None:
    wid = wt_id(config, "proj")
    for route in ("commits", "diff"):
        r = client.get(f"/api/w/{wid}/{route}", params={"base": "origin/nope"})
        assert r.status_code == 400, route
        assert "origin/nope" in r.json()["error"]


def test_bad_base_is_not_persisted(client: TestClient, config: DiffwebConfig) -> None:
    wid = wt_id(config, "proj")
    client.post(f"/api/w/{wid}/base", params={"base": "origin/nope"})
    assert client.get(f"/api/w/{wid}/commits").status_code == 200


def test_explicit_commit_range(client: TestClient, config: DiffwebConfig, world: dict[str, Path]) -> None:
    wid = wt_id(config, "proj")
    head = git(world["repo"], "rev-parse", "HEAD").strip()
    prev = git(world["repo"], "rev-parse", "HEAD~1").strip()
    data = client.get(f"/api/w/{wid}/diff", params={"start": prev, "end": head}).json()
    assert {f["path"] for f in data["files"]} == {"c.txt"}  # just the last commit


def _enable_structural(tmp_path: Path, **extra: object) -> None:
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data["features"]["structural"] = True
    data.setdefault("tools", {}).update(extra)
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()


def test_structural_reports_missing_difft(client: TestClient, config: DiffwebConfig, tmp_path: Path) -> None:
    wid = wt_id(config, "proj")
    _enable_structural(tmp_path, difft_path=str(tmp_path / "no-such-difft"))
    r = client.get(f"/api/w/{wid}/diff", params={"renderer": "structural"})
    assert r.status_code == 503
    assert "cargo binstall difftastic" in r.json()["error"]


@pytest.mark.skipif(not shutil.which("difft"), reason="difftastic is an optional extra")
def test_structural_renders(client: TestClient, config: DiffwebConfig, tmp_path: Path) -> None:
    wid = wt_id(config, "proj")
    _enable_structural(tmp_path)
    body = client.get(f"/api/w/{wid}/diff", params={"renderer": "structural"}).json()
    assert body["renderer"] == "structural"
    assert "c.txt" in body["html"]
