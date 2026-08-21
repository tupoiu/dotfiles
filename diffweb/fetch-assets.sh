#!/usr/bin/bash
# Vendor the browser-side libraries so diffweb works with no CDN at runtime.
set -euo pipefail
cd "$(dirname "$0")/static/vendor"
D2H=3.4.51
HLJS=11.9.0
curl -fsSL -o diff2html.min.css   "https://cdn.jsdelivr.net/npm/diff2html@${D2H}/bundles/css/diff2html.min.css"
curl -fsSL -o diff2html-ui.min.js "https://cdn.jsdelivr.net/npm/diff2html@${D2H}/bundles/js/diff2html-ui.min.js"
# The core bundle is not used by the page; it runs under node, which lets the
# tests assert on real rendered output without a browser.
curl -fsSL -o diff2html.min.js    "https://cdn.jsdelivr.net/npm/diff2html@${D2H}/bundles/js/diff2html.min.js"
curl -fsSL -o highlight-light.min.css "https://cdn.jsdelivr.net/npm/highlight.js@${HLJS}/styles/github.min.css"
curl -fsSL -o highlight-dark.min.css  "https://cdn.jsdelivr.net/npm/highlight.js@${HLJS}/styles/github-dark.min.css"
echo "vendored diff2html ${D2H} + highlight.js ${HLJS} into $(pwd)"
