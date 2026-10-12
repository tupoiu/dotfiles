// Renders a unified diff with diffweb's real options and prints one character
// per row: "-" deletion, "+" insertion, "." context. Used by the Python tests
// so they can assert on genuinely rendered output without driving a browser.
//
// With --count-inline it prints the number of word-level <ins>/<del> marks
// instead, to prove the grouping did not cost the inline highlighting.
//
// Run with bun, which imports the TypeScript render options directly.

import { join } from "node:path";
import { diffwebRenderOptions } from "../diffweb/web/render-options.ts";

// The vendored core bundle (poe fetch-diffweb-assets): unlike the -ui bundle
// the page loads, it needs no DOM.
const { html } = require(join(import.meta.dir, "..", "diffweb", "static", "vendor", "diff2html.min.js")) as {
  html: (diff: string, config: ReturnType<typeof diffwebRenderOptions>) => string;
};

const sideBySide = process.argv.includes("--side-by-side");
const diff = await Bun.stdin.text();
const rendered = html(diff, diffwebRenderOptions({ sideBySide }));

if (process.argv.includes("--count-inline")) {
  process.stdout.write(String(rendered.match(/<(ins|del)>/g)?.length ?? 0));
} else {
  const rows = rendered.match(/<tr>[\s\S]*?<\/tr>/g) ?? [];
  process.stdout.write(
    rows.map((r) => (r.includes("d2h-del") ? "-" : r.includes("d2h-ins") ? "+" : ".")).join(""),
  );
}
