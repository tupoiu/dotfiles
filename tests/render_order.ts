// Renders a unified diff with diffweb's real parser and renderer and prints one
// character per row: "-" deletion, "+" insertion, "." context. Used by the
// Python tests so they can assert on genuinely rendered output without
// driving a browser.
//
// With --side-by-side, it prints the old column top to bottom, then the new
// column, so a block of removals then additions reads the same in both layouts.
//
// With --count-inline it prints the number of word-level <ins>/<del> marks
// instead, to prove the grouping did not cost the inline highlighting.
//
// Run with bun, which imports the TypeScript modules directly.

import { join } from "node:path";
import { parseDiff } from "../diffweb/web/diff/parse.ts";
import { renderFile } from "../diffweb/web/diff/render.ts";

// The page loads jsdiff as a global; the vendored bundle is also CommonJS.
(globalThis as { Diff?: unknown }).Diff = require(join(import.meta.dir, "..", "diffweb", "static", "vendor", "diff.min.js"));

const sideBySide = process.argv.includes("--side-by-side");
const diff = await Bun.stdin.text();
const rendered = parseDiff(diff)
  .map((file) => renderFile({ file, sideBySide }, { id: "f", path: file.newPath, name: file.newPath }))
  .join("");

const kind = (cell: string) => (cell.includes("dw-del") ? "-" : cell.includes("dw-add") ? "+" : cell.includes("dw-ctx") ? "." : "");

if (process.argv.includes("--count-inline")) {
  process.stdout.write(String(rendered.match(/<(ins|del)>/g)?.length ?? 0));
} else {
  const rows = (rendered.match(/<tr>[\s\S]*?<\/tr>/g) ?? []);
  const codeCells = (row: string) => row.match(/<td class="dw-code[^"]*"/g) ?? [];
  if (sideBySide) {
    const left = rows.map((r) => kind(codeCells(r)[0] ?? "")).join("");
    const right = rows.map((r) => kind(codeCells(r)[1] ?? "")).join("");
    process.stdout.write(left + right);
  } else {
    process.stdout.write(rows.map((r) => kind(codeCells(r)[0] ?? "")).join(""));
  }
}
