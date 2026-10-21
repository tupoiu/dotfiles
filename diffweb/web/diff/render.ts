// Draws parsed diffs as HTML strings.
//
// Strings rather than DOM nodes, so `bun test` can check the output without a
// browser, and so a whole diff is one innerHTML assignment.
//
// Both layouts are one table per file. Side-by-side has four columns (old
// number, old code, new number, new code), so its two sides cannot drift out
// of line, and a line shown by an expander is one row in either layout.

import type { DiffFile, DiffLine, Hunk } from "./parse.ts";
import type { HlLine, Seg } from "./highlight.ts";

// Lines shown per click of ↑ or ↓. GitHub uses 20, which is a little tight.
export const STEP = 30;

export interface Interval {
  from: number;  // inclusive, new-side line numbers
  to: number;
}

export interface FileView {
  file: DiffFile;
  sideBySide: boolean;
  // A line's highlighted text, or null to draw it plain.
  highlight?: (side: "old" | "new", lineNo: number, text: string) => HlLine | null;
  // The end side's lines. Expanders need them; without them none are drawn.
  endLines?: string[] | null;
  revealed?: Interval[];
}

export const esc = (s: string): string =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// --- gaps between hunks ---------------------------------------------------

// The lines a hunk covers on one side. A side with no lines (a pure deletion
// on the new side) sits just after line `start`.
function span(start: number, count: number): [first: number, last: number] {
  const first = count > 0 ? start : start + 1;
  return [first, first + count - 1];
}

export interface Gap {
  from: number;          // new-side lines from..to are not in any hunk; may be empty
  to: number;
  offset: number;        // old line number = new line number + offset
  before: number | null; // the hunk after the gap, or null for the end of the file
}

// The gap before each hunk, plus the one after the last hunk when the length
// of the file is known.
export function gaps(file: DiffFile, endLineCount: number | null): Gap[] {
  const out: Gap[] = [];
  let newLast = 0;
  let oldLast = 0;
  file.hunks.forEach((h, k) => {
    const [newFirst, nl] = span(h.newStart, h.newCount);
    const [oldFirst, ol] = span(h.oldStart, h.oldCount);
    out.push({ from: newLast + 1, to: newFirst - 1, offset: oldFirst - newFirst, before: k });
    newLast = nl;
    oldLast = ol;
  });
  if (endLineCount !== null && file.hunks.length) {
    out.push({ from: newLast + 1, to: endLineCount, offset: oldLast - newLast, before: null });
  }
  return out;
}

export type Piece = { shown: boolean; from: number; to: number };

// A gap cut into the runs the user has revealed and the runs still hidden.
export function pieces(gap: Gap, revealed: Interval[]): Piece[] {
  const out: Piece[] = [];
  let at = gap.from;
  const sorted = [...revealed].sort((a, b) => a.from - b.from);
  for (const r of sorted) {
    const from = Math.max(r.from, at);
    const to = Math.min(r.to, gap.to);
    if (from > to) continue;
    if (from > at) out.push({ shown: false, from: at, to: from - 1 });
    out.push({ shown: true, from, to });
    at = to + 1;
  }
  if (at <= gap.to) out.push({ shown: false, from: at, to: gap.to });
  return out;
}

export interface Expander {
  label: string;
  title: string;
  reveal: Interval;
}

// The buttons for one hidden run. ↓ grows the code above the run downwards,
// ↑ grows the code below it upwards.
export function expanders(hidden: Interval, atEnd: boolean): Expander[] {
  const n = hidden.to - hidden.from + 1;
  const lines = (k: number) => `${k} line${k === 1 ? "" : "s"}`;
  if (n <= STEP) return [{ label: "↕", title: `Show ${lines(n)}`, reveal: hidden }];
  const out: Expander[] = [];
  if (hidden.from > 1) {
    out.push({ label: "↓", title: `Show ${lines(STEP)} below`, reveal: { from: hidden.from, to: hidden.from + STEP - 1 } });
  }
  if (!atEnd) {
    out.push({ label: "↑", title: `Show ${lines(STEP)} above`, reveal: { from: hidden.to - STEP + 1, to: hidden.to } });
  }
  if (n > 2 * STEP) out.push({ label: "↕", title: `Show all ${lines(n)}`, reveal: hidden });
  return out;
}

