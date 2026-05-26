import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "bash-helpers" / "claude-jj-checkpoint.sh"

@pytest.fixture()
def env(tmp_path):
    """Temp workdir with a real jj repo."""
    subprocess.run(["jj", "git", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def run_script(input_json: str, env_path: Path) -> Path:
    subprocess.run(
        ["bash", str(SCRIPT)],
        input=input_json,
        text=True,
        check=True,
        cwd=env_path,
    )
    return env_path / ".jj_checkpoints.jsonl"


def parse_entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_writes_valid_json(env):
    out = run_script('{"prompt": "hello"}', env)
    entry = json.loads(out.read_text())
    assert entry["prompt"] == "hello"


def test_output_is_jsonl(env):
    run_script('{"prompt": "first"}', env)
    run_script('{"prompt": "second"}', env)
    lines = (env / ".jj_checkpoints.jsonl").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # each line must be valid JSON on its own


def test_captures_op_id_from_jj(env):
    out = run_script('{"prompt": "x"}', env)
    entry = json.loads(out.read_text())
    assert entry["op_id"] and all(c in "0123456789abcdef" for c in entry["op_id"])


def test_captures_timestamp(env):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    out = run_script('{"prompt": "x"}', env)
    after = datetime.now(timezone.utc)
    ts = datetime.fromisoformat(json.loads(out.read_text())["ts"])
    assert before <= ts <= after


def test_appends_on_multiple_runs(env):
    run_script('{"prompt": "first"}', env)
    run_script('{"prompt": "second"}', env)
    entries = parse_entries(env / ".jj_checkpoints.jsonl")
    assert len(entries) == 2
    assert entries[0]["prompt"] == "first"
    assert entries[1]["prompt"] == "second"


def test_prompt_with_special_characters(env):
    special = 'has "quotes" & <angle> and \\backslash'
    input_json = json.dumps({"prompt": special})
    out = run_script(input_json, env)
    entry = json.loads(out.read_text())
    assert entry["prompt"] == special
