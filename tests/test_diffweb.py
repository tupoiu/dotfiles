"""diffweb tests.

Everything runs against a throwaway git world under tmp_path; nothing here
reads the developer's real repos, and the suite must pass on a machine with
no ~/code at all.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from diffweb import __main__ as diffweb_main
from diffweb import forge, gitio, profiling
from diffweb.config import DiffwebConfig, Noise, load_config
from diffweb.gitio import INDEX, UPSTREAM, WORKTREE
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
                "profile_log": str(tmp_path / "profiles.jsonl"),
                "features": {"structural": False, "reviewed_state": True, "live_reload": True},
            }
        )
    )
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg_path))
    return load_config()


@pytest.fixture
def fake_gh(tmp_path: Path) -> Path:
    """A stand-in for `gh pr list` so tests never touch the network.

    It records each invocation so the caching test can count calls.
    """
    script = tmp_path / "gh"
    counter = tmp_path / "gh-calls"
    script.write_text(
        "#!/bin/sh\n"
        f"printf x >> {counter}\n"
        'printf \'[{"number":42,"title":"Add the thing",'
        '"url":"https://example.invalid/pr/42","state":"OPEN","isDraft":true,'
        '"createdAt":"2026-08-18T09:00:00Z"}]\'\n'
    )
    script.chmod(0o755)
    return script


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


def clear_cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset what apply_args sets, so that teardown removes it again.

    A plain delenv of an unset variable records nothing to restore.
    """
    for var in ("DIFFWEB_CONFIG", "DIFFWEB_PORT", "DIFFWEB_ROOTS"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"roots": ["~/x"], "server": {"host": "0.0.0.0", "port": 9999}}))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(p))
    monkeypatch.setenv("DIFFWEB_PORT", "9001")
    monkeypatch.setenv("DIFFWEB_ROOTS", os.pathsep.join(["~/a", "/b/*"]))
    cfg = load_config()
    assert cfg.server.port == 9001
    assert cfg.server.host == "0.0.0.0"  # the rest of server: is kept
    assert cfg.roots == ["~/a", "/b/*"]


def test_cli_flags_reach_load_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_cli_env(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"features": {"structural": True}}))
    args = diffweb_main.parse_args(["--config", str(p), "--port", "9002", "--root", "~/a", "--root", "~/b"])
    diffweb_main.apply_args(args)
    cfg = load_config()
    assert (cfg.features.structural, cfg.server.port, cfg.roots) == (True, 9002, ["~/a", "~/b"])


def test_print_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    clear_cli_env(monkeypatch)
    diffweb_main.main(["--config", str(tmp_path / "nope.yaml"), "--port", "9003", "--print-config"])
    out = capsys.readouterr().out
    assert out.startswith(f"# {tmp_path / 'nope.yaml'} (absent: defaults)\n")
    # What it prints is itself a valid config.
    assert DiffwebConfig.model_validate(yaml.safe_load(out)).server.port == 9003


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


def test_diff_to_index_shows_only_staged(world: dict[str, Path]) -> None:
    """The staged shortcut: what is in the index, and nothing merely on disk."""
    repo = str(world["repo"])
    (world["repo"] / "d.txt").write_text("staged\n")
    git(world["repo"], "add", "d.txt")
    mb = gitio.merge_base(repo, "origin/master")

    paths = {r["path"] for r in gitio.numstat(repo, mb, INDEX)}
    assert paths == {"a.txt", "c.txt", "d.txt"}   # b.txt is unstaged, so absent
    text = gitio.diff_text(repo, mb, INDEX)
    assert "staged" in text and "uncommitted" not in text
    assert set(gitio.blob_shas(repo, mb, INDEX)) == paths


def test_index_is_only_ever_an_end(world: dict[str, Path], client: TestClient) -> None:
    repo = str(world["repo"])
    assert gitio.resolve_ref(repo, INDEX) == INDEX
    wt = next(w for w in gitio.discover(load_config()) if w.name == "proj")
    assert client.get(f"/api/w/{wt.id}/diff", params={"start": INDEX}).status_code == 400


def test_api_diff_accepts_the_index_as_an_end(world: dict[str, Path], client: TestClient) -> None:
    repo = world["repo"]
    (repo / "d.txt").write_text("staged\n")
    git(repo, "add", "d.txt")
    wt = next(w for w in gitio.discover(load_config()) if w.name == "proj")
    data = client.get(f"/api/w/{wt.id}/diff", params={"end": INDEX}).json()
    assert {f["path"] for f in data["files"]} == {"a.txt", "c.txt", "d.txt"}
    assert data["end"] == INDEX


def test_api_diff_of_the_last_commit_only(world: dict[str, Path], client: TestClient) -> None:
    """The last-commit shortcut: HEAD~1..HEAD, with nothing uncommitted in it."""
    wt = next(w for w in gitio.discover(load_config()) if w.name == "proj")
    data = client.get(f"/api/w/{wt.id}/diff", params={"start": "HEAD~1", "end": "HEAD"}).json()
    assert {f["path"] for f in data["files"]} == {"c.txt"}   # the "add c" commit
    assert "uncommitted" not in data["diff"]


def test_api_diff_of_unpushed_commits(world: dict[str, Path], client: TestClient) -> None:
    """The unpushed shortcut: what the upstream has not got yet."""
    repo = world["repo"]
    git(repo, "push", "-u", "origin", "feature")
    commit(repo, "e.txt", "epsilon\n", "add e")     # one commit past the upstream
    wt = next(w for w in gitio.discover(load_config()) if w.name == "proj")

    data = client.get(f"/api/w/{wt.id}/diff", params={"start": UPSTREAM, "end": "HEAD"}).json()
    assert {f["path"] for f in data["files"]} == {"e.txt"}


def test_unpushed_without_an_upstream_explains_itself(
    world: dict[str, Path], client: TestClient
) -> None:
    """A branch with no upstream is normal, so say so rather than 'unknown ref'."""
    wt = next(w for w in gitio.discover(load_config()) if w.name == "proj")
    r = client.get(f"/api/w/{wt.id}/diff", params={"start": UPSTREAM, "end": "HEAD"})
    assert r.status_code == 400
    assert "no upstream" in r.json()["error"]


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


# --- merge commits ---------------------------------------------------------


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    return tuple(int(n) for n in re.findall(r"\d+", out)[:2])


# `git show --remerge-diff` arrived in git 2.36; older git rejects the flag.
needs_remerge = pytest.mark.skipif(_git_version() < (2, 36), reason="needs git >= 2.36 for --remerge-diff")


