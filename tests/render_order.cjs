// Renders a unified diff with diffweb's real options and prints one character
// per row: "-" deletion, "+" insertion, "." context. Used by the Python tests
// so they can assert on genuinely rendered output without driving a browser.
const path = require("path");
const STATIC = path.join(__dirname, "..", "diffweb", "static");
const { html } = require(path.join(STATIC, "vendor", "diff2html.min.js"));
const { diffwebRenderOptions } = require(path.join(STATIC, "render-options.js"));

const sideBySide = process.argv.includes("--side-by-side");
const diff = require("fs").readFileSync(0, "utf8");
const rendered = html(diff, diffwebRenderOptions({ sideBySide }));

const rows = rendered.match(/<tr>[\s\S]*?<\/tr>/g) || [];
process.stdout.write(
  rows.map((r) => (r.includes("d2h-del") ? "-" : r.includes("d2h-ins") ? "+" : ".")).join(""),
);
