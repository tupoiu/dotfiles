import subprocess
import pytest

IMAGE = "claude-default"


def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", "run", "--rm", IMAGE, "sh", "-c", cmd],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("cmd", [
    "claude --version",
    "git --version",
    "jj --version",
    "gh --version",
    "delta --version",
    "fzf --version",
    "jq --version",
    "fish --version",
    "zsh --version",
    "uv --version",
    "poe --version",
    "vim --version",
    "nano --version",
])
def test_tool_available(cmd: str) -> None:
    result = run(cmd)
    assert result.returncode == 0, f"{cmd!r} failed:\n{result.stderr}"


def test_workspace_dir_exists() -> None:
    result = run("[ -d /workspace ]")
    assert result.returncode == 0


def test_claude_dir_exists() -> None:
    result = run("[ -d /home/node/.claude ]")
    assert result.returncode == 0


def test_prompt_shows_git_status() -> None:
    """Prompt renders git branch info when inside a git repo."""
    script = (
        "git init /tmp/testrepo -b main && "
        "fish -c 'cd /tmp/testrepo; fish_prompt'"
    )
    result = run(script)
    assert result.returncode == 0
    assert "main" in result.stdout, f"branch not visible in rendered prompt: {result.stdout!r}"
