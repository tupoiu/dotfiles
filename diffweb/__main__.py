"""Entry point: `python -m diffweb` (host/port come from the YAML config)."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

from .config import load_config


BUNDLE = Path(__file__).parent / "static" / "dist" / "worktree.js"


def main() -> None:
    cfg = load_config()
    reload = "--reload" in sys.argv[1:]
    if not BUNDLE.exists():
        # The pages would load, then do nothing: their scripts are built from web/*.ts.
        print("diffweb: page scripts not built; run `poe diffweb-build` (or `poe diffweb`)", file=sys.stderr)
    print(f"diffweb on http://{cfg.server.host}:{cfg.server.port}", flush=True)
    uvicorn.run("diffweb.app:app", host=cfg.server.host, port=cfg.server.port, reload=reload)


if __name__ == "__main__":
    main()
