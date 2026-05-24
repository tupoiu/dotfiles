import subprocess
import pytest

IMAGE = "playwright-default"


def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", "run", "--rm", IMAGE, "sh", "-c", cmd],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("cmd", [
    "playwright --version",
])
def test_tool_available(cmd: str) -> None:
    result = run(cmd)
    assert result.returncode == 0, f"{cmd!r} failed:\n{result.stderr}"


def test_browsers_path_populated() -> None:
    result = run('[ -n "$(ls -A /ms-playwright)" ]')
    assert result.returncode == 0, "/ms-playwright is empty or missing"


def test_chromium_launches() -> None:
    """Chromium and its OS deps run headless from within the container."""
    script = (
        "NODE_PATH=/usr/local/share/npm-global/lib/node_modules "
        "node -e \""
        "const { chromium } = require('@playwright/test');"
        "chromium.launch().then(async b => {"
        " const p = await b.newPage();"
        " await p.setContent('<title>ok</title>');"
        " console.log(await p.title());"
        " await b.close();"
        "});\""
    )
    result = run(script)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
