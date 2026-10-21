// Renderer, highlighter and expander tests for web/diff/.
//
// The renderer makes HTML strings, so these run under `bun test` with no
// browser. jsdiff and highlight.js are the vendored bundles the page loads
// (poe fetch-diffweb-assets).

import { describe, expect, test } from "bun:test";
import { join } from "node:path";
import { parseDiff, type DiffFile } from "../diffweb/web/diff/parse.ts";
import {
  STEP, addInterval, codeHtml, expanders, gaps, pieces, renderBody, renderFile, textMatches, wordMarks,
  type FileView,
} from "../diffweb/web/diff/render.ts";
import { fileLines, highlightFile, languageFor, splitHighlighted } from "../diffweb/web/diff/highlight.ts";

const VENDOR = join(import.meta.dir, "..", "diffweb", "static", "vendor");
const g = globalThis as { Diff?: unknown; hljs?: unknown };
g.Diff = require(join(VENDOR, "diff.min.js"));
g.hljs = require(join(VENDOR, "highlight.min.js"));

const one = (diff: string): DiffFile => parseDiff(diff)[0]!;

// Rows of a rendered body as [old number, new number, kind] for line-by-line.
function rows(html: string): string[] {
  return [...html.matchAll(/<tr( class="[^"]*")?>([\s\S]*?)<\/tr>/g)].map((m) => {
    const cells = [...m[2]!.matchAll(/<td class="([^"]*)"[^>]*>([\s\S]*?)<\/td>/g)];
    if (m[2]!.includes("dw-hunk-text")) return "@@";
    const nums = cells.filter((c) => c[1] === "dw-num").map((c) => c[2]);
    const codeCell = cells.find((c) => c[1]!.startsWith("dw-code"))!;
    const kind = codeCell[1]!.replace("dw-code dw-", "");
    return `${nums.join(",")} ${kind}${m[1]?.includes("dw-revealed") ? " revealed" : ""}`;
  });
}

const SIMPLE = [
  "diff --git a/f.txt b/f.txt",
  "index 1111111..2222222 100644",
  "--- a/f.txt",
  "+++ b/f.txt",
  "@@ -1,3 +1,3 @@",
  " unchanged",
  "-the quick brown fox",
  "-second old line",
  "+the quick red fox",
  "+second new line",
  "",
].join("\n");

