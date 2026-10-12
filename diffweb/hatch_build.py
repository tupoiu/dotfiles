"""Build hook: put the browser assets into the wheel.

Both asset folders are gitignored, so a build from a clean checkout makes them:
- static/vendor/ - diff2html and highlight.js, from fetch-assets.sh.
- static/dist/ - the page scripts, bundled from web/*.ts by `bun run build`.

A build from an sdist already has both, so it needs neither bun nor the
network. With bun it still rebuilds the page scripts.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

HERE = Path(__file__).parent
WEB = HERE / "web"
DIST = HERE / "static" / "dist"
VENDOR = HERE / "static" / "vendor"
VENDORED = [
    "diff2html.min.css",
    "diff2html-ui.min.js",
    "highlight-light.min.css",
    "highlight-dark.min.css",
]


class AssetsHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if not all((VENDOR / name).exists() for name in VENDORED):
            subprocess.run(["bash", str(HERE / "fetch-assets.sh"), "--vendor-only"], check=True)
        bun = shutil.which("bun")
        if bun is not None and WEB.exists():
            # Rebuild every time, so a wheel from a checkout never has stale page scripts.
            subprocess.run([bun, "run", "build"], cwd=HERE, check=True)
        elif not (DIST / "worktree.js").exists():
            raise RuntimeError(
                "diffweb: building from source needs `bun` on PATH to bundle the page scripts "
                "(https://bun.sh). Or install the prebuilt wheel - see README.md."
            )
