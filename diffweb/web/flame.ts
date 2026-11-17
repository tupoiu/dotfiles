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

const plural = (k: number, word: string) => `${k} ${word}${k === 1 ? "" : "s"}`;
const KIND_NAME: Record<Kind, string> = { code: "code", test: "tests", docs: "docs" };

// Every bar gets some width, so a binary file or a rename with no lines still shows.
const grow = (lines: number) => Math.max(lines, 1);

// +added -removed over some files.
export function diffstat(files: MapFile[]): string {
  const added = files.reduce((k, f) => k + f.added, 0);
  const removed = files.reduce((k, f) => k + f.removed, 0);
  return `+${fmt(added)} \u2212${fmt(removed)}`;
}

// What a bar's tooltip says. A file: its path, diffstat and kind. A folder or
// a project: its path, file count, diffstat and code/tests/docs split.
export function tipFor(node: MapNode, label = node.path): string {
  const only = node.files.length === 1 && node.kids.size === 0 ? node.files[0]! : null;
  if (only) return `${label}\n${diffstat([only])} \u00b7 ${KIND_NAME[only.kind]}`;
  const t = split(node.files);
  return `${label}/\n${plural(node.files.length, "file")} \u00b7 ${diffstat(node.files)}\n` +
    `code ${fmt(t.code)} \u00b7 tests ${fmt(t.test)} \u00b7 docs ${fmt(t.docs)}`;
}

function smallTip(nodes: MapNode[]): string {
  const files = nodes.flatMap((n) => n.files);
  return `${nodes.length} small entries \u00b7 ${diffstat(files)}\n` +
    nodes.map((n) => `${n.path}${n.kids.size ? "/" : ""}  ${diffstat(n.files)}`).join("\n");
}

// The narrowest a bar may be and still be hovered. A bar is at least wide
// enough for the bars under it, so a box of many tiny projects grows to fit
// them rather than clipping all but the first.
export const MIN_BAR = 3;
const GAP = 1;  // .fkids gap

export function minWidth(bar: Bar): number {
  if (bar.kind === "small" || !bar.kids.length) return MIN_BAR;
  return Math.max(MIN_BAR, rowWidth(bar.kids));
}

export const rowWidth = (bars: Bar[]) =>
  bars.reduce((w, b) => w + minWidth(b), 0) + GAP * Math.max(0, bars.length - 1);

const nodeStyle = (bar: Bar) => `flex: ${grow(bar.lines)} 1 0; min-width: ${minWidth(bar)}px`;

function barHtml(bar: Bar, scope: string): string {
  if (bar.kind === "small") {
    return `<div class="fnode" style="${nodeStyle(bar)}">` +
      `<span class="fbar more" tabindex="0" data-tip="${esc(smallTip(bar.nodes))}">+${bar.nodes.length} small</span></div>`;
  }
  const n = bar.node;
  const kids = bar.kids.length ? `<div class="fkids">${bar.kids.map((k) => barHtml(k, scope)).join("")}</div>` : "";
  return `<div class="fnode" style="${nodeStyle(bar)}">` +
    `<button type="button" class="fbar" data-scope="${esc(n.path)}" aria-pressed="${scope === n.path}"` +
    ` data-tip="${esc(tipFor(n))}" style="background: ${fill(bar.split)}">${esc(n.name)} ${fmt(bar.lines)}</button>` +
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
    boxHtml(p.root, p.path || rootName, tipFor(p.root, p.path || `${rootName} (the repo root)`), levels, scope, ""));
  if (shown.length < all.length) {
    const rest = othersNode(all.slice(shown.length), rootName);
    const n = all.length - shown.length;
    const tip = `${n} smaller projects, a bar each\n${plural(rest.files.length, "file")} \u00b7 ${diffstat(rest.files)}`;
    boxes.push(boxHtml(rest, `+${n} smaller projects`, tip, levels, scope, " others"));
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

// A box's width is its share of the range's lines, exactly: flex-basis 0 and no
// padding or border of its own (the outline is an inset shadow), so a project
// beside a 75k-line one may be a few pixels wide. Its tooltip is how you read it.
function boxHtml(root: MapNode, name: string, tip: string, levels: number, scope: string, cls: string): string {
  const t = split(root.files);
  const small = cls ? 0 : SMALL;
  const parts = `<span class="split"><span class="c">code <b>${fmt(t.code)}</b></span>` +
    `<span class="t">tests <b>${fmt(t.test)}</b></span><span class="o">docs <b>${fmt(t.docs)}</b></span></span>`;
  const bars = fold(root, levels, 0, small);
  const rows = bars.map((b) => barHtml(b, scope)).join("");
  // 2px: the row's 1px margin each side, inside the box's inset outline.
  const min = Math.max(6, rowWidth(bars) + 2);
  return `<div class="pbox${cls}" style="flex: ${grow(total(t))} 1 0; min-width: ${min}px">` +
    `<div class="phd" tabindex="0" data-tip="${esc(tip)}"><span class="pn">${esc(name)}</span>${parts}</div>` +
    `<div class="fkids">${rows}</div></div>`;
}

// The tooltip: drawn by hand, so it appears at once (a native `title` waits
// about a second) and on keyboard focus too. One element, moved to whichever
// bar is under the pointer or focused.
export function attachTips(root: HTMLElement): void {
  const tip = document.createElement("div");
  tip.className = "flame-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);
  const show = (target: EventTarget | null) => {
    const el = (target as Element | null)?.closest<HTMLElement>("[data-tip]");
    if (!el || !root.contains(el)) return;
    tip.textContent = el.dataset.tip!;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const left = Math.max(4, Math.min(r.left, window.innerWidth - w - 4));
    const below = r.bottom + 4 + h <= window.innerHeight;
    tip.style.left = `${left}px`;
    tip.style.top = `${below ? r.bottom + 4 : Math.max(4, r.top - h - 4)}px`;
  };
  const hide = () => { tip.hidden = true; };
  root.addEventListener("mouseover", (e) => show(e.target));
  root.addEventListener("focusin", (e) => show(e.target));
  root.addEventListener("mouseleave", hide);
  root.addEventListener("focusout", hide);
  window.addEventListener("scroll", hide, { passive: true });
}
