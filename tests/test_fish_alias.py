import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "fishfunc" / "fish_alias.py"


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "fish" / "functions").mkdir(parents=True)
    (repo / "container-fish" / "functions").mkdir(parents=True)
    (repo / "install_fish_functions.sh").write_text('touch "$(dirname "$0")/installed"\n')
    return repo


def run(tmp_path: Path, repo: Path, *args: str, input: str = "") -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "FISH_ALIAS_REPO": str(repo),
        "FISH_ALIAS_ACTIONS": str(tmp_path / "actions"),
        "HOME": str(tmp_path / "home"),
    }
    return subprocess.run(
        [str(SCRIPT), *args],
        input=input,
        text=True,
        capture_output=True,
        env=env,
    )


def actions(tmp_path: Path) -> list[str]:
    return (tmp_path / "actions").read_text().splitlines()


def test_add_host(tmp_path):
    repo = make_repo(tmp_path)
    result = run(tmp_path, repo, "add", "zzgs", input="git status\nShort status\ns\nhost\n")
    assert result.returncode == 0, result.stderr

    path = repo / "fish" / "functions" / "zzgs.fish"
    text = path.read_text()
    assert "--description 'Short status'" in text
    assert "git status $argv" in text
    assert (repo / "installed").exists()
    assert actions(tmp_path) == [f"source {path}"]


def test_add_container_skips_install(tmp_path):
    repo = make_repo(tmp_path)
    result = run(tmp_path, repo, "add", "zzgs", input="git status\n\ns\ncontainer\n")
    assert result.returncode == 0, result.stderr

    assert (repo / "container-fish" / "functions" / "zzgs.fish").exists()
    assert not (repo / "fish" / "functions" / "zzgs.fish").exists()
    assert not (repo / "installed").exists()
    assert not (tmp_path / "actions").exists()


def test_add_quit_writes_nothing(tmp_path):
    repo = make_repo(tmp_path)
    result = run(tmp_path, repo, "add", "zzgs", input="git status\n\nq\n")
    assert result.returncode == 0, result.stderr
    assert not list(repo.rglob("zzgs.fish"))


def test_list_show_rm(tmp_path):
    repo = make_repo(tmp_path)
    run(tmp_path, repo, "add", "zzgs", input="git status\nShort status\ns\nboth\n")
    host = repo / "fish" / "functions" / "zzgs.fish"
    link_dir = tmp_path / "home" / ".config" / "fish" / "functions"
    link_dir.mkdir(parents=True)
    (link_dir / "zzgs.fish").symlink_to(host)
    (tmp_path / "actions").unlink()

    listed = run(tmp_path, repo, "list").stdout
    assert "zzgs" in listed and "both" in listed and "Short status" in listed

    shown = run(tmp_path, repo, "show", "zzgs").stdout
    assert "# host:" in shown and "# container:" in shown

    result = run(tmp_path, repo, "rm", "zzgs", input="both\ny\n")
    assert result.returncode == 0, result.stderr
    assert not list(repo.rglob("zzgs.fish"))
    assert not (link_dir / "zzgs.fish").is_symlink()
    assert actions(tmp_path) == ["erase zzgs"]
