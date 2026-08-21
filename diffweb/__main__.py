"""Entry point: `python -m diffweb` (host/port come from the YAML config)."""

from __future__ import annotations

import sys

import uvicorn

from .config import load_config


def main() -> None:
    cfg = load_config()
    reload = "--reload" in sys.argv[1:]
    print(f"diffweb on http://{cfg.server.host}:{cfg.server.port}", flush=True)
    uvicorn.run("diffweb.app:app", host=cfg.server.host, port=cfg.server.port, reload=reload)


if __name__ == "__main__":
    main()
