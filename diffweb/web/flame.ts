// The flame map: where a range's change is, by project. Each project is a
// box sized by its lines; inside, an icicle - row 1 the project's top-level
// entries, each row below splitting its parent - to as many levels as asked.
// The server sends every file's lines, kind and project; this file folds them
// into bars and draws them as an HTML string.

import { esc } from "./diff/render.ts";

export type Kind = "code" | "test" | "docs";

export interface MapFile {
  path: string;
  added: number;
  removed: number;
  kind: Kind;
  project: string;  // its folder, "" for the repo root
}

export interface Split { code: number; test: number; docs: number }

export interface MapNode {
  name: string;   // the last part, with a trailing "/" for a folder
  path: string;   // repo-relative, no trailing "/": what scoping the diff uses
  files: MapFile[];
  kids: Map<string, MapNode>;
}

export interface Project { path: string; root: MapNode }

// A bar to draw: a node, or several small siblings merged into one.
export type Bar =
  | { kind: "node"; node: MapNode; lines: number; split: Split; kids: Bar[] }
  | { kind: "small"; nodes: MapNode[]; lines: number; split: Split };

// Siblings under this share of their parent merge into one "+N small" bar.
export const SMALL = 0.04;

const linesOf = (f: MapFile) => f.added + f.removed;

export function split(files: MapFile[]): Split {
  const t: Split = { code: 0, test: 0, docs: 0 };
  for (const f of files) t[f.kind] += linesOf(f);
  return t;
}

export const total = (t: Split) => t.code + t.test + t.docs;

// Projects in order of size, each a tree of its files' paths below it.
export function buildProjects(files: MapFile[]): Project[] {
  const byPath = new Map<string, Project>();
  for (const f of files) {
    let p = byPath.get(f.project);
    if (!p) {
      p = { path: f.project, root: { name: "", path: f.project, files: [], kids: new Map() } };
      byPath.set(f.project, p);
    }
    const rel = f.project ? f.path.slice(f.project.length + 1) : f.path;
    const parts = rel.split("/");
    let node = p.root;
    node.files.push(f);
    parts.forEach((part, i) => {
      const folder = i < parts.length - 1;
      let kid = node.kids.get(part);
      if (!kid) {
        kid = { name: part + (folder ? "/" : ""), path: node.path ? `${node.path}/${part}` : part, files: [], kids: new Map() };
        node.kids.set(part, kid);
      }
      kid.files.push(f);
      node = kid;
    });
  }
  return [...byPath.values()].sort((a, b) => total(split(b.root.files)) - total(split(a.root.files)));
}

// The rows below `node`, down to `levels` rows. Anything deeper is summed into
// the last row, since a bar's lines are all the lines under it.
// `small` is the share under which siblings merge; the shared box of smaller
// projects passes 0 for its first row, which is the projects themselves.
export function fold(node: MapNode, levels: number, depth = 0, small = SMALL): Bar[] {
  if (depth >= levels) return [];
  const parent = total(split(node.files));
  const kids = [...node.kids.values()]
    .map((k) => ({ k, t: split(k.files) }))
    .sort((a, b) => total(b.t) - total(a.t) || a.k.name.localeCompare(b.k.name));
  const bars: Bar[] = [];
  const tiny: typeof kids = [];
  for (const { k, t } of kids) {
    if (parent > 0 && total(t) / parent < small) tiny.push({ k, t });
    else bars.push({ kind: "node", node: k, lines: total(t), split: t, kids: fold(k, levels, depth + 1) });
  }
  if (tiny.length === 1) {
    // One small sibling is drawn as itself: a "+1 small" bar hides a name to save nothing.
    const { k, t } = tiny[0]!;
    bars.push({ kind: "node", node: k, lines: total(t), split: t, kids: [] });
  } else if (tiny.length) {
    const t = split(tiny.flatMap((s) => s.k.files));
    bars.push({ kind: "small", nodes: tiny.map((s) => s.k), lines: total(t), split: t });
  }
  return bars;
}

const fmt = (n: number) => n.toLocaleString("en-GB");

// A left-to-right blend of the bar's own code, tests and docs.
export function fill(t: Split): string {
  const n = total(t) || 1;
  const a = (t.code / n) * 100;
  const b = a + (t.test / n) * 100;
  const mix = (k: Kind) => `var(--k-${k}-soft)`;
  return `linear-gradient(90deg, ${mix("code")} 0 ${a}%, ${mix("test")} ${a}% ${b}%, ${mix("docs")} ${b}% 100%)`;
}

