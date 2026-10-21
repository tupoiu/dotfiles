#!/usr/bin/bash
# Vendor the browser-side libraries so diffweb works with no CDN at runtime.
set -euo pipefail
cd "$(dirname "$0")"
# --vendor-only skips `bun install`; the wheel build (hatch_build.py) uses it.
cd static/vendor
HLJS=11.9.0
JSDIFF=7.0.0
# The page loads these with <script> tags, which put `hljs` and `Diff` on
# window. Both bundles are also CommonJS, so the bun tests require() the same files.
curl -fsSL -o highlight.min.js     "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@${HLJS}/highlight.min.js"
# Not in the "common" set, and this repo is full of it.
curl -fsSL -o highlight-nix.min.js "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@${HLJS}/languages/nix.min.js"
curl -fsSL -o diff.min.js          "https://cdn.jsdelivr.net/npm/diff@${JSDIFF}/dist/diff.min.js"
curl -fsSL -o highlight-light.min.css "https://cdn.jsdelivr.net/npm/highlight.js@${HLJS}/styles/github.min.css"
curl -fsSL -o highlight-dark.min.css  "https://cdn.jsdelivr.net/npm/highlight.js@${HLJS}/styles/github-dark.min.css"
# diff2html is no longer used; drop what an older run left behind.
rm -f diff2html.min.css diff2html-ui.min.js diff2html.min.js
echo "vendored highlight.js ${HLJS} + jsdiff ${JSDIFF} into $(pwd)"
[[ "${1:-}" == --vendor-only ]] && exit 0
cd ../..
# The type checker and playwright (package.json).
bun install
