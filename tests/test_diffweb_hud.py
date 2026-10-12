"""diffweb HUD tests.

A fake `gh` answers the two GraphQL queries from canned JSON, so nothing here
touches the network or the developer's GitHub account.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from diffweb.config import DiffwebConfig, load_config
from diffweb.hud import github, service
from diffweb.state import State

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

ME = "me-login"


def _pr(n: int, repo: str = "acme/widgets", branch: str = "feature", draft: bool = False,
        state: str = "OPEN", ready: str | None = None, updated: str = "2026-10-05T10:00:00Z",
        author: str = ME) -> dict:
    return {
        "id": f"PR_{n}",
        "number": n,
        "title": f"PR <b>{n}</b>",
        "url": f"https://github.com/{repo}/pull/{n}",
        "state": state,
        "isDraft": draft,
        "createdAt": "2026-10-01T09:00:00Z",
        "closedAt": "2026-10-06T09:00:00Z" if state != "OPEN" else None,
        "mergedAt": "2026-10-06T09:00:00Z" if state == "MERGED" else None,
        "updatedAt": updated,
        "headRefName": branch,
        "repository": {"nameWithOwner": repo},
        "author": {"login": author},
        "ready": {"nodes": [{"createdAt": ready}] if ready else []},
    }


OVERVIEW = {
    "data": {
        "viewer": {"login": ME},
        "mine": {"issueCount": 3, "nodes": [
            _pr(1), _pr(2, draft=True, branch="other"), _pr(3, repo="acme/gears", branch="x"),
        ]},
        "closed": {"issueCount": 1, "nodes": [_pr(4, state="MERGED")]},
        "review": {"issueCount": 2, "nodes": [
            _pr(5, author="alice", ready="2026-10-03T09:00:00Z"),
            _pr(6, author="bob", draft=True),
        ]},
    }
}

DETAILS = {
    "data": {
        "nodes": [
            {
                "id": "PR_1",
                "reviewDecision": "CHANGES_REQUESTED",
                "reviewRequests": {"nodes": [
                    {"requestedReviewer": {"__typename": "User", "login": "carol"}},
                    {"requestedReviewer": {"__typename": "Team", "slug": "core",
                                           "organization": {"login": "acme"}}},
                ]},
                "activity": {"nodes": [
                    {"__typename": "PullRequestCommit",
                     "commit": {"committedDate": "2026-10-05T09:00:00Z", "author": {"user": {"login": ME}}}},
                    {"__typename": "PullRequestReview", "state": "CHANGES_REQUESTED",
                     "submittedAt": "2026-10-05T10:00:00Z", "author": {"__typename": "User", "login": "dave"}},
                    {"__typename": "IssueComment", "createdAt": "2026-10-05T11:00:00Z",
                     "author": {"__typename": "Bot", "login": "ci"}},
                ]},
            },
            {"id": "PR_2", "reviewDecision": None, "reviewRequests": {"nodes": []},
             "activity": {"nodes": []}},
        ]
    }
}


@pytest.fixture
def fake_gh(tmp_path: Path) -> Path:
    """Answers `gh api graphql --input -` by looking at which query it got.

    Each call is logged as one line, `overview` or `details`, so tests can count
    them. `gh-fail` next to it makes every call fail like an offline gh.
    """
    (tmp_path / "overview.json").write_text(json.dumps(OVERVIEW))
    (tmp_path / "details.json").write_text(json.dumps(DETAILS))
    script = tmp_path / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"here = {str(tmp_path)!r}\n"
        "body = json.load(sys.stdin)\n"
        "kind = 'details' if 'nodes(ids' in body['query'] else 'overview'\n"
        "open(os.path.join(here, 'gh-calls'), 'a').write(kind + ' ' + json.dumps(body['variables']) + '\\n')\n"
        "if os.path.exists(os.path.join(here, 'gh-fail')):\n"
        "    print('error connecting to api.github.com', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "print(open(os.path.join(here, kind + '.json')).read())\n"
    )
    script.chmod(0o755)
    return script


def calls(tmp_path: Path, kind: str | None = None) -> list[str]:
    log = tmp_path / "gh-calls"
    lines = log.read_text().splitlines() if log.exists() else []
    return [line for line in lines if kind is None or line.startswith(kind + " ")]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A local checkout of acme/widgets on the branch that PR 1 is from."""
    path = tmp_path / "widgets"
    git(tmp_path, "init", "-b", "feature", str(path))
    (path / "a.txt").write_text("a\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-m", "initial")
    git(path, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    return path


def write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gh: Path | None, **extra) -> DiffwebConfig:
    data = {
        "roots": [str(tmp_path / "widgets")],
        "state_db": str(tmp_path / "state.db"),
        "profile_log": str(tmp_path / "profiles.jsonl"),
        "features": {"live_reload": False, "pr_links": False},
        "tools": {"gh_path": str(fake_gh or tmp_path / "no-gh")},
        "hud": {"repos": ["acme/widgets", "acme/gears"]},
    }
    for key, value in extra.items():
        data[key] = value
    path = tmp_path / "diffweb.yaml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("DIFFWEB_CONFIG", str(path))
    return load_config()


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gh: Path, repo: Path) -> DiffwebConfig:
    return write_config(tmp_path, monkeypatch, fake_gh)


