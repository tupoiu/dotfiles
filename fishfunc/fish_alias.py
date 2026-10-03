#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["typer"]
# ///
"""Add, test and manage fish aliases in the dotfiles repo.

Run it through the `fish-alias` fish function to update the current shell.
The function sets FISH_ALIAS_ACTIONS to a file. This script writes
`source <path>` or `erase <name>` lines to it. The function runs them.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import typer

REPO = Path(os.environ.get("FISH_ALIAS_REPO") or Path(__file__).resolve().parents[1])
TARGETS = {
    "host": REPO / "fish" / "functions",
    "container": REPO / "container-fish" / "functions",
}

app = typer.Typer(help=__doc__, no_args_is_help=True)


def fish(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["fish", "--no-config", "-c", script, *args],
        capture_output=True,
        text=True,
    )


def emit(action: str) -> None:
    path = os.environ.get("FISH_ALIAS_ACTIONS")
    if path:
        with open(path, "a") as f:
            f.write(action + "\n")


def check_name(name: str) -> str:
    if not re.fullmatch(r"[\w.+-]+", name):
        raise typer.BadParameter(f"not a usable function name: {name!r}")
    return name


def locations(name: str) -> dict[str, Path]:
    found = {}
    for target, folder in TARGETS.items():
        path = folder / f"{name}.fish"
        if path.exists():
            found[target] = path
    return found


def choose(prompt: str, choices: list[str], default: str | None = None) -> str:
    while True:
        answer = typer.prompt(f"{prompt} [{'/'.join(choices)}]", default=default, show_default=False)
        if answer in choices:
            return answer
        typer.echo(f"Pick one of: {', '.join(choices)}")


def pick_targets(prompt: str, options: list[str]) -> list[str]:
    if len(options) == 1:
        return options
    choices = [*options, "both"]
    choice = choose(prompt, choices, default="host")
    return options if choice == "both" else [choice]


def draft(name: str, command: str, description: str) -> str:
    # Fish's own `alias` writes the --wraps and $argv parts.
    script = "alias $argv[1] $argv[2]; or exit 1\n"
    if description:
        script += "functions -d $argv[3] $argv[1]\n"
    script += "functions --no-details $argv[1]"
    result = fish(script, name, command, description)
    if result.returncode != 0:
        typer.secho(result.stderr, fg="red", err=True)
        raise typer.Exit(1)
    lines = [line.rstrip() for line in result.stdout.splitlines()]
    return "\n".join(line for line in lines if line) + "\n"


def has_terminal() -> bool:
    # A process started in a new session (e.g. by poe) has no terminal.
    try:
        os.tcgetpgrp(0)
        return True
    except OSError:
        return False


def install() -> None:
    result = subprocess.run(
        ["bash", str(REPO / "install_fish_functions.sh")],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.secho(
            f"install_fish_functions.sh failed ({result.returncode}):",
            fg="red",
            err=True,
        )
        typer.echo(result.stdout + result.stderr, err=True)
        raise typer.Exit(result.returncode)
    typer.echo("Ran install_fish_functions.sh.")


def save(name: str, text: str) -> None:
    targets = pick_targets("Save to", list(TARGETS))
    for target in targets:
        path = TARGETS[target] / f"{name}.fish"
        if path.exists() and not typer.confirm(f"{path} exists. Overwrite?"):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        typer.echo(f"Wrote {path}")
        if target == "host":
            install()
            emit(f"source {path}")
        else:
            typer.echo("Container aliases apply on the next container start.")


@app.command()
def add(name: str = typer.Argument(None, help="Alias name.")) -> None:
    """Draft an alias, test it in a subshell, then save it."""
    name = check_name(name or typer.prompt("Alias name"))
    existing = fish("type $argv[1]", name)
    if existing.returncode == 0:
        typer.echo(existing.stdout)
        typer.confirm(f"{name} already exists. Continue?", abort=True)

    command = typer.prompt("Command")
    description = typer.prompt("Description", default="", show_default=False)

    fd, tmp = tempfile.mkstemp(suffix=".fish")
    os.close(fd)
    tmp = Path(tmp)
    tmp.write_text(draft(name, command, description))
    try:
        while True:
            typer.secho(f"\n{tmp.read_text()}", fg="cyan")
            action = choose("Test, edit, save or quit", ["t", "e", "s", "q"])
            if action == "t":
                if not has_terminal():
                    typer.secho(
                        "No controlling terminal, so no interactive subshell. "
                        "Run `fish-alias` from an interactive fish.",
                        fg="red",
                    )
                    continue
                typer.echo(f"Subshell with {name} loaded. Type `exit` to return.")
                subprocess.run(["fish", "-C", f"source {tmp}"])
            elif action == "e":
                editor = os.environ.get("EDITOR", "vim")
                subprocess.run([editor, str(tmp)])
                check = subprocess.run(["fish", "-n", str(tmp)])
                if check.returncode != 0:
                    typer.secho("Syntax error. Edit again before saving.", fg="red")
            elif action == "s":
                if subprocess.run(["fish", "-n", str(tmp)]).returncode != 0:
                    typer.secho("Syntax error. Not saved.", fg="red")
                    continue
                save(name, tmp.read_text())
                return
            else:
                raise typer.Exit()
    finally:
        tmp.unlink(missing_ok=True)


def description_of(path: Path) -> str:
    result = fish("source $argv[1]; functions -Dv $argv[2]", str(path), path.stem)
    lines = result.stdout.splitlines()
    # Fish prints "n/a" when there is no description.
    desc = lines[4] if len(lines) > 4 else ""
    return "" if desc == "n/a" else desc


@app.command("list")
def list_() -> None:
    """List aliases in the repo and where they live."""
    names = sorted({p.stem for folder in TARGETS.values() for p in folder.glob("*.fish")})
    rows = []
    for name in names:
        found = locations(name)
        where = "both" if len(found) == 2 else next(iter(found))
        rows.append((name, where, description_of(next(iter(found.values())))))
    if not rows:
        typer.echo("No aliases.")
        return
    width = max(len(r[0]) for r in rows)
    for name, where, desc in rows:
        typer.echo(f"{name:<{width}}  {where:<9}  {desc}")


@app.command()
def show(name: str) -> None:
    """Print an alias's file(s)."""
    found = locations(check_name(name))
    if not found:
        typer.secho(f"No alias named {name}.", fg="red", err=True)
        raise typer.Exit(1)
    for target, path in found.items():
        typer.secho(f"# {target}: {path}", fg="cyan")
        typer.echo(path.read_text())


@app.command()
def rm(name: str) -> None:
    """Delete an alias from the repo and the current shell."""
    found = locations(check_name(name))
    if not found:
        typer.secho(f"No alias named {name}.", fg="red", err=True)
        raise typer.Exit(1)
    targets = pick_targets("Delete from", list(found))
    paths = [found[t] for t in targets]
    typer.confirm(f"Delete {', '.join(map(str, paths))}?", abort=True)
    for target in targets:
        path = found[target]
        if target == "host":
            link = Path.home() / ".config" / "fish" / "functions" / f"{name}.fish"
            if link.is_symlink() and Path(os.readlink(link)) == path.resolve():
                link.unlink()
                typer.echo(f"Removed {link}")
            emit(f"erase {name}")
        path.unlink()
        typer.echo(f"Deleted {path}")


if __name__ == "__main__":
    app()
