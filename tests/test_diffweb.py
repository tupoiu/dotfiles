"""diffweb tests.

Everything runs against a throwaway git world under tmp_path; nothing here
reads the developer's real repos, and the suite must pass on a machine with
no ~/code at all.
"""

from __future__ import annotations

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

from diffweb import forge, gitio, profiling
from diffweb.config import DiffwebConfig, Noise, load_config
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
    body = client.get(f"/w/{wt_id(config, 'proj')}").text
    assert "/static/keys.js" in body
    assert 'id="key-help-btn"' in body



def test_hide_reviewed_control_is_present(client: TestClient, config: DiffwebConfig) -> None:
    assert 'id="hide-reviewed"' in client.get(f"/w/{wt_id(config, 'proj')}").text


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
