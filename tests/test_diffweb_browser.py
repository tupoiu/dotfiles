"""Browser tests for the diffweb pages: the behaviour that lives in the page
scripts, which neither pytest's TestClient nor `bun test` can run.

The server runs in-process on a free port, and Chromium drives it. The tests
skip without the built page scripts (poe diffweb-build) or without the browser
(`uv run playwright install chromium`).
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
import yaml

from diffweb import app as app_module
from diffweb import gitio
from diffweb.config import DiffwebConfig, load_config
from test_diffweb import world  # noqa: F401 - a fixture

sync_api = pytest.importorskip("playwright.sync_api")

BUNDLE = Path(__file__).parent.parent / "diffweb" / "static" / "dist" / "worktree.js"
pytestmark = pytest.mark.skipif(not BUNDLE.exists(), reason="needs the built page scripts (poe diffweb-build)")


@pytest.fixture(scope="module")
def browser() -> Iterator[sync_api.Browser]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except sync_api.Error as err:
            pytest.skip(f"no Chromium for Playwright (uv run playwright install chromium): {err}")
        yield b
        b.close()


@pytest.fixture
def lazy_config(world: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DiffwebConfig:  # noqa: F811
    """Every diff loads file by file. No live reload, so the test alone starts renders."""
    cfg_path = tmp_path / "diffweb.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "roots": [f"{world['root']}/proj"],
                "state_db": str(tmp_path / "state.db"),
                "profile_log": str(tmp_path / "profiles.jsonl"),
                "features": {"structural": False, "live_reload": False, "pr_links": False},
                "limits": {"max_inline_files": 0},
            }
        )
    )
    monkeypatch.setenv("DIFFWEB_CONFIG", str(cfg_path))
    return load_config()


@pytest.fixture
def server(lazy_config: DiffwebConfig) -> Iterator[str]:
    app_module.reset_for_tests()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    srv = uvicorn.Server(uvicorn.Config(app_module.app, log_level="warning"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not srv.started:
        if not thread.is_alive():
            raise RuntimeError("diffweb did not start")
        threading.Event().wait(0.01)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    srv.should_exit = True
    thread.join(5)
    app_module.reset_for_tests()


def test_a_stale_lazy_load_does_not_add_files_to_a_newer_render(
    browser: sync_api.Browser, server: str, lazy_config: DiffwebConfig, world: dict[str, Path]  # noqa: F811
) -> None:
    """A render that a newer render replaced stops, and draws nothing more.

    The first render's first per-file load is held. While it is held, c.txt is
    deleted and a second render runs to the end. Then the held load is let go.
    Before the fix, the first render then went on through its own file list
    and added b.txt to the page a second time.
    """
    wt = next(w for w in gitio.discover(lazy_config) if w.name == "proj")
    page = browser.new_page()
    held: list[tuple[sync_api.Route, sync_api.APIResponse]] = []

    def hold_first_file_load(route: sync_api.Route) -> None:
        if "files=" in route.request.url and not held:
            held.append((route, route.fetch()))
        else:
            route.continue_()

    page.route("**/api/w/*/diff?*", hold_first_file_load)
    page.goto(f"{server}/w/{wt.id}")
    page.wait_for_function("() => document.querySelector('#diff') !== null")
    while not held:
        page.wait_for_timeout(10)

    (world["repo"] / "c.txt").unlink()
    page.dispatch_event("#start", "change")  # the second render, as live reload would start it
    page.wait_for_function(
        "() => document.querySelectorAll('#diff [data-path]').length === 2"
        " && document.querySelector('#status').textContent === ''"
    )

    route, response = held[0]
    route.fulfill(response=response)
    # Before the fix, the first render now loads b.txt and c.txt again.
    page.wait_for_timeout(300)

    paths = page.eval_on_selector_all("#diff [data-path]", "els => els.map(e => e.dataset.path)")
    assert paths == ["a.txt", "b.txt"]
    assert page.text_content("#summary").startswith("2 files")
    page.close()


@pytest.mark.parametrize("max_inline_files", [300, 0], ids=["whole", "lazy"])
def test_emphasised_files_come_first(
    browser: sync_api.Browser, server: str, lazy_config: DiffwebConfig, max_inline_files: int, tmp_path: Path
) -> None:
    """git lists a.txt, b.txt, c.txt; with c.txt emphasised, it is drawn first."""
    cfg_path = tmp_path / "diffweb.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["limits"] = {"max_inline_files": max_inline_files}
    data["emphasis"] = [{"label": "watch", "paths": ["c.txt"], "color": "teal"}]
    cfg_path.write_text(yaml.safe_dump(data))
    app_module.reset_for_tests()  # the server reads the new config on its next request

    wt = next(w for w in gitio.discover(lazy_config) if w.name == "proj")
    page = browser.new_page()
    page.goto(f"{server}/w/{wt.id}")
    page.wait_for_function("() => document.querySelectorAll('#diff [data-path]').length === 3")
    paths = page.eval_on_selector_all("#diff [data-path]", "els => els.map(e => e.dataset.path)")
    assert paths == ["c.txt", "a.txt", "b.txt"]
    if max_inline_files:  # the file list only exists when the diff is drawn whole
        listed = page.eval_on_selector_all(".dw-file-list li a", "els => els.map(e => e.textContent)")
        assert listed == ["c.txt", "a.txt", "b.txt"]
    page.close()
