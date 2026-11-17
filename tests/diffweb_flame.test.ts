// The flame map's folding and HTML (web/flame.ts). Plain strings, no browser.

import { expect, test } from "bun:test";
import { MAX_PROJECTS, buildProjects, fold, mapHtml, type Bar, type MapFile } from "../diffweb/web/flame.ts";

const f = (path: string, lines: number, project = "", kind: MapFile["kind"] = "code"): MapFile =>
  ({ path, added: lines, removed: 0, kind, project });

const FILES: MapFile[] = [
  f("api/src/app.py", 500, "api"),
  f("api/src/util/x.py", 300, "api"),
  f("api/tests/test_app.py", 150, "api", "test"),
  f("api/README.md", 10, "api", "docs"),
  f("api/a.txt", 10, "api", "docs"),
  f("api/b.txt", 10, "api", "docs"),
  f("README.md", 20),
];

const names = (bars: Bar[]) => bars.map((b) => (b.kind === "node" ? `${b.node.name} ${b.lines}` : `+${b.nodes.length} ${b.lines}`));

test("projects come biggest first, and a file's path is cut at its project", () => {
  const ps = buildProjects(FILES);
  expect(ps.map((p) => p.path)).toEqual(["api", ""]);
  expect([...ps[0]!.root.kids.keys()]).toEqual(["src", "tests", "README.md", "a.txt", "b.txt"]);
  expect(ps[0]!.root.kids.get("src")!.path).toBe("api/src");
});

test("each row splits its parent, and levels cap the depth", () => {
  const api = buildProjects(FILES)[0]!.root;
  const one = fold(api, 1);
  // README.md, a.txt and b.txt are each 10 of 980 lines: under 4%, so merged.
  expect(names(one)).toEqual(["src/ 800", "tests/ 150", "+3 30"]);
  expect(one.every((b) => b.kind === "small" || b.kids.length === 0)).toBe(true);
  const src = fold(api, 3)[0]!;
  expect(src.kind === "node" && names(src.kids)).toEqual(["app.py 500", "util/ 300"]);
  const util = src.kind === "node" ? src.kids[1]! : null;
  expect(util?.kind === "node" && names(util.kids)).toEqual(["x.py 300"]);
});

test("one small sibling is drawn as itself, not as +1", () => {
  const root = buildProjects([f("big.py", 990), f("tiny.py", 10)])[0]!.root;
  expect(names(fold(root, 1))).toEqual(["big.py 990", "tiny.py 10"]);
});

test("the HTML names the root after the worktree and marks the scoped bar", () => {
  const html = mapHtml(FILES, 2, "api/src", "dotfiles");
  expect(html).toContain('<span class="pn" title="the repo root">dotfiles</span>');
  expect(html).toContain('data-scope="api/src" aria-pressed="true"');
  expect(html).toContain('data-scope="api/tests" aria-pressed="false"');
  expect(html).toContain("code <b>800</b>");
  expect(html).toContain('<span class="fbar more" title="3 small entries:');
  expect(html).toContain('data-levels="2" aria-pressed="true"');
});

test("an empty range says so", () => {
  expect(mapHtml([], 3, "", "x")).toContain("Nothing changed");
});

test("past the cap, the smallest projects share one box, a bar each", () => {
  // 30 one-line projects are each about 3% of their shared box: tiny, but
  // never merged there, as the projects are the point of that row.
  const big = Array.from({ length: MAX_PROJECTS - 1 }, (_, i) => f(`big${i}/a.py`, 1000, `big${i}`));
  const tiny = Array.from({ length: 30 }, (_, i) => f(`t${i}/a.py`, 1, `t${i}`));
  const html = mapHtml([...big, ...tiny], 2, "", "root");
  expect(html.match(/class="pbox"/g)?.length).toBe(MAX_PROJECTS - 1);
  expect(html).toContain("+30 smaller projects");
  expect(html).toContain('data-scope="t29" aria-pressed="false"');
  expect(html).toContain(">t29/ 1</button>");
  expect(html).not.toContain("+30 small<");
});