@pytest.fixture
def client(config: DiffwebConfig):
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        yield c
    app_module.reset_for_tests()


# --- config ---------------------------------------------------------------


def test_hud_is_on_only_when_repos_are_set() -> None:
    assert not DiffwebConfig().hud_enabled()
    assert DiffwebConfig.model_validate({"hud": {"repos": ["a/b"]}}).hud_enabled()
    assert not DiffwebConfig.model_validate({"hud": {"repos": ["a/b"]}, "features": {"hud": False}}).hud_enabled()


@pytest.mark.parametrize("bad", ["widgets", "acme/widgets is:closed", "a/b/c", ""])
def test_hud_repos_must_be_owner_slash_name(bad: str) -> None:
    # They are pasted into a search string, so anything else could widen it.
    with pytest.raises(ValidationError):
        DiffwebConfig.model_validate({"hud": {"repos": [bad]}})


# --- queries --------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "cutoff"),
    [
        (date(2026, 10, 7), date(2026, 10, 2)),  # Wed -> Fri before
        (date(2026, 10, 5), date(2026, 9, 30)),  # Mon -> Wed before
        (date(2026, 10, 10), date(2026, 10, 7)),  # Sat -> Wed
        (date(2026, 10, 9), date(2026, 10, 6)),  # Fri -> Tue
    ],
)
def test_workday_cutoff_skips_weekends(today: date, cutoff: date) -> None:
    assert github.workday_cutoff(today, 3) == cutoff


def test_search_strings_scope_to_the_repos() -> None:
    q = github.search_strings(["acme/widgets", "acme/gears"], date(2026, 10, 2))
    for text in q.values():
        assert "repo:acme/widgets repo:acme/gears" in text
    assert "is:open author:@me" in q["mine"]
    assert "closed:>=2026-10-02" in q["closed"]
    assert "user-review-requested:@me" in q["review"]


def test_overview_parses_the_three_sections(fake_gh: Path, tmp_path: Path) -> None:
    found = github.overview(["acme/widgets"], date(2026, 10, 2), 50, str(fake_gh), 10)
    assert found["viewer"] == ME
    mine = found["sections"]["mine"]
    assert mine["total"] == 3
    assert [p["status"] for p in mine["prs"]] == ["open", "draft", "open"]
    assert found["sections"]["closed"]["prs"][0]["status"] == "merged"
    review = found["sections"]["review"]["prs"]
    # Ready for review when it left draft; a never-draft PR was ready when opened.
    assert review[0]["ready_at"] == github.timestamp("2026-10-03T09:00:00Z")
    assert review[1]["ready_at"] == review[1]["created_at"]
    sent = json.loads(calls(tmp_path, "overview")[0].split(" ", 1)[1])
    assert sent["limit"] == 50 and "repo:acme/widgets" in sent["mine"]