// Add an interval and merge any that now touch or overlap.
export function addInterval(list: Interval[], add: Interval): Interval[] {
  const all = [...list, add].sort((a, b) => a.from - b.from);
  const out: Interval[] = [];
  for (const r of all) {
    const last = out[out.length - 1];
    if (last && r.from <= last.to + 1) last.to = Math.max(last.to, r.to);
    else out.push({ ...r });
  }
  return out;
}

// The expanders read the end side's text, so it must agree with the diff.
// It does not when the file changed between fetching the diff and the text.
export function textMatches(file: DiffFile, endLines: string[]): boolean {
  for (const h of file.hunks) {
    for (const l of h.lines) {
      if (l.newNo !== null && endLines[l.newNo - 1] !== l.text) return false;
    }
  }
  return true;
}

// --- word-level marks -----------------------------------------------------

interface Change { value: string; added?: boolean; removed?: boolean }
const jsdiff = () => (globalThis as { Diff?: { diffWordsWithSpace(a: string, b: string): Change[] } }).Diff;

type Range = [start: number, end: number];

// What changed inside a removed line and the line that replaced it. Lines
// with little in common get no marks: marking nearly every word says nothing.
export function wordMarks(before: string, after: string): [Range[], Range[]] | null {
  const d = jsdiff();
  if (!d || before.length + after.length > 4000) return null;
  const changes = d.diffWordsWithSpace(before, after);
  const common = changes.filter((c) => !c.added && !c.removed).map((c) => c.value).join("").trim().length;
  if (common < 0.3 * Math.max(before.trim().length, after.trim().length)) return null;
  const del: Range[] = [];
  const ins: Range[] = [];
  let a = 0;
  let b = 0;
  for (const c of changes) {
    const n = c.value.length;
    if (c.removed) { del.push([a, a + n]); a += n; }
    else if (c.added) { ins.push([b, b + n]); b += n; }
    else { a += n; b += n; }
  }
  return [del, ins];
}

// --- one line of code -----------------------------------------------------

function wrap(text: string, scopes: string[]): string {
  if (!scopes.length) return esc(text);
  return scopes.map((s) => `<span class="${esc(s)}">`).join("") + esc(text) + "</span>".repeat(scopes.length);
}

// Highlighted segments with word marks laid over them. A mark is the outer
// element, so it can cross highlight spans and the HTML stays well nested.
export function codeHtml(segs: Seg[], marks: Range[], tag: "ins" | "del"): string {
  const cuts = [...new Set(marks.flat())].sort((x, y) => x - y);
  const marked = (p: number) => marks.some(([s, e]) => p >= s && p < e);
  let out = "";
  let pos = 0;
  let open = false;
  for (const seg of segs) {
    let start = 0;
    while (start < seg.text.length) {
      const abs = pos + start;
      const cut = cuts.find((c) => c > abs && c - pos < seg.text.length);
      const end = cut === undefined ? seg.text.length : cut - pos;
      const inMark = marked(abs);
      if (inMark !== open) {
        out += inMark ? `<${tag}>` : `</${tag}>`;
        open = inMark;
      }
      out += wrap(seg.text.slice(start, end), seg.scopes);
      start = end;
    }
    pos += seg.text.length;
  }
  return open ? out + `</${tag}>` : out;
}

function segsFor(v: FileView, side: "old" | "new", lineNo: number, text: string): Seg[] {
  const hl = v.highlight?.(side, lineNo, text);
  // Highlighting comes from a separate fetch of the file, so check it is the same line.
  if (hl && hl.map((s) => s.text).join("") === text) return hl;
  return text ? [{ text, scopes: [] }] : [];
}