describe("line-by-line", () => {
  test("rows carry both line numbers and keep git's order", () => {
    const html = renderBody({ file: one(SIMPLE), sideBySide: false });
    expect(rows(html)).toEqual(["@@", "1,1 ctx", "2, del", "3, del", ",2 add", ",3 add"]);
  });

  test("content is escaped", () => {
    const diff = SIMPLE.replace("+the quick red fox", '+<script>alert("x")</script>');
    const html = renderBody({ file: one(diff), sideBySide: false });
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  test("a missing newline at the end of a file is marked", () => {
    const diff = [
      "diff --git a/f b/f", "--- a/f", "+++ b/f", "@@ -1 +1 @@", "-a", "\\ No newline at end of file", "+b", "",
    ].join("\n");
    expect(renderBody({ file: one(diff), sideBySide: false })).toContain("dw-nonl");
  });
});

describe("side-by-side", () => {
  test("removals pair with additions row by row; extras get an empty side", () => {
    const diff = [
      "diff --git a/f b/f", "--- a/f", "+++ b/f", "@@ -1,2 +1,3 @@", "-a", "-b", "+A", "+B", "+C", "",
    ].join("\n");
    const html = renderBody({ file: one(diff), sideBySide: true });
    const body = [...html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
      [...m[1]!.matchAll(/<td class="dw-code ([^"]*)"/g)].map((c) => c[1]).join("|"));
    expect(body).toEqual(["dw-del|dw-add", "dw-del|dw-add", "dw-empty|dw-add"]);
  });

  test("context lines show on both sides with their own numbers", () => {
    const diff = ["diff --git a/f b/f", "--- a/f", "+++ b/f", "@@ -5,2 +7,2 @@", " x", "-y", "+z", ""].join("\n");
    const html = renderBody({ file: one(diff), sideBySide: true });
    expect(html).toContain('<tr><td class="dw-num">5</td><td class="dw-code dw-ctx" data-sign=" ">x</td>' +
      '<td class="dw-num">7</td><td class="dw-code dw-ctx" data-sign=" ">x</td></tr>');
  });
});

describe("word marks", () => {
  test("similar lines get marks on what changed", () => {
    expect(wordMarks("the quick brown fox", "the quick red fox")).toEqual([[[10, 15]], [[10, 13]]]);
  });

  test("lines with little in common get none", () => {
    expect(wordMarks("import os", "return render(template, context)")).toBeNull();
  });

  test("a rendered pair carries <del> and <ins>", () => {
    const html = renderBody({ file: one(SIMPLE), sideBySide: false });
    expect(html).toContain("the quick <del>brown</del> fox");
    expect(html).toContain("the quick <ins>red</ins> fox");
  });

  test("a mark that crosses highlight spans stays well nested", () => {
    const segs = [{ text: "ab", scopes: ["hljs-string"] }, { text: "cd", scopes: [] }];
    expect(codeHtml(segs, [[1, 3]], "ins")).toBe(
      '<span class="hljs-string">a</span><ins><span class="hljs-string">b</span>c</ins>d');
  });
});

describe("highlighting", () => {
  test("a span across a line break is closed and opened again", () => {
    const lines = splitHighlighted('x = <span class="hljs-string">"""a\nb"""</span>\n<span class="hljs-comment"># c</span>');
    expect(lines).toEqual([
      [{ text: "x = ", scopes: [] }, { text: '"""a', scopes: ["hljs-string"] }],
      [{ text: 'b"""', scopes: ["hljs-string"] }],
      [{ text: "# c", scopes: ["hljs-comment"] }],
    ]);
  });

  test("the middle of a multi-line string is coloured as a string", () => {
    const lines = highlightFile('s = """\nimport os\n"""\n', "python");
    expect(lines[1]).toEqual([{ text: "import os", scopes: ["hljs-string"] }]);
  });

  test("the language comes from the extension or the name", () => {
    expect(languageFor("a/b/app.py")).toBe("py");
    expect(languageFor("web/x.tsx")).toBe("typescript");
    expect(languageFor("sub/Makefile")).toBe("makefile");
    expect(languageFor("LICENSE")).toBeNull();
    expect(languageFor("uv.lock")).toBeNull();
  });

  test("highlighting that does not match the diff line is not used", () => {
    const view: FileView = {
      file: one(SIMPLE), sideBySide: false,
      highlight: () => [{ text: "something else", scopes: ["hljs-keyword"] }],
    };
    expect(renderBody(view)).not.toContain("hljs-keyword");
  });

  test("file lines drop the final newline and CRs", () => {
    expect(fileLines("a\r\nb\n")).toEqual(["a", "b"]);
    expect(fileLines("")).toEqual([]);
  });
});

// --- expanding ---------------------------------------------------------------

// A 201-line file, changed at lines 50 and 150, with one line added at 50.
const END = Array.from({ length: 201 }, (_, i) => `line ${i + 1}`);
const TWO_HUNKS = [
  "diff --git a/f.txt b/f.txt",
  "--- a/f.txt",
  "+++ b/f.txt",
  "@@ -47,6 +47,7 @@ def first():",
  " line 47", " line 48", " line 49", "+line 50", " line 51", " line 52", " line 53",
  "@@ -146,7 +147,7 @@ def second():",
  " line 147", " line 148", " line 149", "-old 150", "+line 150", " line 151", " line 152", " line 153",
  "",
].join("\n");

describe("gaps", () => {
  const f = one(TWO_HUNKS);

  test("the gaps before, between and after hunks, with their old-side offsets", () => {
    expect(gaps(f, END.length)).toEqual([
      { from: 1, to: 46, offset: 0, before: 0 },
      { from: 54, to: 146, offset: -1, before: 1 },
      { from: 154, to: 201, offset: -1, before: null },
    ]);
  });

  test("the gap after the last hunk needs the length of the file", () => {
    expect(gaps(f, null)).toHaveLength(2);
  });

  test("a pure deletion sits just after its start line", () => {
    const del = one(["diff --git a/f b/f", "--- a/f", "+++ b/f", "@@ -10,2 +9,0 @@", "-x", "-y", ""].join("\n"));
    expect(gaps(del, 20)).toEqual([
      { from: 1, to: 9, offset: 0, before: 0 },
      { from: 10, to: 20, offset: 2, before: null },
    ]);
  });

  test("an added file has no gaps with lines in them", () => {
    const add = one(["diff --git a/f b/f", "new file mode 100644", "--- /dev/null", "+++ b/f",
      "@@ -0,0 +1,2 @@", "+a", "+b", ""].join("\n"));
    expect(gaps(add, 2).filter((x) => x.from <= x.to)).toEqual([]);
  });
});

describe("expanders", () => {
  test(`a run of ${STEP} lines or fewer has one button that shows it all`, () => {
    expect(expanders({ from: 5, to: 5 + STEP - 1 }, false).map((e) => e.label)).toEqual(["↕"]);
  });

  test("a longer run grows from both sides; past twice the step it can open all at once", () => {
    expect(expanders({ from: 54, to: 54 + 2 * STEP - 1 }, false).map((e) => e.label)).toEqual(["↓", "↑"]);
    const big = expanders({ from: 54, to: 146 }, false);
    expect(big.map((e) => [e.label, e.reveal])).toEqual([
      ["↓", { from: 54, to: 54 + STEP - 1 }],
      ["↑", { from: 146 - STEP + 1, to: 146 }],
      ["↕", { from: 54, to: 146 }],
    ]);
  });

  test("the top of the file only grows upwards and the end only downwards", () => {
    expect(expanders({ from: 1, to: 46 }, false).map((e) => e.label)).toEqual(["↑"]);
    expect(expanders({ from: 154, to: 201 }, true).map((e) => e.label)).toEqual(["↓"]);
  });

  test("revealed runs cut a gap into shown and hidden pieces", () => {
    const gap = { from: 54, to: 146, offset: -1, before: 1 };
    expect(pieces(gap, [{ from: 54, to: 83 }, { from: 117, to: 146 }])).toEqual([
      { shown: true, from: 54, to: 83 },
      { shown: false, from: 84, to: 116 },
      { shown: true, from: 117, to: 146 },
    ]);
  });

  test("intervals that touch or overlap merge", () => {
    expect(addInterval([{ from: 1, to: 10 }, { from: 30, to: 40 }], { from: 11, to: 29 })).toEqual([{ from: 1, to: 40 }]);
    expect(addInterval([{ from: 1, to: 10 }], { from: 20, to: 25 })).toHaveLength(2);
  });
});

describe("expanded rows", () => {
  const f = one(TWO_HUNKS);
  const view = (revealed = [] as { from: number; to: number }[]): FileView =>
    ({ file: f, sideBySide: false, endLines: END, revealed });

  test("without the file's text there are headers but no buttons", () => {
    const html = renderBody({ file: f, sideBySide: false });
    expect(html).not.toContain("dw-expand");
    expect(rows(html).filter((r) => r === "@@")).toHaveLength(2);
  });

  test("with it, every gap gets buttons, including the end of the file", () => {
    const html = renderBody(view());
    expect(html.match(/class="dw-hunk"/g)).toHaveLength(3);
    expect(html).toContain('data-from="17" data-to="46"');  // the top of the file only grows upwards
    expect(html).toContain('data-from="154" data-to="183"');
  });

  test("revealed lines are context with the right number on each side", () => {
    const r = rows(renderBody(view([{ from: 144, to: 146 }])));
    const at = r.indexOf("143,144 ctx revealed");
    // The rest of the gap stays behind its header; the hunk follows the revealed lines.
    expect(r.slice(at - 1, at + 4)).toEqual([
      "@@", "143,144 ctx revealed", "144,145 ctx revealed", "145,146 ctx revealed", "146,147 ctx",
    ]);
  });

  test("a gap opened all the way joins the hunks: its header goes", () => {
    const html = renderBody(view([{ from: 54, to: 146 }]));
    expect(html.match(/class="dw-hunk"/g)).toHaveLength(2);  // the top and the end of the file
    expect(html).not.toContain("def second():");
  });

  test("a stale text (the file changed since the diff) gets no buttons", () => {
    const stale = END.map((l) => (l === "line 50" ? "line fifty" : l));
    expect(textMatches(f, stale)).toBe(false);
    expect(renderBody({ ...view(), endLines: stale })).not.toContain("dw-expand");
  });

  test("side-by-side expanded rows are one row each", () => {
    const html = renderBody({ ...view([{ from: 45, to: 46 }]), sideBySide: true });
    expect(html).toContain('<tr class="dw-revealed"><td class="dw-num">46</td>' +
      '<td class="dw-code dw-ctx" data-sign=" ">line 46</td><td class="dw-num">46</td>');
  });
});

describe("files with no hunks", () => {
  test("binary, rename-only and mode-only files say what changed", () => {
    const diff = [
      "diff --git a/img.png b/img.png", "index 1..2 100644", "Binary files a/img.png and b/img.png differ",
      "diff --git a/old.txt b/new.txt", "similarity index 100%", "rename from old.txt", "rename to new.txt",
      "diff --git a/run.sh b/run.sh", "old mode 100644", "new mode 100755",
      "",
    ].join("\n");
    const [bin, ren, mode] = parseDiff(diff).map((file) => renderBody({ file, sideBySide: false }));
    expect(bin).toContain("Binary file");
    expect(ren).toContain("Renamed without changes (100% similar)");
    expect(mode).toContain("Mode changed from 100644 to 100755");
  });

  test("the header carries the path and a status tag", () => {
    const file = one(["diff --git a/x b/x", "deleted file mode 100644", "--- a/x", "+++ /dev/null",
      "@@ -1 +0,0 @@", "-gone", ""].join("\n"));
    const html = renderFile({ file, sideBySide: false }, { id: "f1", path: "x", name: "x" });
    expect(html).toContain('<div class="dw-file" id="f1" data-path="x">');
    expect(html).toContain('<span class="dw-tag dw-tag-deleted">deleted</span>');
  });
});
