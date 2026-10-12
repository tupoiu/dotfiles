"""Entry point: `diffweb` (installed) or `python -m diffweb` (from the tree).

Host and port come from the YAML config; the flags override it for one run.
"""

from __future__ import annotations

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .config import ENV_VAR, PORT_ENV_VAR, ROOTS_ENV_VAR, DiffwebConfig, config_path, load_config


BUNDLE = Path(__file__).parent / "static" / "dist" / "worktree.js"


def _version() -> str:
    try:
        return version("diffweb")
    except PackageNotFoundError:
        return "from source"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="diffweb",
        description="A local web UI for reviewing your own work across git worktrees.",
        epilog=f"Config: ${ENV_VAR}, else ~/.config/diffweb.yaml. Absent means defaults.",
    )
    parser.add_argument("--config", metavar="PATH", help="the YAML config to use")
    parser.add_argument("--port", type=int, help="the port to serve on (default 8765)")
    parser.add_argument(
        "--root",
        action="append",
        metavar="GLOB",
        help="a directory glob to look for worktrees in; repeat for more (default ~/code, ~/code.*, ~/worktrees/*/*)",
    )
    parser.add_argument("--print-config", action="store_true", help="print the config in effect as YAML, then exit")
    parser.add_argument("--reload", action="store_true", help="restart when the Python code changes (development)")
    parser.add_argument("--version", action="version", version=f"diffweb {_version()}")
    return parser.parse_args(argv)


def apply_args(args: argparse.Namespace) -> None:
    """Pass the flags on as environment variables, which load_config reads.

    The uvicorn worker loads the config again (and in a new process when
    reloading), so the environment is the one place both can see.
    """
    if args.config:
        os.environ[ENV_VAR] = os.path.abspath(os.path.expanduser(args.config))
    if args.port is not None:
        os.environ[PORT_ENV_VAR] = str(args.port)
    if args.root:
        os.environ[ROOTS_ENV_VAR] = os.pathsep.join(args.root)


def config_yaml(cfg: DiffwebConfig) -> str:
    import yaml

    return yaml.safe_dump(cfg.model_dump(), sort_keys=False)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    apply_args(args)
    cfg = load_config()
    if args.print_config:
        print(f"# {config_path()}{'' if config_path().exists() else ' (absent: defaults)'}")
        print(config_yaml(cfg), end="")
        return

    import uvicorn

    if not BUNDLE.exists():
        # The pages would load, then do nothing: their scripts are built from web/*.ts.
        print("diffweb: page scripts not built; run `poe diffweb-build` (or `poe diffweb`)", file=sys.stderr)
    print(f"diffweb on http://{cfg.server.host}:{cfg.server.port}", flush=True)
    uvicorn.run("diffweb.app:app", host=cfg.server.host, port=cfg.server.port, reload=args.reload)


if __name__ == "__main__":
    main()