const NO_NEWLINE = '<span class="dw-nonl" title="No newline at end of file">⏎̸</span>';

function lineCode(v: FileView, line: DiffLine, marks: Range[] = []): string {
  const side = line.kind === "del" ? "old" : "new";
  const no = (side === "old" ? line.oldNo : line.newNo)!;
  const tag = line.kind === "del" ? "del" : "ins";
  return codeHtml(segsFor(v, side, no, line.text), marks, tag) + (line.noNewline ? NO_NEWLINE : "");
}

// --- rows -----------------------------------------------------------------

const SIGN: Record<DiffLine["kind"], string> = { ctx: " ", add: "+", del: "-" };
const num = (n: number | null) => `<td class="dw-num">${n ?? ""}</td>`;
const code = (kind: string, sign: string, html: string) =>
  `<td class="dw-code dw-${kind}" data-sign="${sign}">${html}</td>`;
const EMPTY = '<td class="dw-num dw-empty"></td><td class="dw-code dw-empty"></td>';

// A hunk body as runs: single context lines, and blocks of removals followed
// by the additions that replace them, in git's order.
type Block = { ctx: DiffLine } | { dels: DiffLine[]; adds: DiffLine[] };

function blocks(lines: DiffLine[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i]!;
    if (l.kind === "ctx") { out.push({ ctx: l }); i++; continue; }
    const dels: DiffLine[] = [];
    const adds: DiffLine[] = [];
    while (i < lines.length && lines[i]!.kind === "del") dels.push(lines[i++]!);
    while (i < lines.length && lines[i]!.kind === "add") adds.push(lines[i++]!);
    out.push({ dels, adds });
  }
  return out;
}

function hunkRows(v: FileView, hunk: Hunk): string {
  let out = "";
  for (const b of blocks(hunk.lines)) {
    if ("ctx" in b) {
      out += lineRow(v, b.ctx, lineCode(v, b.ctx));
      continue;
    }
    const marks = b.dels.map((d, i) => (b.adds[i] ? wordMarks(d.text, b.adds[i].text) : null));
    if (v.sideBySide) {
      for (let i = 0; i < Math.max(b.dels.length, b.adds.length); i++) {
        const d = b.dels[i];
        const a = b.adds[i];
        const left = d ? num(d.oldNo) + code("del", "-", lineCode(v, d, marks[i]?.[0])) : EMPTY;
        const right = a ? num(a.newNo) + code("add", "+", lineCode(v, a, marks[i]?.[1])) : EMPTY;
        out += `<tr>${left}${right}</tr>`;
      }
    } else {
      b.dels.forEach((d, i) => { out += lineRow(v, d, lineCode(v, d, marks[i]?.[0])); });
      b.adds.forEach((a, i) => { out += lineRow(v, a, lineCode(v, a, marks[i]?.[1])); });
    }
  }
  return out;
}

// One line in the line-by-line layout, or a context line in either layout.
function lineRow(v: FileView, l: DiffLine, html: string): string {
  if (v.sideBySide) {
    return `<tr>${num(l.oldNo)}${code("ctx", " ", html)}${num(l.newNo)}${code("ctx", " ", html)}</tr>`;
  }
  return `<tr>${num(l.oldNo)}${num(l.newNo)}${code(l.kind, SIGN[l.kind], html)}</tr>`;
}

// Lines an expander has revealed: context, read from the end side's text.
function revealedRows(v: FileView, gap: Gap, from: number, to: number): string {
  let out = "";
  for (let n = from; n <= to; n++) {
    const text = v.endLines![n - 1] ?? "";
    const line: DiffLine = { kind: "ctx", oldNo: n + gap.offset, newNo: n, text, noNewline: false };
    const html = lineCode(v, line);
    out += lineRow(v, line, html).replace("<tr>", '<tr class="dw-revealed">');
  }
  return out;
}

