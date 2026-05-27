import subprocess
import pytest

IMAGE = "claude-default"


def run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", "run", "--rm", IMAGE, "sh", "-c", cmd],
        capture_output=True,
        text=True,
    )


def test_tools_available() -> None:
    tools = [
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
    ]
    # Run all checks in one container; emit per-tool results for diagnosis.
    script = "; ".join(
        f'echo "CHECK {cmd.split()[0]}"; {cmd} >/dev/null 2>&1 && echo "OK" || echo "FAIL"'
        for cmd in tools
    )
    result = run(script)
    failures = []
    lines = result.stdout.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("CHECK "):
            tool = line.removeprefix("CHECK ")
            status = lines[i + 1] if i + 1 < len(lines) else "FAIL"
            if status != "OK":
                failures.append(tool)
    assert not failures, f"Tools not available in image: {', '.join(failures)}"


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
