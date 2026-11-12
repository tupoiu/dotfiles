"""diffweb: "hide tests" also hides the unit tests inside a Rust file."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from diffweb import gitio, rust_tests
from diffweb.config import load_config

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}

OLD = """fn a() {}

#[cfg(test)]
mod tests {
    fn t() {}
}
"""

NEW = """fn a() {}
fn b() {}

#[cfg(test)]
mod tests {
    fn t() {}
    fn u() {}
}
"""

ONLY_TESTS = """fn a() {}

#[cfg(test)]
mod tests {
    fn t() {}
    fn v() {}
}
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV}
    ).stdout


def diff_of(old: str, new: str, tmp_path: Path, name: str = "src/lib.rs") -> str:
    """git's own patch between two versions of one file."""
    repo = tmp_path / "d"
    (repo / Path(name).parent).mkdir(parents=True, exist_ok=True)
    git(tmp_path, "init", "-q", str(repo))
    (repo / name).write_text(old)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "old")
    (repo / name).write_text(new)
    return git(repo, "diff", "--no-color")


def side(old: str | None, new: str | None):
    return lambda which, _path: old if which == "old" else new


def test_test_start_follows_loc() -> None:
    assert rust_tests.test_start(OLD) == 3
    assert rust_tests.test_start("fn a() {}\n  mod tests {\n}\n") == 2
    assert rust_tests.test_start("fn a() {}\nmod testsuite;\n") is None
    assert rust_tests.test_start(None) is None


def test_cuts_the_test_region_and_counts_it(tmp_path: Path) -> None:
    text, hidden = rust_tests.hide(diff_of(OLD, NEW, tmp_path), side(OLD, NEW))
    assert "+fn b() {}" in text and "fn u" not in text and "mod tests" not in text
    info = hidden["src/lib.rs"]
    assert (info.added, info.removed, info.whole_file, info.new_start) == (1, 0, False, 4)
    # What is left is a well-formed patch that git agrees with.
    assert "@@ -1,2 +1,3 @@" in text


def test_a_file_with_only_test_changes_goes(tmp_path: Path) -> None:
    text, hidden = rust_tests.hide(diff_of(OLD, ONLY_TESTS, tmp_path), side(OLD, ONLY_TESTS))
    assert text == ""
    assert hidden["src/lib.rs"].whole_file and hidden["src/lib.rs"].added == 1


def test_removed_lines_are_judged_by_the_old_file(tmp_path: Path) -> None:
    # The old file's tests go away entirely: those removals are tests too.
    new = "fn a() {}\nfn c() {}\n"
    text, hidden = rust_tests.hide(diff_of(OLD, new, tmp_path), side(OLD, new))
    assert "+fn c() {}" in text and "-mod tests" not in text and "-    fn t() {}" not in text
    assert hidden["src/lib.rs"].removed == 4 and hidden["src/lib.rs"].new_start is None


def test_other_files_and_rust_without_tests_are_untouched(tmp_path: Path) -> None:
    plain = diff_of("x\n", "y\n", tmp_path, name="notes.txt")
    assert rust_tests.hide(plain, side("x\n", "y\n")) == (plain, {})
    no_tests = "fn a() {}\n"
    patch = diff_of(no_tests, no_tests + "fn b() {}\n", tmp_path / "2")
    assert rust_tests.hide(patch, side(no_tests, no_tests + "fn b() {}\n")) == (patch, {})


# --- through the API -------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A repo whose branch changes lib.rs (code and tests) and only_tests.rs (tests)."""
    repo = tmp_path / "proj"
    git(tmp_path, "init", "-q", "-b", "master", str(repo))
    (repo / "src").mkdir()
    (repo / "src/lib.rs").write_text(OLD)
    (repo / "src/only_tests.rs").write_text(OLD)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "src/lib.rs").write_text(NEW)
    (repo / "src/only_tests.rs").write_text(ONLY_TESTS)
    git(repo, "commit", "-qam", "change")
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
        yield c, wid
    app_module.reset_for_tests()


def test_hide_tests_hides_rust_tests_in_the_page(client) -> None:
    c, wid = client
    params = {"base": "master", "end": "HEAD"}
    everything = c.get(f"/api/w/{wid}/diff", params=params).json()
    assert {f["path"] for f in everything["files"]} == {"src/lib.rs", "src/only_tests.rs"}
    assert "fn u()" in everything["diff"] and everything["tests_from"] == {}

    data = c.get(f"/api/w/{wid}/diff", params={**params, "tests": "false"}).json()
    # only_tests.rs changed only its tests, so it is gone like a test file.
    assert {f["path"]: (f["added"], f["removed"]) for f in data["files"]} == {"src/lib.rs": (1, 0)}
    assert set(data["shas"]) == set(data["review"]) == {"src/lib.rs"}
    assert "fn b()" in data["diff"] and "fn u()" not in data["diff"] and "fn v()" not in data["diff"]
    assert data["hidden_tests"]["in_file"] == {"files": 2, "added": 2, "removed": 0}
    assert data["tests_from"] == {"src/lib.rs": 4, "src/only_tests.rs": 3}

    # A lazy, per-file load is cut the same way.
    one = c.get(f"/api/w/{wid}/diff", params={**params, "tests": "false", "files": ["src/lib.rs"]}).json()
    assert "fn b()" in one["diff"] and "fn u()" not in one["diff"] and one["tests_from"] == {"src/lib.rs": 4}