def test_details_parse_reviewers_and_activity(fake_gh: Path) -> None:
    found = github.details(["PR_1", "PR_2"], ME, str(fake_gh), 10)
    one = found["PR_1"]
    assert one["decision"] == "CHANGES_REQUESTED"
    assert one["pending"] == [{"kind": "user", "name": "carol"}, {"kind": "team", "name": "acme/core"}]
    # The bot comment is the latest thing that happened...
    assert one["last_activity"]["actor"] == "ci"
    # ...but it is dave's review, after my last commit, that waits on me.
    assert one["needs_reply"] is True
    assert found["PR_2"]["last_activity"] is None and found["PR_2"]["needs_reply"] is False


def _item(kind: str, actor: str, at: float, state: str | None = None, bot: bool = False) -> dict:
    return {"kind": kind, "actor": actor, "at": at, "state": state, "bot": bot}


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([_item("comment", "alice", 1), _item("commit", ME, 2)], False),
        ([_item("commit", ME, 1), _item("comment", "alice", 2)], True),
        ([_item("commit", ME, 1), _item("review", "alice", 2, "APPROVED")], False),
        ([_item("commit", ME, 1), _item("review", "alice", 2, "COMMENTED")], True),
        ([_item("commit", ME, 1), _item("commit", "alice", 2)], False),
        ([_item("comment", ME, 1), _item("comment", "ci", 2, bot=True)], False),
        ([], False),
    ],
)
def test_needs_reply(items: list[dict], expected: bool) -> None:
    assert github.needs_reply(items, ME) is expected


def test_a_failing_gh_raises(fake_gh: Path, tmp_path: Path) -> None:
    (tmp_path / "gh-fail").touch()
    with pytest.raises(github.GitHubError, match="error connecting"):
        github.overview(["acme/widgets"], date(2026, 10, 2), 50, str(fake_gh), 10)


def test_a_missing_gh_raises(tmp_path: Path) -> None:
    with pytest.raises(github.GitHubError, match="not installed|could not run"):
        github.run_graphql("query { viewer { login } }", {}, str(tmp_path / "no-gh"), 5)


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("git@github.com:acme/widgets.git", "acme/widgets"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets.git/", "acme/widgets"),
        ("ssh://git@github.com/acme/my.repo.git", "acme/my.repo"),
        ("/srv/git/widgets.git", None),
        (None, None),
    ],
)
def test_repo_of_remote_urls(url: str | None, repo: str | None) -> None:
    assert service.repo_of(url) == repo


# --- caching --------------------------------------------------------------


def test_overview_is_cached_until_asked_to_refresh(config: DiffwebConfig, tmp_path: Path) -> None:
    state = State(config.state_db_path())
    first = service.overview(state, config, "cached")
    assert first["data"]["viewer"] == ME and first["error"] is None and not first["stale"]
    service.overview(state, config, "cached")
    service.overview(state, config, "fresh")  # younger than the refresh age
    assert len(calls(tmp_path, "overview")) == 1
    service.overview(state, config, "force")
    assert len(calls(tmp_path, "overview")) == 2


def test_a_stale_answer_is_refreshed_by_fresh(config: DiffwebConfig, tmp_path: Path) -> None:
    state = State(config.state_db_path())
    service.overview(state, config)
    key = "hud:overview"
    payload, _ = state.cached(key)
    state.save_cached(key, payload, time.time() - 3600)
    assert service.overview(state, config, "cached")["stale"] is True
    assert len(calls(tmp_path, "overview")) == 1
    assert service.overview(state, config, "fresh")["stale"] is False
    assert len(calls(tmp_path, "overview")) == 2