@pytest.fixture
def merged(world: dict[str, Path]) -> dict[str, Path]:
    """`feature` with a hand-resolved merge of `side` on top.

    `side` touches a.txt (conflicts with feature's TWO) and adds s.txt (merges
    cleanly). The resolution is the one line a human wrote: MERGED.
    """
    repo = world["repo"]
    git(repo, "checkout", "-b", "side", "origin/master")
    commit(repo, "a.txt", "one\ntwo-side\nthree\n", "side a")
    commit(repo, "s.txt", "side\n", "add s")
    git(repo, "checkout", "feature")
    proc = subprocess.run(["git", "merge", "side"], cwd=repo, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV})
    assert proc.returncode != 0, "the merge must conflict for the test to mean anything"
    (repo / "a.txt").write_text("one\nMERGED\nthree\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "merge side")
    return world


def test_commit_list_flags_merge_commits(merged: dict[str, Path]) -> None:
    repo = str(merged["repo"])
    rows = gitio.commits(repo, gitio.merge_base(repo, "origin/master"), "HEAD")
    assert [r["subject"] for r in rows if r["merge"]] == ["merge side"]
    assert not any(r["merge"] for r in rows if r["subject"] != "merge side")


def test_parents_of_a_merge_and_of_a_plain_commit(merged: dict[str, Path]) -> None:
    repo = str(merged["repo"])
    head, first = (git(merged["repo"], "rev-parse", "HEAD", "HEAD^1").split())
    assert gitio.parents(repo, head)[0] == first
    assert len(gitio.parents(repo, head)) == 2
    assert len(gitio.parents(repo, first)) == 1
    assert gitio.is_merge_commit(repo, head) and not gitio.is_merge_commit(repo, first)


@needs_remerge
def test_remerge_diff_shows_only_the_resolution(merged: dict[str, Path]) -> None:
    """A merge commit's true diff is mostly the other branch; what a human wrote
    is the conflict resolution, and that is what --remerge-diff isolates."""
    repo = str(merged["repo"])
    head = gitio.resolve_ref(repo, "HEAD")
    text = gitio.remerge_diff_text(repo, head)
    assert "+MERGED" in text
    assert "<<<<<<<" in text and ">>>>>>>" in text      # the conflict being resolved
    assert "s.txt" not in text                          # merged cleanly, so not authored
    paths = {r["path"] for r in gitio.remerge_numstat(repo, head)}
    assert paths == {"a.txt"}


def test_first_parent_diff_of_a_merge_shows_everything(merged: dict[str, Path]) -> None:
    repo = str(merged["repo"])
    head = gitio.resolve_ref(repo, "HEAD")
    paths = {r["path"] for r in gitio.numstat(repo, gitio.parents(repo, head)[0], head)}
    assert paths == {"a.txt", "s.txt"}


@needs_remerge
def test_remerge_diff_limited_to_files(merged: dict[str, Path]) -> None:
    repo = str(merged["repo"])
    head = gitio.resolve_ref(repo, "HEAD")
    assert gitio.remerge_diff_text(repo, head, files=["s.txt"]) == ""
    assert "a.txt" in gitio.remerge_diff_text(repo, head, files=["a.txt"])


@needs_remerge
def test_remerge_blob_shas_are_the_resolved_blobs(merged: dict[str, Path]) -> None:
    repo = str(merged["repo"])
    head = gitio.resolve_ref(repo, "HEAD")
    shas = gitio.remerge_blob_shas(repo, head)
    resolved = git(merged["repo"], "rev-parse", "HEAD:a.txt").strip()
    assert set(shas) == {"a.txt"}
    assert resolved.startswith(shas["a.txt"])


@needs_remerge
def test_api_single_merge_commit_defaults_to_remerge(
    merged: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    data = client.get(f"/api/w/{wid}/diff", params={"start": "HEAD~1", "end": "HEAD"}).json()
    assert data["merge"] is True and data["remerge"] is True
    assert {f["path"] for f in data["files"]} == {"a.txt"}
    assert "+MERGED" in data["diff"] and "s.txt" not in data["diff"]


def test_api_merge_commit_true_diff_on_request(
    merged: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    data = client.get(
        f"/api/w/{wid}/diff", params={"start": "HEAD~1", "end": "HEAD", "remerge": "false"}
    ).json()
    assert data["merge"] is True and data["remerge"] is False
    assert {f["path"] for f in data["files"]} == {"a.txt", "s.txt"}
    assert "<<<<<<<" not in data["diff"]


@needs_remerge
def test_api_remerge_only_applies_to_a_single_merge_commit(
    merged: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    # A range that merely *ends* at the merge is a range, not a commit.
    data = client.get(f"/api/w/{wid}/diff", params={"end": "HEAD"}).json()
    assert data["merge"] is False and data["remerge"] is False
    assert "s.txt" in {f["path"] for f in data["files"]}
    # A single non-merge commit is not a merge either, even if asked.
    data = client.get(
        f"/api/w/{wid}/diff", params={"start": "HEAD~2", "end": "HEAD~1", "remerge": "true"}
    ).json()
    assert data["merge"] is False and data["remerge"] is False
    # The lazy, per-file path keeps the mode.
    data = client.get(
        f"/api/w/{wid}/diff", params={"start": "HEAD~1", "end": "HEAD", "files": ["a.txt"]}
    ).json()
    assert data["remerge"] is True and "<<<<<<<" in data["diff"]


def test_remerge_and_true_diffs_do_not_share_a_cache_entry(
    merged: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    params = {"start": "HEAD~1", "end": "HEAD"}
    first = client.get(f"/api/w/{wid}/diff", params=params).json()["diff"]
    second = client.get(f"/api/w/{wid}/diff", params={**params, "remerge": "false"}).json()["diff"]
    third = client.get(f"/api/w/{wid}/diff", params=params).json()["diff"]
    assert first == third and first != second


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
    """The structural view is ordinary unified diff text, drawn by the same renderer."""
    wid = wt_id(config, "proj")
    _enable_structural(tmp_path)
    body = client.get(f"/api/w/{wid}/diff", params={"renderer": "structural"}).json()
    assert body["renderer"] == "structural"
    assert "html" not in body
    assert "diff --git a/c.txt b/c.txt" in body["diff"]
    assert body["files"] and body["lazy"] is False


@pytest.mark.skipif(not shutil.which("difft"), reason="difftastic is an optional extra")
def test_structural_hides_a_pure_reformat(world: dict[str, Path]) -> None:
    """Reindenting a function is a whole-file line diff but no structural change:
    the file keeps its header (so it is listed) and loses its hunks."""
    repo = world["repo"]
    commit(repo, "mod.py", "def f(x):\n  return x\n\ndef g():\n  return 2\n", "add mod")
    (repo / "mod.py").write_text("def f(x):\n    return x\n\n\ndef g():\n    return 2\n")
    text = gitio.structural_diff(str(repo), "HEAD", WORKTREE)
    assert "diff --git a/mod.py b/mod.py" in text
    assert "@@" not in text.split("diff --git a/mod.py")[1]
    assert "+" + "    return x" not in text


@pytest.mark.skipif(not shutil.which("difft"), reason="difftastic is an optional extra")
def test_structural_marks_only_the_syntactic_change(world: dict[str, Path]) -> None:
    repo = world["repo"]
    commit(repo, "mod.py", "def f(x):\n  return x\n\ndef g():\n  return 2\n", "add mod")
    (repo / "mod.py").write_text("def f(x):\n    return x\n\ndef g():\n    return 3\n")
    text = gitio.structural_diff(str(repo), "HEAD", WORKTREE)
    hunk = text.split("@@", 2)[2]
    assert "-  return 2" in hunk and "+    return 3" in hunk
    # The reindented return line is context, in its after spelling.
    assert "     return x" in hunk and "-  return x" not in hunk


# --- structural: pure adapter tests, no difft needed ----------------------

from diffweb import structural  # noqa: E402

GIT_PATCH = (
    "diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n"
    "@@ -1,2 +1,2 @@\n a\n-b\n+c\n"
    "diff --git a/old.txt b/new.txt\nsimilarity index 100%\nrename from old.txt\nrename to new.txt\n"
    "diff --git a/i.png b/i.png\nindex 3..4 100644\nBinary files a/i.png and b/i.png differ\n"
)


def test_split_patches_keeps_headers_and_paths() -> None:
    ps = structural.split_patches(GIT_PATCH)
    assert [(p.old_path, p.new_path) for p in ps] == [("a.py", "a.py"), ("old.txt", "new.txt"), ("i.png", "i.png")]
    assert ps[0].hunks == ["@@ -1,2 +1,2 @@", " a", "-b", "+c"]
    assert ps[1].hunks == [] and ps[2].hunks == []
    assert "".join(p.text() for p in ps) == GIT_PATCH


def test_rows_from_alignment_uses_after_text_for_unchanged_lines() -> None:
    lhs = ["def f():", "  return 1", "", "x = 2"]
    rhs = ["def f():", "    return 1", "", "x = 3"]
    aligned = [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]]  # difft counts the phantom line after the final newline
    rows = structural.rows_from_alignment(lhs, rhs, aligned, {3}, {3})
    assert rows == [(" ", "def f():"), (" ", "    return 1"), (" ", ""), ("-", "x = 2"), ("+", "x = 3")]


def test_rows_from_alignment_batches_removals_before_additions() -> None:
    lhs = ["a", "b", "c", "z"]
    rhs = ["a", "B", "C", "z"]
    rows = structural.rows_from_alignment(lhs, rhs, [[0, 0], [1, 1], [2, 2], [3, 3]], {1, 2}, {1, 2})
    assert [k for k, _ in rows] == [" ", "-", "-", "+", "+", " "]


def test_hunks_have_exact_line_counts_and_context() -> None:
    rows = [(" ", str(i)) for i in range(10)]
    rows[5] = ("-", "five")
    rows.insert(6, ("+", "FIVE"))
    hunks = structural.hunks_from_rows(rows, context=2)
    assert hunks[0] == "@@ -4,5 +4,5 @@"
    assert hunks[1:] == [" 3", " 4", "-five", "+FIVE", " 6", " 7"]


def test_hunks_split_when_changes_are_far_apart() -> None:
    rows = [(" ", str(i)) for i in range(30)]
    rows[2] = ("+", "two")
    rows[25] = ("-", "twenty-five")
    hunks = structural.hunks_from_rows(rows)
    heads = [h for h in hunks if h.startswith("@@")]
    assert heads == ["@@ -1,5 +1,6 @@", "@@ -22,7 +23,6 @@"]


def test_merge_replaces_hunks_only_where_difft_had_a_view() -> None:
    blobs = {("S", "a.py"): b"a\nb\n", ("E", "a.py"): b"a\nc\n"}
    entries = [
        {"path": "a.py", "language": "Python", "status": "changed",
         "aligned_lines": [[0, 0], [1, 1], [2, 2]],
         "chunks": [[{"lhs": {"line_number": 1, "changes": []}, "rhs": {"line_number": 1, "changes": []}}]]},
        {"path": "new.txt", "language": "Text", "status": "unchanged"},
        {"path": "i.png", "language": "Binary", "status": "changed"},
    ]
    difft_out = "\n".join(json.dumps(e) for e in entries) + "\ndifft warning: something\n"
    out = structural.merge(GIT_PATCH, difft_out, lambda rev, f: blobs.get((rev, f)), "S", "E")
    assert out == GIT_PATCH  # same verdict as git here, so byte-identical


def test_merge_drops_hunks_of_a_file_difft_calls_unchanged() -> None:
    entries = [{"path": "a.py", "language": "Python", "status": "unchanged"}]
    out = structural.merge(GIT_PATCH, json.dumps(entries[0]), lambda rev, f: None, "S", "E")
    assert out.startswith("diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\ndiff --git a/old.txt")


def test_merge_keeps_git_patch_when_a_blob_is_missing() -> None:
    entry = {"path": "a.py", "language": "Python", "status": "changed",
             "aligned_lines": [[0, 0]], "chunks": []}
    out = structural.merge(GIT_PATCH, json.dumps(entry), lambda rev, f: None, "S", "E")
    assert out == GIT_PATCH


# --- untracked files ------------------------------------------------------


def test_untracked_file_is_a_new_file_when_the_range_ends_at_the_worktree(world: dict[str, Path]) -> None:
    repo = world["repo"]
    (repo / "fresh.py").write_text("print(1)\nprint(2)\n")
    (repo / "pic.png").write_bytes(b"\x89PNG\x00\x01")
    path = str(repo)
    rows = {r["path"]: r for r in gitio.numstat(path, "HEAD", WORKTREE)}
    assert rows["fresh.py"] == {"path": "fresh.py", "added": 2, "removed": 0, "binary": False,
                                "image": False, "untracked": True}
    assert rows["pic.png"]["binary"] and rows["pic.png"]["image"] and rows["pic.png"]["untracked"]
    changes = gitio.raw_changes(path, "HEAD", WORKTREE)
    assert changes["fresh.py"]["status"] == "A"
    assert changes["fresh.py"]["sha"] == git(repo, "hash-object", "fresh.py").strip()
    text = gitio.diff_text(path, "HEAD", WORKTREE)
    assert "diff --git a/fresh.py b/fresh.py\nnew file mode" in text
    assert "+++ b/fresh.py" in text and "+print(2)" in text
    assert "Binary files /dev/null and b/pic.png differ" in text
    # The tracked changes are still there, ahead of the untracked ones.
    assert text.index("@@") < text.index("+++ b/fresh.py")


def test_untracked_files_do_not_leak_into_other_ranges(world: dict[str, Path]) -> None:
    repo = world["repo"]
    (repo / "fresh.py").write_text("print(1)\n")
    path = str(repo)
    assert "fresh.py" not in [r["path"] for r in gitio.numstat(path, "HEAD", INDEX)]
    assert "fresh.py" not in gitio.diff_text(path, "HEAD", INDEX)
    assert "fresh.py" not in gitio.diff_text(path, "HEAD~1", "HEAD")
    # The per-file limiter applies to untracked files too.
    assert "fresh.py" not in gitio.diff_text(path, "HEAD", WORKTREE, files=["c.txt"])
    assert "c.txt" not in gitio.diff_text(path, "HEAD", WORKTREE, files=["fresh.py"])


def test_ignored_and_nested_repo_paths_are_not_untracked(world: dict[str, Path]) -> None:
    repo = world["repo"]
    (repo / ".git" / "info" / "exclude").write_text("*.log\n")
    (repo / "noise.log").write_text("x\n")
    nested = repo / "vendored"
    git(world["root"], "init", "-q", str(nested))
    (nested / "f.txt").write_text("inside\n")
    assert gitio.untracked_files(str(repo)) == []


def test_untracked_edit_is_a_cache_miss(world: dict[str, Path]) -> None:
    repo = world["repo"]
    (repo / "fresh.py").write_text("v1\n")
    assert "+v1" in gitio.diff_text(str(repo), "HEAD", WORKTREE)
    time.sleep(0.01)
    (repo / "fresh.py").write_text("v2!\n")  # a different size, so the stat key changes even on a coarse clock
    assert "+v2!" in gitio.diff_text(str(repo), "HEAD", WORKTREE)


def test_api_diff_and_catalog_include_untracked_files(client: TestClient, config: DiffwebConfig) -> None:
    wid = wt_id(config, "proj")
    repo = next(w.path for w in gitio.discover(config) if w.name == "proj")
    (Path(repo) / "fresh.py").write_text("print(1)\n")
    data = client.get(f"/api/w/{wid}/diff").json()
    row = next(f for f in data["files"] if f["path"] == "fresh.py")
    assert row["untracked"] is True and row["status"] == "A"
    assert data["review"]["fresh.py"] == "new"
    assert "+++ b/fresh.py" in data["diff"]
    # Tick it reviewed; the sha is the on-disk blob, so the tick sticks until the file changes.
    client.post(f"/api/w/{wid}/reviewed", params={"path": "fresh.py", "blob_sha": data["shas"]["fresh.py"], "reviewed": "true"})
    assert client.get(f"/api/w/{wid}/diff").json()["review"]["fresh.py"] == "reviewed"
    (Path(repo) / "fresh.py").write_text("print(2)\n")
    assert client.get(f"/api/w/{wid}/diff").json()["review"]["fresh.py"] == "changed"
    # The catalog's file count agrees with the diff page.
    wt = next(w for w in gitio.discover(config) if w.name == "proj")
    assert gitio.summarise(wt, "origin/master").files == len(data["files"])


# --- branch context (PR links, ahead/behind, dirty) -----------------------


def test_ahead_behind_counts(world: dict[str, Path]) -> None:
    repo = str(world["repo"])
    assert gitio.ahead_behind(repo, "origin/master") == (2, 0)
    # Move origin/master on so the branch is genuinely behind.
    other = world["root"] / "clone"
    git(world["root"], "clone", "-q", str(world["origin"]), str(other))
    commit(other, "d.txt", "delta\n", "move master on")
    git(other, "push", "-q", "origin", "master")
    git(world["repo"], "fetch", "-q", "origin")
    assert gitio.ahead_behind(repo, "origin/master") == (2, 1)


def test_dirty_count(world: dict[str, Path]) -> None:
    # The fixture leaves exactly one uncommitted edit.
    assert gitio.dirty_count(str(world["repo"])) == 1
    assert gitio.dirty_count(str(world["linked"])) == 0


def test_summary_carries_branch_context(world: dict[str, Path]) -> None:
    wt = next(w for w in gitio.discover(DiffwebConfig(roots=[str(world["repo"])])) if w.name == "proj")
    s = gitio.summarise(wt, "origin/master")
    assert (s.ahead, s.behind, s.dirty) == (2, 0, 1)


def test_pr_lookup_without_gh_is_none(world: dict[str, Path], tmp_path: Path) -> None:
    assert forge.query(str(world["repo"]), "feature", gh_path=str(tmp_path / "no-gh")) is None


def test_pr_lookup_skips_detached_head(world: dict[str, Path], fake_gh: Path) -> None:
    assert forge.query(str(world["repo"]), "detached @ abc123", gh_path=str(fake_gh)) is None


def test_pr_lookup_parses_gh_output(world: dict[str, Path], fake_gh: Path) -> None:
    pr = forge.query(str(world["repo"]), "feature", gh_path=str(fake_gh))
    assert pr is not None
    assert (pr.number, pr.label, pr.status) == (42, "#42", "draft")
    assert pr.url.endswith("/42")


def test_pr_lookup_survives_a_broken_gh(world: dict[str, Path], tmp_path: Path) -> None:
    broken = tmp_path / "gh-broken"
    broken.write_text("#!/bin/sh\necho 'not json' \nexit 0\n")
    broken.chmod(0o755)
    assert forge.query(str(world["repo"]), "feature", gh_path=str(broken)) is None


def test_pr_status_is_cached_in_the_state_db(
    world: dict[str, Path], fake_gh: Path, tmp_path: Path
) -> None:
    counter = tmp_path / "gh-calls"
    st = State(tmp_path / "pr.db")
    for _ in range(3):
        found = forge.status(st, "wt1", str(world["repo"]), "feature", gh_path=str(fake_gh))
    assert counter.read_text().count("x") == 1
    assert found.pr.number == 42
    assert found.checked_age is not None


def test_pr_status_force_refresh_re_asks(world: dict[str, Path], fake_gh: Path, tmp_path: Path) -> None:
    counter = tmp_path / "gh-calls"
    st = State(tmp_path / "pr.db")
    forge.status(st, "wt1", str(world["repo"]), "feature", gh_path=str(fake_gh))
    forge.status(st, "wt1", str(world["repo"]), "feature", gh_path=str(fake_gh), force=True)
    assert counter.read_text().count("x") == 2


def test_pr_status_records_a_negative_answer(world: dict[str, Path], tmp_path: Path) -> None:
    """No PR is a real answer, and must be cached with its own timestamp."""
    empty = tmp_path / "gh-empty"
    empty.write_text("#!/bin/sh\nprintf '[]'\n")
    empty.chmod(0o755)
    st = State(tmp_path / "pr.db")
    found = forge.status(st, "wt1", str(world["repo"]), "feature", gh_path=str(empty))
    assert found.pr is None
    assert found.checked_at is not None
    assert st.pr_record("wt1", "feature") == (None, found.checked_at)


def test_pr_record_ignores_a_different_branch(tmp_path: Path) -> None:
    st = State(tmp_path / "pr.db")
    st.save_pr("wt1", "feature", {"number": 1}, 100.0)
    assert st.pr_record("wt1", "feature") == ({"number": 1}, 100.0)
    # The worktree has been switched since we looked.
    assert st.pr_record("wt1", "other") is None


def test_pr_created_at_is_parsed(world: dict[str, Path], fake_gh: Path) -> None:
    pr = forge.query(str(world["repo"]), "feature", gh_path=str(fake_gh))
    assert pr.created_at == pytest.approx(datetime(2026, 8, 18, 9, tzinfo=timezone.utc).timestamp())
    assert pr.opened_age is not None


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"), (45, "45s"), (60, "1m"), (119, "1m"), (3599, "59m"),
        (3600, "1h"), (86399, "23h"), (86400, "1d"), (2 * 86400, "2d"),
        (7 * 86400, "1w"), (60 * 86400, "8w"), (400 * 86400, "1y"), (-5, "0s"),
    ],
)
def test_relative_age(seconds: float, expected: str) -> None:
    assert forge.relative_age(seconds) == expected


def test_catalog_renders_pr_chip(client: TestClient, config: DiffwebConfig, tmp_path: Path,
                                 fake_gh: Path) -> None:
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data.setdefault("tools", {})["gh_path"] = str(fake_gh)
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    body = client.get("/").text
    assert "#42" in body and "pr-draft" in body


# --- noise control --------------------------------------------------------


@pytest.mark.parametrize(
    "path,noisy",
    [
        ("Cargo.lock", True),
        ("uv.lock", True),
        ("go.sum", True),
        ("projects/svc/Cargo.lock", True),
        ("api/generated/client.ts", True),
        ("proto/thing_pb2.py", True),
        ("api/thing.pb.go", True),
        ("tests/__snapshots__/a.snap", True),
        ("src/lib.rs", False),
        ("locked.rs", False),
        ("docs/generated-by-hand.md", False),
    ],
)
def test_is_noisy(path: str, noisy: bool) -> None:
    assert gitio.is_noisy(path, Noise().collapse_by_default) is noisy


def test_noise_patterns_are_configurable() -> None:
    assert gitio.is_noisy("src/lib.rs", ["*.rs"]) is True
    assert gitio.is_noisy("Cargo.lock", []) is False


def test_diff_endpoint_flags_noisy_files(
    client: TestClient, config: DiffwebConfig, world: dict[str, Path], tmp_path: Path
) -> None:
    commit(world["repo"], "deps.lock", "pinned\n", "add a lockfile")
    wid = wt_id(config, "proj")
    files = {f["path"]: f for f in client.get(f"/api/w/{wid}/diff").json()["files"]}
    assert files["deps.lock"]["noisy"] is True
    assert files["a.txt"]["noisy"] is False


def test_noise_list_can_be_emptied(
    client: TestClient, config: DiffwebConfig, world: dict[str, Path], tmp_path: Path
) -> None:
    commit(world["repo"], "deps.lock", "pinned\n", "add a lockfile")
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data["noise"] = {"collapse_by_default": []}
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    wid = wt_id(config, "proj")
    files = {f["path"]: f for f in client.get(f"/api/w/{wid}/diff").json()["files"]}
    assert files["deps.lock"]["noisy"] is False


def test_pr_links_can_be_disabled(client: TestClient, config: DiffwebConfig, tmp_path: Path,
                                  fake_gh: Path) -> None:
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data.setdefault("tools", {})["gh_path"] = str(fake_gh)
    data["features"]["pr_links"] = False
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    assert "#42" not in client.get("/").text


def test_worktree_page_wires_up_keyboard_nav(client: TestClient, config: DiffwebConfig) -> None:
    # The behaviour itself is browser-tested; this catches the wiring regressing.
    # keys.ts is bundled into worktree.js, so the page carries one script of ours.
    body = client.get(f"/w/{wt_id(config, 'proj')}").text
    assert "/static/dist/worktree.js" in body
    assert 'id="key-help-btn"' in body



def test_hide_reviewed_control_is_present(client: TestClient, config: DiffwebConfig) -> None:
    assert 'id="hide-reviewed"' in client.get(f"/w/{wt_id(config, 'proj')}").text


# --- the non-test diff -------------------------------------------------------


@pytest.fixture
def with_tests(world: dict[str, Path]) -> dict[str, Path]:
    """`feature` also touches tests: a committed test module, a test fixture
    under tests/, and an untracked Go test."""
    repo = world["repo"]
    (repo / "tests").mkdir()
    commit(repo, "tests/fixture.json", "{}\n", "add a fixture")
    commit(repo, "test_a.py", "assert 1\nassert 2\n", "add a test")
    (repo / "src").mkdir()
    (repo / "src" / "thing_test.go").write_text("package src\n")  # untracked
    (repo / "src" / "thing.go").write_text("package src\n")       # untracked, not a test
    return world


def _paths(rows: object) -> set[str]:
    return {r["path"] for r in rows} if isinstance(rows, list) else set(rows)


def test_test_pathspecs_match_the_default_patterns(with_tests: dict[str, Path]) -> None:
    repo = str(with_tests["repo"])
    patterns = DiffwebConfig().tests.paths
    mb = gitio.merge_base(repo, "origin/master")
    only = gitio.numstat(repo, mb, WORKTREE, gitio.test_pathspecs(patterns, exclude=False))
    assert _paths(only) == {"tests/fixture.json", "test_a.py", "src/thing_test.go"}


def test_excluding_tests_filters_every_view_of_the_range(with_tests: dict[str, Path]) -> None:
    repo = str(with_tests["repo"])
    spec = gitio.test_pathspecs(DiffwebConfig().tests.paths, exclude=True)
    mb = gitio.merge_base(repo, "origin/master")
    code = {"a.txt", "b.txt", "c.txt", "src/thing.go"}
    assert _paths(gitio.numstat(repo, mb, WORKTREE, spec)) == code
    assert _paths(gitio.raw_changes(repo, mb, WORKTREE, spec)) == code
    text = gitio.diff_text(repo, mb, WORKTREE, spec=spec)
    assert "b/src/thing.go" in text and "+uncommitted" in text
    assert "test_a.py" not in text and "fixture.json" not in text and "thing_test.go" not in text
    # A per-file load of a test file under the filter is empty, not an error.
    assert gitio.diff_text(repo, mb, WORKTREE, ["test_a.py"], spec) == ""
    assert "+TWO" in gitio.diff_text(repo, mb, WORKTREE, ["a.txt"], spec)


@needs_remerge
def test_excluding_tests_applies_to_a_remerge_diff(merged: dict[str, Path]) -> None:
    repo = str(merged["repo"])
    head = gitio.resolve_ref(repo, "HEAD")
    assert _paths(gitio.remerge_numstat(repo, head)) == {"a.txt"}
    spec = gitio.test_pathspecs(["a.*"], exclude=True)
    assert gitio.remerge_numstat(repo, head, spec) == []
    assert gitio.remerge_raw_changes(repo, head, spec) == {}
    assert gitio.remerge_diff_text(repo, head, spec=spec) == ""


def test_api_diff_hides_tests_on_request(
    with_tests: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    everything = client.get(f"/api/w/{wid}/diff").json()
    assert "test_a.py" in _paths(everything["files"])
    assert everything["hidden_tests"] is None

    data = client.get(f"/api/w/{wid}/diff", params={"tests": "false"}).json()
    assert _paths(data["files"]) == {"a.txt", "b.txt", "c.txt", "src/thing.go"}
    assert set(data["review"]) == set(data["shas"]) == _paths(data["files"])
    assert "test_a.py" not in data["diff"] and "thing_test.go" not in data["diff"]
    assert data["hidden_tests"] == {"files": 3, "added": 4, "removed": 0}


def test_api_hidden_tests_reports_a_range_with_none(
    with_tests: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    data = client.get(f"/api/w/{wid}/diff", params={"start": "HEAD~3", "end": "HEAD~2", "tests": "false"}).json()
    assert _paths(data["files"]) == {"c.txt"}
    assert data["hidden_tests"] == {"files": 0, "added": 0, "removed": 0}


def test_with_and_without_tests_do_not_share_a_cache_entry(
    with_tests: dict[str, Path], client: TestClient, config: DiffwebConfig
) -> None:
    wid = wt_id(config, "proj")
    params = {"start": "HEAD~2", "end": "HEAD"}  # commit to commit, so cached
    first = client.get(f"/api/w/{wid}/diff", params=params).json()["diff"]
    second = client.get(f"/api/w/{wid}/diff", params={**params, "tests": "false"}).json()["diff"]
    third = client.get(f"/api/w/{wid}/diff", params=params).json()["diff"]
    assert first == third and "test_a.py" in first and "test_a.py" not in second


def test_hide_tests_control_follows_the_config(
    with_tests: dict[str, Path], client: TestClient, config: DiffwebConfig, tmp_path: Path
) -> None:
    wid = wt_id(config, "proj")
    assert 'id="hide-tests"' in client.get(f"/w/{wid}").text

    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data["tests"] = {"paths": []}
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    assert 'id="hide-tests"' not in client.get(f"/w/{wid}").text
    # No patterns means nothing to hide, and nothing to report as hidden.
    body = client.get(f"/api/w/{wid}/diff", params={"tests": "false"}).json()
    assert "test_a.py" in _paths(body["files"]) and body["hidden_tests"] is None


def _with_gh(tmp_path: Path, gh: Path, **features: object) -> None:
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data.setdefault("tools", {})["gh_path"] = str(gh)
    data["features"].update(features)
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()


def test_catalog_chip_shows_when_the_pr_was_opened(
    client: TestClient, config: DiffwebConfig, tmp_path: Path, fake_gh: Path
) -> None:
    _with_gh(tmp_path, fake_gh)
    body = client.get("/").text
    assert "#42" in body
    assert 'class="pr-age"' in body


def test_catalog_chip_shows_no_pr_with_the_age_of_the_check(
    client: TestClient, config: DiffwebConfig, tmp_path: Path
) -> None:
    empty = tmp_path / "gh-empty"
    empty.write_text("#!/bin/sh\nprintf '[]'\n")
    empty.chmod(0o755)
    _with_gh(tmp_path, empty)
    body = client.get("/").text
    assert "No PR" in body
    assert "pr-refresh" in body
    # Seconds, not a fixed 0s: under a loaded parallel run the check itself takes time.
    assert re.search(r'<span class="pr-checked">\d+s</span>', body)


def test_refresh_endpoint_re_asks_and_reports_the_age(
    client: TestClient, config: DiffwebConfig, tmp_path: Path, fake_gh: Path
) -> None:
    _with_gh(tmp_path, fake_gh)
    wid = wt_id(config, "proj")
    body = client.post(f"/api/w/{wid}/pr/refresh").json()
    assert body["pr"]["label"] == "#42"
    assert body["pr"]["status"] == "draft"
    assert body["pr"]["opened_age"]
    assert re.fullmatch(r"\d+s", body["checked_age"])


def test_refresh_endpoint_404s_when_pr_links_are_off(
    client: TestClient, config: DiffwebConfig, tmp_path: Path, fake_gh: Path
) -> None:
    _with_gh(tmp_path, fake_gh, pr_links=False)
    assert client.post(f"/api/w/{wt_id(config, 'proj')}/pr/refresh").status_code == 404


def test_stale_check_reports_a_growing_age(
    client: TestClient, config: DiffwebConfig, tmp_path: Path
) -> None:
    """A gh that has not been reachable for days must say so, not lie fresh."""
    missing = tmp_path / "gh-gone"
    _with_gh(tmp_path, missing)
    from diffweb import app as app_module

    state = app_module.get_state()
    wid = wt_id(config, "proj")
    state.save_pr(wid, "feature", None, time.time() - 2 * 86400)
    assert '<span class="pr-checked">2d</span>' in client.get("/").text


# --- how changed lines are grouped ----------------------------------------

BUN = shutil.which("bun")
D2H_CORE = Path(__file__).parent.parent / "diffweb" / "static" / "vendor" / "diff2html.min.js"
needs_bun = pytest.mark.skipif(
    not BUN or not D2H_CORE.exists(),
    reason="needs bun and the vendored diff2html core (poe fetch-diffweb-assets)",
)

TWO_FOR_TWO = "\n".join([
    "diff --git a/f.txt b/f.txt",
    "--- a/f.txt",
    "+++ b/f.txt",
    "@@ -1,3 +1,3 @@",
    " unchanged",
    "-the quick brown fox",
    "-second old line",
    "+the quick red fox",
    "+second new line",
    "",
])


def render_order(diff: str, *flags: str) -> str:
    """One character per rendered row: - deletion, + insertion, . context."""
    argv = [BUN, str(Path(__file__).parent / "render_order.ts"), *flags]
    proc = subprocess.run(argv, input=diff, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@needs_bun
@pytest.mark.parametrize("side_by_side", [False, True])
def test_deletions_are_grouped_before_insertions(side_by_side: bool) -> None:
    """Git emits -,-,+,+; the page must not interleave them back to -,+,-,+."""
    flags = ["--side-by-side"] if side_by_side else []
    order = render_order(TWO_FOR_TWO, *flags).replace(".", "")
    assert order == "--++", f"expected a block of deletions then insertions, got {order!r}"


@needs_bun
def test_grouping_survives_a_longer_run() -> None:
    diff = "\n".join([
        "diff --git a/f.txt b/f.txt",
        "--- a/f.txt",
        "+++ b/f.txt",
        "@@ -1,7 +1,7 @@",
        " keep",
        *[f"-old {i}" for i in range(3)],
        *[f"+new {i}" for i in range(3)],
        "",
    ])
    assert render_order(diff).replace(".", "") == "---+++"


@needs_bun
def test_word_level_highlighting_is_not_lost_by_the_grouping() -> None:
    """Grouping must not cost the inline word diffs - they are the reason the
    interleaving option was there in the first place."""
    assert int(render_order(TWO_FOR_TWO, "--count-inline")) >= 4


# --- the diff icon --------------------------------------------------------


def test_favicon_is_a_red_and_green_square(client: TestClient) -> None:
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    body = r.text
    assert "#e5484d" in body and "#2ea043" in body  # removals red, additions green
    assert "<rect" in body and "rx=" in body        # a square, slightly rounded


@pytest.mark.parametrize("page", ["catalog", "diff"])
def test_pages_carry_the_icon(client: TestClient, config: DiffwebConfig, page: str) -> None:
    url = "/" if page == "catalog" else f"/w/{wt_id(config, 'proj')}"
    body = client.get(url).text
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg?v=' in body
    assert 'class="mark" src="/static/favicon.svg?v=' in body


BUNDLE = Path(__file__).parent.parent / "diffweb" / "static" / "dist" / "worktree.js"


@pytest.mark.skipif(not BUNDLE.exists(), reason="needs the built page scripts (poe diffweb-build)")
def test_static_urls_are_stamped_with_the_file_mtime(client: TestClient, config: DiffwebConfig) -> None:
    """A stale cached worktree.js silently disables anything new in the UI."""
    body = client.get(f"/w/{wt_id(config, 'proj')}").text
    stamp = int(BUNDLE.stat().st_mtime)
    assert f'src="/static/dist/worktree.js?v={stamp}"' in body
    assert client.get(f"/static/dist/worktree.js?v={stamp}").status_code == 200


def test_favicon_ico_is_answered_not_404(client: TestClient) -> None:
    """Browsers ask for /favicon.ico whatever the link tag says."""
    r = client.get("/favicon.ico", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/static/favicon.svg"


def test_diff_endpoint_runs_git_diff_once(
    client: TestClient, config: DiffwebConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sizing the diff used to cost a second, identical `git diff`."""
    from diffweb import app as app_module

    app_module.reset_for_tests()
    calls: list[tuple] = []
    real = gitio.diff_text

    def counted(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(gitio, "diff_text", counted)
    assert client.get(f"/api/w/{wt_id(config, 'proj')}/diff").status_code == 200
    assert len(calls) == 1, f"expected one git diff, got {len(calls)}"


def test_oversized_diffs_are_not_cached(
    client: TestClient, config: DiffwebConfig, world: dict[str, Path], tmp_path: Path
) -> None:
    """A diff too big to inline must not be pinned in the cache either."""
    p = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(p.read_text())
    data["limits"] = {"max_inline_diff_bytes": 10}
    p.write_text(yaml.safe_dump(data))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    wid = wt_id(config, "proj")
    head = git(world["repo"], "rev-parse", "HEAD").strip()
    body = client.get(f"/api/w/{wid}/diff", params={"end": head}).json()
    assert body["lazy"] is True and body["diff"] == ""
    assert app_module._diff_cache == {}


def test_shot_command_exists_and_is_documented() -> None:
    """The screenshot step is a required part of handing work back, so the
    command it names must actually be there."""
    root = Path(__file__).parent.parent
    assert (root / "diffweb" / "shot.ts").exists()
    assert "diffweb-shot" in (root / "pyproject.toml").read_text()
    readme = (root / "diffweb" / "README.md").read_text()
    for command in ("poe diffweb-test", "poe diffweb-shot", "poe fetch-diffweb-assets"):
        assert command in readme, command
    # Feedback on the tooling has somewhere to go, and the docs point at it.
    assert (root / "diffweb" / "TOOLING_ROADMAP.md").exists()
    assert "TOOLING_ROADMAP.md" in readme
    assert "TOOLING_ROADMAP.md" in (root / "diffweb" / "shot.ts").read_text()


def test_state_survives_concurrent_access(tmp_path: Path) -> None:
    """One sqlite connection is shared by the request pool and by background PR
    refreshes; interleaved calls used to raise "bad parameter or other API
    misuse" and 500 the catalog every few hundred page loads."""
    import concurrent.futures

    st = State(tmp_path / "concurrent.db")

    def hammer(n: int) -> None:
        for i in range(40):
            st.set_base_ref(f"wt{n}", f"origin/b{i}")
            st.base_ref(f"wt{n}")
            st.all_base_refs()
            st.save_pr(f"wt{n}", "feature", {"number": i}, 1.0 * i)
            st.pr_record(f"wt{n}", "feature")
            st.mark_reviewed(f"wt{n}", f"f{i}.txt", f"sha{i}")
            st.reviewed(f"wt{n}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        # .result() so an exception in any worker fails the test.
        for future in [pool.submit(hammer, n) for n in range(8)]:
            future.result()

    assert len(st.all_base_refs()) == 8
def test_diff_response_carries_a_profile_id(client: TestClient, config: DiffwebConfig) -> None:
    body = client.get(f"/api/w/{wt_id(config, 'proj')}/diff").json()
    assert len(body["profile_id"]) == 32


def test_profile_is_written_only_once_the_page_reports_back(
    client: TestClient, config: DiffwebConfig, tmp_path: Path
) -> None:
    log = config.profile_log_path()
    profile_id = client.get(f"/api/w/{wt_id(config, 'proj')}/diff").json()["profile_id"]
    assert not log.exists(), "the server half alone is not a profile"

    r = client.post("/api/profile", json={"profile_id": profile_id, "fetch_ms": 12.5, "render_ms": 30})
    assert r.json() == {"ok": True}

    (record,) = profiling.read(log)
    assert record["worktree"] == "proj"
    assert record["branch"] == "feature"
    assert record["files_count"] >= 1
    assert record["diff_bytes"] > 0
    assert record["total_ms"] == 42.5
    assert set(record["stages"]) == set(profiling.SERVER_STAGES)
    assert all(ms >= 0 for ms in record["stages"].values())


def test_profile_report_with_an_unknown_id_is_ignored(client: TestClient, config: DiffwebConfig) -> None:
    r = client.post("/api/profile", json={"profile_id": "nope", "fetch_ms": 1, "render_ms": 1})
    assert r.json() == {"ok": False}
    assert not config.profile_log_path().exists()


def test_profiles_page_lists_recent_profiles_newest_first(
    client: TestClient, config: DiffwebConfig
) -> None:
    for render_ms in (10, 20):
        profile_id = client.get(f"/api/w/{wt_id(config, 'proj')}/diff").json()["profile_id"]
        client.post("/api/profile", json={"profile_id": profile_id, "fetch_ms": 5, "render_ms": render_ms})

    html = client.get("/profiles").text
    assert html.index("25</strong>") < html.index("15</strong>")
    assert "2 recent" in html
    assert "stagebar" in html


def test_profiles_page_is_empty_without_profiles(client: TestClient, config: DiffwebConfig) -> None:
    assert "No profiles yet" in client.get("/profiles").text


def test_catalog_links_to_profiles(client: TestClient, config: DiffwebConfig) -> None:
    assert '/profiles"' in client.get("/").text


def test_profiling_can_be_disabled(
    world: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_path / "off.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "roots": [f"{world['root']}/proj"],
                "state_db": str(tmp_path / "state.db"),
                "profile_log": str(tmp_path / "profiles.jsonl"),
                "features": {"profiling": False, "pr_links": False},
            }
        )
    )
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg_path))
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        assert "profile_id" not in c.get(f"/api/w/{wt_id(load_config(), 'proj')}/diff").json()
        assert c.get("/profiles").status_code == 404
        assert c.post("/api/profile", json={}).status_code == 404
        assert "/profiles" not in c.get("/").text
    app_module.reset_for_tests()


def test_disabled_timer_records_nothing() -> None:
    timer = profiling.Timer(enabled=False)
    with timer.stage("refs"):
        pass
    assert timer.stages == {} and timer.total_ms == 0


def test_profile_log_is_capped(tmp_path: Path) -> None:
    log = tmp_path / "profiles.jsonl"
    for i in range(12):
        profiling.append(log, {"total_ms": i}, keep=5)
    assert [r["total_ms"] for r in profiling.read(log)] == [11, 10, 9, 8, 7]


def test_profile_log_skips_a_corrupt_line(tmp_path: Path) -> None:
    log = tmp_path / "profiles.jsonl"
    profiling.append(log, {"total_ms": 1})
    log.write_text(log.read_text() + "{ not json\n")
    assert [r["total_ms"] for r in profiling.read(log)] == [1]


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [([], 0.5, 0), ([7], 0.95, 7), ([1, 2, 3], 0.5, 2), ([1, 2, 3, 4, 100], 0.95, 100)],
)
def test_quantile(values: list[float], q: float, expected: float) -> None:
    assert profiling.quantile(values, q) == expected


def test_segments_cover_the_whole_wait() -> None:
    record = {
        "stages": {"refs": 1, "numstat": 2, "shas": 1, "diff": 6, "serialise": 0},
        "fetch_ms": 20,
        "render_ms": 30,
        "total_ms": 50,
    }
    segs = profiling.segments(record)
    assert [s["name"] for s in segs] == ["refs", "numstat", "shas", "diff", "network", "render"]
    # network is the fetch time the server did not account for.
    assert dict((s["name"], s["ms"]) for s in segs)["network"] == 10
    assert round(sum(s["pct"] for s in segs)) == 100


def test_segments_of_an_untimed_record_are_empty() -> None:
    assert profiling.segments({"total_ms": 0}) == []


REMERGE_PATCH = "\n".join([
    "diff --git a/a.txt b/a.txt",
    "remerge CONFLICT (content): Merge conflict in a.txt",
    "index 97af53a..755550f 100644",
    "--- a/a.txt",
    "+++ b/a.txt",
    "@@ -1,7 +1,3 @@",
    " one",
    "-<<<<<<< 61e83c5 (feat)",
    "-TWO",
    "-=======",
    "-two-side",
    "->>>>>>> 45ba0c3 (side)",
    "+MERGED",
    " three",
    "",
])


@needs_bun
def test_remerge_patch_renders_despite_the_conflict_header_line() -> None:
    """git puts a `remerge CONFLICT` line in the extended header, which diff2html
    has never heard of. It must be skipped, not mistaken for the patch."""
    assert render_order(REMERGE_PATCH) == "..-----+."


# --- images ---------------------------------------------------------------

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\xa1\x8e\x8c\x00\x00\x00\x00IEND\xaeB`\x82"
)


def add_images(world: dict[str, Path]) -> dict[str, Path]:
    """On the feature branch: a committed then re-committed png, a renamed png,
    a staged edit and an unstaged edit, so every end of a range has a picture."""
    repo = world["repo"]
    (repo / "logo.png").write_bytes(PNG_1PX)
    (repo / "pics").mkdir()
    (repo / "pics" / "old.png").write_bytes(PNG_1PX)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "add images")
    (repo / "logo.png").write_bytes(PNG_1PX + b"v2")
    git(repo, "mv", "pics/old.png", "pics/new.png")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "change images")
    (repo / "logo.png").write_bytes(PNG_1PX + b"staged")
    git(repo, "add", "logo.png")
    (repo / "logo.png").write_bytes(PNG_1PX + b"disk")
    return world


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("a.png", "a.png"),
        ("img/{keep.png => moved.png}", "img/moved.png"),
        ("{old => new}/x.png", "new/x.png"),
        ("old.png => new.png", "new.png"),
    ],
)
def test_rename_target(name: str, target: str) -> None:
    assert gitio.rename_target(name) == target


def test_is_image_by_extension() -> None:
    assert gitio.is_image("a/b.PNG") and gitio.is_image("logo.svg") and gitio.is_image("x.webp")
    assert not gitio.is_image("a.txt") and not gitio.is_image("Makefile")


def test_diff_rows_carry_image_status_and_old_path(client: TestClient, config: DiffwebConfig, world: dict[str, Path]) -> None:
    add_images(world)
    from diffweb import app as app_module

    app_module.reset_for_tests()
    data = client.get(f"/api/w/{wt_id(config, 'proj')}/diff", params={"start": "HEAD~1", "end": "HEAD"}).json()
    rows = {f["path"]: f for f in data["files"]}
    assert rows["logo.png"]["image"] is True and rows["logo.png"]["binary"] is True
    assert rows["logo.png"]["status"] == "M" and rows["logo.png"]["old_path"] == "logo.png"
    moved = rows["pics/{old.png => new.png}"]
    assert moved["status"] == "R" and moved["old_path"] == "pics/old.png" and moved["image"] is True
    # Text rows are unaffected apart from the two new keys.
    whole = client.get(f"/api/w/{wt_id(config, 'proj')}/diff").json()
    a = next(f for f in whole["files"] if f["path"] == "a.txt")
    assert a["image"] is False and a["status"] == "M" and a["old_path"] == "a.txt"


def test_blob_serves_each_end_of_a_range(client: TestClient, config: DiffwebConfig, world: dict[str, Path]) -> None:
    add_images(world)
    from diffweb import app as app_module

    app_module.reset_for_tests()
    wt = wt_id(config, "proj")
    head = git(world["repo"], "rev-parse", "HEAD").strip()

    r = client.get(f"/api/w/{wt}/blob", params={"rev": head, "path": "logo.png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content == PNG_1PX + b"v2"
    assert "immutable" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"

    r = client.get(f"/api/w/{wt}/blob", params={"rev": "HEAD~1", "path": "pics/old.png"})
    assert r.status_code == 200 and r.content == PNG_1PX
    assert r.headers["cache-control"] == "no-store"  # HEAD~1 moves; only a sha is pinned

    r = client.get(f"/api/w/{wt}/blob", params={"rev": INDEX, "path": "logo.png"})
    assert r.status_code == 200 and r.content == PNG_1PX + b"staged"
    r = client.get(f"/api/w/{wt}/blob", params={"rev": WORKTREE, "path": "logo.png"})
    assert r.status_code == 200 and r.content == PNG_1PX + b"disk"
    assert r.headers["cache-control"] == "no-store"


def test_blob_refuses_what_it_should(client: TestClient, config: DiffwebConfig, world: dict[str, Path], tmp_path: Path) -> None:
    add_images(world)
    from diffweb import app as app_module

    app_module.reset_for_tests()
    wt = wt_id(config, "proj")
    # Not an image: never served, even though it exists.
    assert client.get(f"/api/w/{wt}/blob", params={"rev": WORKTREE, "path": "a.txt"}).status_code == 415
    # Absent at that rev.
    assert client.get(f"/api/w/{wt}/blob", params={"rev": "HEAD~1", "path": "pics/new.png"}).status_code == 404
    # Reaching outside the worktree, by path or by symlink.
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    assert client.get(f"/api/w/{wt}/blob", params={"rev": WORKTREE, "path": "../secret.png"}).status_code == 404
    (world["repo"] / "link.png").symlink_to(tmp_path / "secret.png")
    assert client.get(f"/api/w/{wt}/blob", params={"rev": WORKTREE, "path": "link.png"}).status_code == 404
    # Option-shaped inputs never reach git.
    assert client.get(f"/api/w/{wt}/blob", params={"rev": "--output=x", "path": "logo.png"}).status_code == 400
    assert client.get(f"/api/w/{wt}/blob", params={"rev": WORKTREE, "path": "--x.png"}).status_code == 400


# --- PR chip colours --------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "draft", "status", "colour"),
    [
        ("OPEN", False, "open", "var(--add)"),
        ("OPEN", True, "draft", "var(--dim)"),
        ("MERGED", False, "merged", "#8957e5"),
        ("CLOSED", False, "closed", "var(--del)"),
    ],
)
def test_pr_chip_status_has_a_colour(state: str, draft: bool, status: str, colour: str) -> None:
    """gh's state becomes the chip's class, and every class is styled: merged
    purple, closed red, open green, draft grey."""
    pr = forge.PullRequest(number=1, title="t", url="u", state=state, draft=draft)
    assert pr.status == status
    css = (Path(__file__).parent.parent / "diffweb" / "static" / "app.css").read_text()
    assert re.search(rf"\.pr-{status}\s*\{{\s*color:\s*{re.escape(colour)}", css), f".pr-{status} is not {colour}"
