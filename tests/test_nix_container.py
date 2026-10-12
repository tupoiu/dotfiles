import subprocess
import pytest

IMAGE = "claude-nix"


def _image_exists() -> bool:
    try:
        return subprocess.run(
            ["podman", "image", "exists", IMAGE], capture_output=True
        ).returncode == 0
    except FileNotFoundError:
        return False


pytestmark = pytest.mark.skipif(
    not _image_exists(), reason=f"{IMAGE} not built (poe build-nix)"
)


def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", "run", "--rm", IMAGE, "bash", "-c", cmd],
        capture_output=True,
        text=True,
    )


def test_pi_runs() -> None:
    result = run("pi --version")
    assert result.returncode == 0, result.stderr
