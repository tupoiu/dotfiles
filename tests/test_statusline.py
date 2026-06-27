import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "bash-helpers" / "claude-statusline.sh"


def run(payload: dict) -> str:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        input=json.dumps(payload),
        text=True,
        check=True,
        capture_output=True,
    ).stdout


def test_format_and_fields(tmp_path):
    out = run(
        {
            "model": {"display_name": "Opus 4.8", "effort": "medium"},
            "workspace": {"current_dir": str(tmp_path)},
        }
    )
    assert out.startswith("eff:M Opus4.8")
    assert str(tmp_path) in out


def test_effort_levels(tmp_path):
    def eff(level):
        out = run(
            {"model": {"display_name": "X", "effort": level}, "workspace": {"current_dir": str(tmp_path)}}
        )
        return out.split(" ")[0]

    assert eff("low") == "eff:L"
    assert eff("medium") == "eff:M"
    assert eff("high") == "eff:H"


def test_effort_falls_back_to_settings(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"effortLevel": "high"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    out = subprocess.run(
        ["bash", str(SCRIPT)],
        input=json.dumps({"model": {"display_name": "X"}, "workspace": {"current_dir": str(tmp_path)}}),
        text=True,
        check=True,
        capture_output=True,
        env={**__import__("os").environ, "CLAUDE_CONFIG_DIR": str(config)},
    ).stdout
    assert out.startswith("eff:H")


def test_home_collapsed_to_tilde(tmp_path, monkeypatch):
    home = str(Path.home())
    out = run({"model": {"display_name": "X"}, "workspace": {"current_dir": f"{home}/code/dotfiles"}})
    assert out.endswith("~/code/dotfiles")


def test_git_branch(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "my-branch"], cwd=tmp_path, check=True)
    out = run({"model": {"display_name": "X"}, "workspace": {"current_dir": str(tmp_path)}})
    assert "my-branch" in out
