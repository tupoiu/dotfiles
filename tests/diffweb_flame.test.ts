// The flame map's folding and HTML (web/flame.ts). Plain strings, no browser.

import { expect, test } from "bun:test";
import { MAX_PROJECTS, buildProjects, countsHtml, diffstat, fold, mapHtml, minWidth, pickForm, tipFor, type Bar, type MapFile } from "../diffweb/web/flame.ts";

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
  expect(html).toContain('<span class="pn">dotfiles</span>');
  expect(html).toContain('data-tip="dotfiles (the repo root)');
  expect(html).toContain('data-scope="api/src" aria-pressed="true"');
  expect(html).toContain('data-scope="api/tests" aria-pressed="false"');
  expect(html).toContain("code <b>800</b>");
  expect(html).toContain('<span class="fbar more" tabindex="0" data-tip="3 small entries · +30 −0\napi/a.txt  +10 −0');
  expect(html).not.toContain(" title="); // the tooltip is drawn by hand, at once
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

test("tooltips: a file's path, diffstat and kind; a folder's count, diffstat and split", () => {
  const api = buildProjects([...FILES, { ...f("api/src/app.py", 0, "api"), path: "api/src/z.py", removed: 7 }])[0]!.root;
  const app = api.kids.get("src")!.kids.get("app.py")!;
  expect(tipFor(app)).toBe("api/src/app.py\n+500 −0 · code");
  expect(tipFor(api.kids.get("src")!)).toBe("api/src/\n3 files · +800 −7\ncode 807 · tests 0 · docs 0");
  expect(diffstat(FILES)).toBe("+1,000 −0");
});

test("boxes are exactly proportional to their lines", () => {
  const html = mapHtml([f("infra/a.py", 75000, "infra"), f("web/b.ts", 30, "web")], 1, "", "r");
  expect(html).toContain('class="pbox" style="flex: 75000 1 0; min-width: 6px"');
  expect(html).toContain('class="pbox" style="flex: 30 1 0; min-width: 6px"');
});

test("a box is at least wide enough for every bar in it to be hovered", () => {
  const big = Array.from({ length: MAX_PROJECTS - 1 }, (_, i) => f(`big${i}/a.py`, 100000, `big${i}`));
  const tiny = Array.from({ length: 26 }, (_, i) => f(`t${i}/a.py`, 2, `t${i}`));
  // 26 bars of 3px, 25 gaps of 1px, and 2px of margin.
  expect(mapHtml([...big, ...tiny], 1, "", "r")).toContain('class="pbox others" style="flex: 52 1 0; min-width: 105px"');
  const deep = buildProjects([f("a/x/1.py", 5), f("a/x/2.py", 5), f("a/x/3.py", 5)])[0]!.root;
  // a/ sits over x/, which sits over three files: 3 + 1 + 3 + 1 + 3.
  expect(minWidth(fold(deep, 3)[0]!)).toBe(11);
});

test("a header's lines take the longest form that fits, or none", () => {
  const widths = [210, 140, 40];  // full words, short letters, the total
  expect(pickForm(widths, 400)).toBe(0);
  expect(pickForm(widths, 210)).toBe(0);  // exactly fitting fits
  expect(pickForm(widths, 209.5)).toBe(1);
  expect(pickForm(widths, 140)).toBe(1);
  expect(pickForm(widths, 60)).toBe(2);
  expect(pickForm(widths, 39)).toBe(3);  // nothing shows
  expect(pickForm(widths, 0)).toBe(3);
});

test("the header's forms carry en-GB numbers, short letters, and the total", () => {
  const html = countsHtml({ code: 3565, test: 0, docs: 387 });
  expect(html).toContain('code <b>3,565</b>');
  expect(html).toContain('c: <b>3,565</b>');
  expect(html).toContain('t: <b>0</b>');
  expect(html).toContain('d: <b>387</b>');
  expect(html).toContain('data-form="3"><b>3,952</b>');
});
