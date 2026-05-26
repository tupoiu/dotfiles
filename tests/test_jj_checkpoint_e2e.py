"""
End-to-end test: run claude -p inside the container and verify the
UserPromptSubmit hook writes a checkpoint entry to .jj_checkpoints.jsonl.
"""

import json
import subprocess
from pathlib import Path

IMAGE = "claude-default"
HELPERS = Path.home() / ".helpers"
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_JSON = Path.home() / ".claude.json"
JJ_CONFIG = Path.home() / ".config" / "jj"


def test_checkpoint_written_on_claude_prompt(tmp_path):
    subprocess.run(["jj", "git", "init"], cwd=tmp_path, check=True, capture_output=True)

    result = subprocess.run(
        [
            "podman", "run", "--rm", "--userns=keep-id",
            "-v", f"{CLAUDE_DIR}:/home/node/.claude",
            "-v", f"{CLAUDE_JSON}:/home/node/.claude.json",
            "-v", f"{tmp_path}:/workspace",
            "-v", f"{JJ_CONFIG}:/home/node/.config/jj",
            "-v", f"{HELPERS}:/home/node/.helpers:ro",
            "-w", "/workspace",
            IMAGE,
            "claude", "-p", "hello",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"claude exited non-zero:\n{result.stderr}"

    checkpoint = tmp_path / ".jj_checkpoints.jsonl"
    assert checkpoint.exists(), ".jj_checkpoints.jsonl was not created"

    entry = json.loads(checkpoint.read_text().splitlines()[0])
    assert entry["prompt"] == "hello"
    assert entry["op_id"]
    assert entry["ts"]