const splitText = (t: Split) => `code ${fmt(t.code)}, tests ${fmt(t.test)}, docs ${fmt(t.docs)}`;
const plural = (k: number, word: string) => `${k} ${word}${k === 1 ? "" : "s"}`;

// Every bar gets some width, so a binary file or a rename with no lines still shows.
const grow = (lines: number) => Math.max(lines, 1);

function barHtml(bar: Bar, scope: string): string {
  if (bar.kind === "small") {
    const title = `${bar.nodes.length} small entries:\n` +
      bar.nodes.map((n) => `${n.path}  ${fmt(total(split(n.files)))}`).join("\n");
    return `<div class="fnode" style="flex: ${grow(bar.lines)} 1 0">` +
      `<span class="fbar more" title="${esc(title)}">+${bar.nodes.length} small</span></div>`;
  }
  const n = bar.node;
  const title = `${n.path} · ${plural(n.files.length, "file")} · ${splitText(bar.split)}`;
  const kids = bar.kids.length ? `<div class="fkids">${bar.kids.map((k) => barHtml(k, scope)).join("")}</div>` : "";
  return `<div class="fnode" style="flex: ${grow(bar.lines)} 1 0">` +
    `<button type="button" class="fbar" data-scope="${esc(n.path)}" aria-pressed="${scope === n.path}"` +
    ` title="${esc(title)}" style="background: ${fill(bar.split)}">${esc(n.name)} ${fmt(bar.lines)}</button>` +
    kids + `</div>`;
}

export function mapHtml(files: MapFile[], levels: number, scope: string, rootName: string): string {
  const legend =
    `<span class="mapkey"><span class="k-code">code</span><span class="k-test">tests</span>` +
    `<span class="k-docs">docs and lockfiles</span></span>`;
  const seg = [1, 2, 3, 4].map((k) =>
    `<button type="button" data-levels="${k}" aria-pressed="${k === levels}">${k}</button>`).join("");
  const head = `<div class="flame-ctl"><span class="dim">levels</span>` +
    `<div class="levels-seg" role="group" aria-label="Levels">${seg}</div>${legend}</div>`;
  if (!files.length) return head + `<p class="dim">Nothing changed in this range.</p>`;
  const all = buildProjects(files);
  const shown = all.length > MAX_PROJECTS ? all.slice(0, MAX_PROJECTS - 1) : all;
  const boxes = shown.map((p) =>
    boxHtml(p.root, p.path || rootName, p.path || "the repo root", levels, scope, ""));
  if (shown.length < all.length) {
    const rest = othersNode(all.slice(shown.length), rootName);
    const n = all.length - shown.length;
    boxes.push(boxHtml(rest, `+${n} smaller projects`, "each bar is one project", levels, scope, " others"));
  }
  return head + `<div class="projects">${boxes.join("")}</div>`;
}

// Past this many, the smallest projects share one box, each a bar in it: a
// change that bumps a line in thirty projects would otherwise be all boxes.
export const MAX_PROJECTS = 8;

// The smaller projects as one tree, whose first row is the projects.
export function othersNode(projects: Project[], rootName: string): MapNode {
  const kids = new Map<string, MapNode>();
  for (const p of projects) kids.set(p.path, { ...p.root, name: `${p.path || rootName}/` });
  return { name: "", path: "", files: projects.flatMap((p) => p.root.files), kids };
}

function boxHtml(root: MapNode, name: string, title: string, levels: number, scope: string, cls: string): string {
  const t = split(root.files);
  const small = cls ? 0 : SMALL;
  const parts = `<span class="split"><span class="c">code <b>${fmt(t.code)}</b></span>` +
    `<span class="t">tests <b>${fmt(t.test)}</b></span><span class="o">docs <b>${fmt(t.docs)}</b></span></span>`;
  const rows = fold(root, levels, 0, small).map((b) => barHtml(b, scope)).join("");
  return `<div class="pbox${cls}" style="flex: ${grow(total(t))} 1 220px">` +
    `<div class="phd"><span class="pn" title="${esc(title)}">${esc(name)}</span>${parts}</div>` +
    `<div class="fkids">${rows}</div></div>`;
}