def test_a_failed_refresh_keeps_the_old_answer(config: DiffwebConfig, tmp_path: Path) -> None:
    state = State(config.state_db_path())
    service.overview(state, config)
    (tmp_path / "gh-fail").touch()
    got = service.overview(state, config, "force")
    assert "error connecting" in got["error"]
    assert got["data"]["viewer"] == ME


def test_first_lookup_failing_reports_the_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 fake_gh: Path) -> None:
    config = write_config(tmp_path, monkeypatch, fake_gh)
    (tmp_path / "gh-fail").touch()
    got = service.overview(State(config.state_db_path()), config)
    assert got["data"] is None and "error connecting" in got["error"]


def test_changing_repos_asks_again(config: DiffwebConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   fake_gh: Path) -> None:
    state = State(config.state_db_path())
    service.overview(state, config)
    other = write_config(tmp_path, monkeypatch, fake_gh, hud={"repos": ["acme/widgets"]})
    service.overview(state, other)
    assert len(calls(tmp_path, "overview")) == 2


def test_details_are_for_the_stored_overview(config: DiffwebConfig, tmp_path: Path) -> None:
    state = State(config.state_db_path())
    assert service.details(state, config)["data"] is None  # never fetches the overview itself
    assert calls(tmp_path) == []
    service.overview(state, config)
    got = service.details(state, config)
    assert got["data"]["PR_1"]["decision"] == "CHANGES_REQUESTED"
    sent = json.loads(calls(tmp_path, "details")[0].split(" ", 1)[1])
    assert sent["ids"] == sorted(f"PR_{n}" for n in range(1, 7))
    service.details(state, config)
    assert len(calls(tmp_path, "details")) == 1


def test_prs_link_to_a_local_worktree_on_their_branch(config: DiffwebConfig, repo: Path) -> None:
    got = service.overview(State(config.state_db_path()), config)
    by_number = {p["number"]: p for p in got["data"]["sections"]["mine"]["prs"]}
    assert by_number[1]["worktree"]["name"] == "widgets"
    assert by_number[2]["worktree"] is None  # same repo, other branch
    assert by_number[3]["worktree"] is None  # other repo, no checkout


# --- routes ---------------------------------------------------------------


def test_hud_page_and_header_link(client: TestClient) -> None:
    page = client.get("/hud")
    assert page.status_code == 200
    assert 'id="section-mine"' in page.text and "dist/hud/hud.js" in page.text
    assert 'class="topnav current" href="/hud"' in page.text
    # Every page links to it, not only the catalog.
    assert 'href="/hud">HUD</a>' in client.get("/").text


def test_hud_api(client: TestClient, tmp_path: Path) -> None:
    got = client.get("/api/hud").json()
    assert got["data"]["sections"]["review"]["total"] == 2
    details = client.get("/api/hud/details").json()
    assert details["data"]["PR_1"]["needs_reply"] is True
    client.get("/api/hud?mode=force")
    assert len(calls(tmp_path, "overview")) == 2
    assert client.get("/api/hud?mode=bogus").status_code == 422


def test_hud_is_absent_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gh: Path,
                                     repo: Path) -> None:
    write_config(tmp_path, monkeypatch, fake_gh, hud={"repos": []})
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        assert c.get("/hud").status_code == 404
        assert c.get("/api/hud").status_code == 404
        assert 'href="/hud"' not in c.get("/").text
    app_module.reset_for_tests()
    assert calls(tmp_path) == []


def test_hud_page_says_when_features_on_but_no_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     fake_gh: Path, repo: Path) -> None:
    write_config(tmp_path, monkeypatch, fake_gh, hud={"repos": []},
                 features={"hud": True, "live_reload": False, "pr_links": False})
    from diffweb import app as app_module

    app_module.reset_for_tests()
    with TestClient(app_module.app) as c:
        page = c.get("/hud")
    app_module.reset_for_tests()
    assert page.status_code == 200 and "hud.repos" in page.text and "hud.js" not in page.text