function hunkHeaderRow(v: FileView, text: string, buttons: Expander[]): string {
  const btns = buttons
    .map((b) =>
      `<button type="button" class="dw-expand" data-from="${b.reveal.from}" data-to="${b.reveal.to}"` +
      ` title="${esc(b.title)}" aria-label="${esc(b.title)}">${b.label}</button>`)
    .join("");
  const gutter = `<td class="dw-gutter"${v.sideBySide ? "" : ' colspan="2"'}>${btns}</td>`;
  const span = v.sideBySide ? ' colspan="3"' : "";
  return `<tr class="dw-hunk">${gutter}<td class="dw-code dw-hunk-text"${span}>${esc(text)}</td></tr>`;
}

function tableRows(v: FileView): string {
  const f = v.file;
  const expandable = !!v.endLines && textMatches(f, v.endLines);
  const endCount = expandable ? v.endLines!.length : null;
  let out = "";
  for (const gap of gaps(f, endCount)) {
    const header = gap.before === null ? "" : f.hunks[gap.before]!.header;
    if (!expandable) {
      if (gap.before !== null) out += hunkHeaderRow(v, header, []);
    } else {
      const ps = pieces(gap, v.revealed ?? []);
      const hiddenCount = ps.filter((p) => !p.shown).length;
      let hiddenSeen = 0;
      // An empty gap still gets its hunk's header. A gap the user has opened
      // all the way gets none: the two hunks then read as one.
      if (!ps.length && gap.before !== null) out += hunkHeaderRow(v, header, []);
      for (const p of ps) {
        if (p.shown) { out += revealedRows(v, gap, p.from, p.to); continue; }
        hiddenSeen++;
        // The header belongs next to its hunk, so only the last hidden run carries it.
        const text = hiddenSeen === hiddenCount ? header : "";
        out += hunkHeaderRow(v, text, expanders(p, gap.before === null));
      }
    }
    if (gap.before !== null) out += hunkRows(v, f.hunks[gap.before]!);
  }
  return out;
}

// --- a whole file ---------------------------------------------------------

function infoText(f: DiffFile): string[] {
  const out = [...f.notes];
  if (f.binary) out.push("Binary file");
  if (f.oldMode && f.newMode && f.oldMode !== f.newMode) out.push(`Mode changed from ${f.oldMode} to ${f.newMode}`);
  if (!f.hunks.length && !f.binary) {
    if (f.status === "renamed") out.push(`Renamed without changes${f.similarity === null ? "" : ` (${f.similarity}% similar)`}`);
    else if (f.status === "copied") out.push(`Copied without changes${f.similarity === null ? "" : ` (${f.similarity}% similar)`}`);
    else if (f.status === "added") out.push("Empty file added");
    else if (f.status === "deleted") out.push("Empty file deleted");
  }
  return out;
}

export function renderBody(v: FileView): string {
  const info = infoText(v.file).map((t) => `<div class="dw-info">${esc(t)}</div>`).join("");
  if (!v.file.hunks.length) return info;
  const cols = v.sideBySide
    ? '<col class="dw-col-num"><col><col class="dw-col-num"><col>'
    : '<col class="dw-col-num"><col class="dw-col-num"><col>';
  const layout = v.sideBySide ? "dw-split" : "dw-unified";
  return `${info}<table class="dw-diff ${layout}"><colgroup>${cols}</colgroup><tbody>${tableRows(v)}</tbody></table>`;
}

const TAGS: Partial<Record<DiffFile["status"], string>> = {
  added: "added", deleted: "deleted", renamed: "renamed", copied: "copied",
};

export function renderFile(v: FileView, attrs: { id: string; path: string; name: string }): string {
  const tag = TAGS[v.file.status];
  return (
    `<div class="dw-file" id="${esc(attrs.id)}" data-path="${esc(attrs.path)}">` +
    `<div class="dw-file-header"><span class="dw-file-name">${esc(attrs.name)}</span>` +
    (tag ? `<span class="dw-tag dw-tag-${tag}">${tag}</span>` : "") +
    `</div><div class="dw-file-body">${renderBody(v)}</div></div>`
  );
}
