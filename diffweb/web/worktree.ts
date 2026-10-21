import { refresh as refreshKeys } from "./keys.ts";
import { filePath, parseDiff, type DiffFile } from "./diff/parse.ts";
import { addInterval, esc, renderBody, renderFile, type FileView, type Interval } from "./diff/render.ts";
import { fileLines, highlightFile, highlightLine, languageFor, type HlLine } from "./diff/highlight.ts";

// --- what the server sends ------------------------------------------------

type ReviewState = "reviewed" | "changed" | "new";

interface FileInfo {
  path: string;                 // numstat spelling: `dir/{old => new}` for a rename
  added: number | null;
  removed: number | null;
  binary: boolean;
  image: boolean;
  noisy: boolean;
  untracked?: boolean;
  status: string;               // git's status letter: A, D, M, R...
  old_path: string;
}

interface DiffResponse {
  error?: string;
  renderer: "line" | "structural";
  start: string;
  end: string;
  base: string;
  merge: boolean;
  remerge: boolean;
  files: FileInfo[];
  hidden_tests: { files: number; added: number; removed: number } | null;
  review: Record<string, ReviewState>;
  shas: Record<string, string>;
  lazy: boolean;
  diff: string;
  profile_id?: string;
}

interface Commit {
  sha: string;
  short: string;
  subject: string;
  when: string;
  merge: boolean;
}

interface CommitsResponse {
  error?: string;
  base: string;
  commits: Commit[];
}

type Query = Record<string, string>;

// --- page wiring ----------------------------------------------------------

const byId = <T extends HTMLElement | null>(id: string) => document.getElementById(id) as T;

const el = document.querySelector<HTMLElement>(".controls")!;
const WT = el.dataset.wt!;
const api = (p: string, q: Query) => `/api/w/${WT}/${p}?${new URLSearchParams(q)}`;

const $base = byId<HTMLInputElement>("base");
const $start = byId<HTMLSelectElement>("start");
const $end = byId<HTMLSelectElement>("end");
const $sbs = byId<HTMLInputElement>("sbs");
const $hideReviewed = byId<HTMLInputElement>("hide-reviewed");
const $hideTests = byId<HTMLInputElement | null>("hide-tests");
const $structural = byId<HTMLInputElement | null>("structural");
const $trueDiff = byId<HTMLInputElement>("true-diff");
const $status = byId<HTMLElement>("status");
const $summary = byId<HTMLElement>("summary");
const $diff = byId<HTMLElement>("diff");
const $toggleAll = byId<HTMLButtonElement>("toggle-all");
const $scopes = byId<HTMLElement>("scopes");
// Above this many bytes a file is not highlighted whole (limits.max_highlight_bytes).
const MAX_HIGHLIGHT = Number(el.dataset.highlightMax) || 1_000_000;

const MERGE_BASE = "";           // empty start means "merge base with base ref"
const WORKTREE = "";             // empty end means "working tree"
const HEAD = "HEAD";             // the last commit
const BEFORE_HEAD = "HEAD~1";    // the commit before it
const UPSTREAM = "@{upstream}";  // the last commit the remote has
const INDEX = "INDEX";           // end at the staging area

// The ranges worth a one-click shortcut, keyed by the button's data-scope.
const SCOPES: Record<string, { start: string; end: string }> = {
  working: { start: HEAD, end: WORKTREE },
  branch: { start: MERGE_BASE, end: WORKTREE },
  "last-commit": { start: BEFORE_HEAD, end: HEAD },
  unpushed: { start: UPSTREAM, end: HEAD },
  staged: { start: MERGE_BASE, end: INDEX },
};

// What the last render drew, for the handlers that run after it.
let currentReview: Record<string, ReviewState> = {};
let shas: Record<string, string> = {};
let fileInfo: Record<string, FileInfo> = {};    // keyed on the numstat spelling
let infoByPath: Record<string, FileInfo> = {};  // keyed on the plain path the parser reports
let currentRange = { start: "", end: "", remerge: false };  // as the server resolved them
let canExpand = false;      // expanders only on the line diff; see INTENT-expand-context.md
let seededNoise = false;    // auto-collapse noisy files once, not on every render

// --- localStorage, which can be full, corrupt or missing ------------------

function loadSet(key: string): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) || "[]") as string[]);
  } catch {
    return new Set();
  }
}

function saveSet(key: string, values: Set<string>): void {
  try {
    localStorage.setItem(key, JSON.stringify([...values]));
  } catch { /* private mode; the state still holds for this page load */ }
}

// A checkbox whose state is remembered across pages.
function rememberCheckbox(box: HTMLInputElement, key: string, onChange: () => void): void {
  box.checked = localStorage.getItem(key) === "1";
  box.addEventListener("change", () => {
    localStorage.setItem(key, box.checked ? "1" : "0");
    onChange();
  });
}

const params = new URLSearchParams(location.search);

rememberCheckbox($sbs, "diffweb.sbs", render);
rememberCheckbox($hideReviewed, "diffweb.hide-reviewed", applyHideReviewed);
$structural?.addEventListener("change", render);
// Only meaningful on a single merge commit; the server decides when that is
// and the box is shown or hidden from its answer. It rides in the URL so a
// merge's true diff can be linked to, but is not remembered across pages:
// the resolution is the default reading every time.
$trueDiff.checked = params.get("remerge") === "0";
$trueDiff.addEventListener("change", render);
// The non-test diff. The server leaves the test files out, so the counts and
// the lazy limit are for what is on screen. It rides in the URL like the
// true-diff tick, and for the same reason is not remembered: a page that
// silently hides the tests the next time is easy to misread.
if ($hideTests) {
  $hideTests.checked = params.get("tests") === "0";
  $hideTests.addEventListener("change", render);
}

// --- collapsing -----------------------------------------------------------

// Which files are collapsed, remembered per worktree so a re-render (live
// reload, switching to side-by-side) does not throw the state away.
const COLLAPSE_KEY = `diffweb.collapsed.${WT}`;
const collapsed = loadSet(COLLAPSE_KEY);
const saveCollapsed = () => saveSet(COLLAPSE_KEY, collapsed);

// Lockfiles and generated code start collapsed, but only the first time we see
// them: after that the user's own choice wins, including deliberately opening one.
function seedNoise(files: FileInfo[]): void {
  if (seededNoise) return;
  seededNoise = true;
  const seenKey = `diffweb.noise-seeded.${WT}`;
  const alreadySeeded = loadSet(seenKey);
  const fresh = files.filter((f) => f.noisy && !alreadySeeded.has(f.path));
  if (!fresh.length) return;
  for (const f of fresh) {
    collapsed.add(f.path);
    alreadySeeded.add(f.path);
  }
  saveCollapsed();
  saveSet(seenKey, alreadySeeded);
}

function markCollapsed(wrapper: HTMLElement, path: string, value: boolean): void {
  wrapper.classList.toggle("collapsed", value);
  if (value) collapsed.add(path); else collapsed.delete(path);
}

function setCollapsed(wrapper: HTMLElement, path: string, value: boolean): void {
  markCollapsed(wrapper, path, value);
  saveCollapsed();
  refreshToggleAll();
}

const fileWrappers = () => [...$diff.querySelectorAll<HTMLElement>(".dw-file")];

function refreshToggleAll(): boolean {
  const wrappers = fileWrappers();
  const anyOpen = wrappers.some((w) => !w.classList.contains("collapsed"));
  $toggleAll.textContent = anyOpen ? "collapse all" : "expand all";
  $toggleAll.hidden = wrappers.length === 0;
  return anyOpen;
}

$toggleAll.addEventListener("click", () => {
  // If anything is still open, the button closes everything; otherwise it opens.
  const anyOpen = refreshToggleAll();
  for (const w of fileWrappers()) markCollapsed(w, w.dataset.path!, anyOpen);
  saveCollapsed();
  refreshToggleAll();
});

// The toolbar wraps on narrow viewports, so sticky file headers cannot assume
// a fixed offset.
function trackToolbarHeight(): void {
  const bar = document.querySelector<HTMLElement>(".topbar")!;
  const apply = () =>
    document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
  apply();
  new ResizeObserver(apply).observe(bar);
}
trackToolbarHeight();

function status(text: string, busy = false): void {
  $status.textContent = text;
  $status.classList.toggle("busy", busy);
}

function syncUrl(): void {
  const q = new URLSearchParams();
  if ($base.value) q.set("base", $base.value);
  if ($start.value) q.set("start", $start.value);
  if ($end.value) q.set("end", $end.value);
  if ($trueDiff.checked) q.set("remerge", "0");
  if ($hideTests?.checked) q.set("tests", "0");
  history.replaceState(null, "", `${location.pathname}?${q}`);
}

// --- loading --------------------------------------------------------------

async function loadCommits(): Promise<boolean> {
  const r = await fetch(api("commits", { base: $base.value }));
  const data = (await r.json()) as CommitsResponse;
  if (!r.ok) { status(data.error || `cannot resolve base ref "${$base.value}"`); return false; }
  const want = { start: el.dataset.start, end: el.dataset.end };
  // A literal HEAD option, rather than the newest sha, so the shortcut still
  // works on a branch sitting on its own merge base with no commits listed.
  $start.replaceChildren(
    new Option(`merge base with ${data.base}`, MERGE_BASE),
    new Option("HEAD (last commit)", HEAD),
    new Option("HEAD~1 (before the last commit)", BEFORE_HEAD),
    new Option("@{upstream} (last pushed commit)", UPSTREAM),
  );
  $end.replaceChildren(
    new Option("working tree", WORKTREE),
    new Option("staging area", INDEX),
    new Option("HEAD (last commit)", HEAD),
  );
  for (const c of data.commits) {
    const label = `${c.short}  ${c.subject}${c.merge ? "  [merge]" : ""}  (${c.when})`;
    $start.appendChild(new Option(label, c.sha));
    $end.appendChild(new Option(label, c.sha));
  }
  $start.value = want.start || MERGE_BASE;
  $end.value = want.end || WORKTREE;
  syncScopes();
  el.dataset.start = el.dataset.end = "";
  status("");
  return true;
}

// Anything that throws mid-render (a missing vendored library, a diff the
// parser cannot read) would otherwise leave "loading diff…" up forever.
async function render(): Promise<void> {
  try {
    await renderDiff();
  } catch (err) {
    console.error(err);
    status("loading diff failed");
  }
}

async function renderDiff(): Promise<void> {
  syncUrl();
  status("loading diff…", true);
  const q: Query = { base: $base.value };
  if ($start.value) q.start = $start.value;
  if ($end.value) q.end = $end.value;
  if ($structural?.checked) q.renderer = "structural";
  if ($trueDiff.checked) q.remerge = "false";
  if ($hideTests?.checked) q.tests = "false";

  const fetchStart = performance.now();
  const r = await fetch(api("diff", q));
  const data = (await r.json()) as DiffResponse;
  const fetchMs = performance.now() - fetchStart;
  if (!r.ok || data.error) {
    $diff.replaceChildren();
    const warn = document.createElement("span");
    warn.className = "warn";
    warn.textContent = data.error ?? `loading diff failed (${r.status})`;
    $summary.replaceChildren(warn);
    status("");
    return;
  }

  // The tickbox exists only while the range is one merge commit.
  $trueDiff.parentElement!.hidden = !data.merge;

  const files = data.files || [];
  currentReview = data.review || {};
  shas = data.shas || {};
  currentRange = { start: data.start, end: data.end, remerge: !!data.remerge };
  canExpand = data.renderer === "line" && !data.remerge;
  fileInfo = Object.fromEntries(files.map((f) => [f.path, f]));
  infoByPath = Object.fromEntries(files.map((f) => [renameTarget(f.path), f]));
  seedNoise(files);
  const added = files.reduce((n, f) => n + (f.added || 0), 0);
  const removed = files.reduce((n, f) => n + (f.removed || 0), 0);
  const states = Object.values(currentReview);
  const reviewed = states.filter((v) => v === "reviewed").length;
  const changed = states.filter((v) => v === "changed").length;
  $summary.innerHTML =
    `${files.length} files · <span class="add">+${added}</span> <span class="del">−${removed}</span>` +
    ` · ${reviewed}/${files.length} reviewed` +
    (changed ? ` · <span class="warn">${changed} changed since you reviewed</span>` : "") +
    testsNote(data) + mergeNote(data) + structuralNote(data);

  const renderStart = performance.now();
  textObserver.disconnect();
  if (data.lazy) {
    $diff.replaceChildren();
    $summary.innerHTML += ` · <span class="warn">diff is large — loading per file</span>`;
    await renderLazy(files, q);
  } else {
    draw(data.diff);
  }
  status("");
  reportProfile(data.profile_id, fetchMs, performance.now() - renderStart);
}

// Say what "hide tests" took out, so the page never looks like the whole range.
function testsNote(data: DiffResponse): string {
  const t = data.hidden_tests;
  if (!t) return "";
  if (!t.files) return ` · <span class="merge-note">no test files in this range</span>`;
  return ` · <span class="merge-note">${t.files} test file${t.files === 1 ? "" : "s"} hidden` +
    ` (<span class="add">+${t.added}</span> <span class="del">−${t.removed}</span>)</span>`;
}

// Say which of the two readings of a merge commit is on screen, since the
// remerge one can legitimately be empty (a clean merge has no resolution).
function mergeNote(data: DiffResponse): string {
  if (!data.merge) return "";
  return data.remerge
    ? ` · <span class="merge-note">merge commit: showing only the conflict resolution` +
      ` (<code>--remerge-diff</code>); tick <i>true diff</i> for everything it brought in</span>`
    : ` · <span class="merge-note">merge commit: showing the full diff against its first parent</span>`;
}

// The structural diff is drawn by the same renderer, so say what is different
// about what it shows: lines difftastic considers unchanged are context.
function structuralNote(data: DiffResponse): string {
  if (data.renderer !== "structural") return "";
  return ` · <span class="merge-note">structural: only syntactic changes are marked;` +
    ` reformatting shows as context (difftastic)</span>`;
}

// The server can time its own work but not the two stages the user waits
// longest for, so the page posts them back and the profile is the whole journey.
function reportProfile(profileId: string | undefined, fetchMs: number, renderMs: number): void {
  if (!profileId) return;
  const body = JSON.stringify({ profile_id: profileId, fetch_ms: fetchMs, render_ms: renderMs });
  // keepalive: the report must survive the user navigating straight back out.
  fetch("/api/profile", {
    method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true,
  }).catch(() => { /* profiling is never worth breaking the page over */ });
}

// --- drawing --------------------------------------------------------------

// What each drawn file was drawn from, so it can be drawn again in place.
interface Drawn {
  view: FileView;
  key: string;  // the plain path: review state, blob shas and expansions use it
}
const drawn = new WeakMap<HTMLElement, Drawn>();

// Lines the user has expanded, per file. Keyed on the end side's blob sha:
// expanded line numbers mean nothing once the file has changed.
const revealedByFile = new Map<string, Interval[]>();
const revealKey = (key: string) => `${key}\0${shaFor(key)}`;

// A stable anchor per path, for the file list's links.
function anchorFor(path: string): string {
  let h = 5381;
  for (let i = 0; i < path.length; i++) h = ((h * 33) ^ path.charCodeAt(i)) >>> 0;
  return `file-${h.toString(36)}`;
}

function fileListHtml(files: DiffFile[]): string {
  if (!files.length) return "";
  const items = files.map((f) => {
    const path = infoByPath[filePath(f)]?.path ?? filePath(f);
    return `<li><a href="#${anchorFor(path)}">${esc(displayName(path))}</a></li>`;
  });
  return `<details class="dw-file-list"><summary>files (${files.length})</summary><ol>${items.join("")}</ol></details>`;
}

const displayName = (path: string) => path.replace(" => ", " → ");

// `target` is one file's holder in a lazy render; the whole page otherwise.
function draw(diffText: string, target?: HTMLElement): void {
  const node = target ?? $diff;
  const files = parseDiff(diffText);
  const html: string[] = target ? [] : [fileListHtml(files)];
  const made: [string, Drawn, boolean][] = [];
  for (const file of files) {
    const key = filePath(file);
    const path = infoByPath[key]?.path ?? key;
    const d: Drawn = {
      key,
      view: { file, sideBySide: $sbs.checked, revealed: revealedByFile.get(revealKey(key)) ?? [] },
    };
    const id = anchorFor(path);
    const ready = prepare(d);
    html.push(renderFile(d.view, { id, path, name: displayName(path) }));
    made.push([id, d, ready]);
  }
  node.innerHTML = html.join("");
  for (const [id, d, ready] of made) {
    const wrapper = node.querySelector<HTMLElement>(`#${id}`);
    if (!wrapper) continue;
    drawn.set(wrapper, d);
    if (!ready) textObserver.observe(wrapper);
  }
  if (!target) decorateFiles(node);
}

async function renderLazy(files: FileInfo[], q: Query): Promise<void> {
  for (const f of files) {
    const holder = document.createElement("div");
    $diff.appendChild(holder);
    const r = await fetch(api("diff", { ...q, files: f.path }));
    const d = (await r.json()) as DiffResponse;
    if (d.diff) draw(d.diff, holder);
  }
  decorateFiles($diff);
}

function redrawBody(wrapper: HTMLElement): void {
  const d = drawn.get(wrapper);
  const body = wrapper.querySelector(".dw-file-body");
  if (d && body) body.innerHTML = renderBody(d.view);
}

// --- highlighting and expanding ---------------------------------------------
//
// A file is drawn plain first. When it comes near the viewport, the page
// fetches the text of its two sides, highlights each side whole, and draws
// the file again. The end side's text is also what the expanders show.

// One side of a file, kept for the life of the page so that a redraw (the
// side-by-side toggle, live reload) draws at once, with nothing to fetch. The
// keys cannot go stale: the old side is a commit and a path, and the new side
// is its blob sha.
interface Side {
  lines: string[];
  hl: HlLine[] | null;  // null when the language is unknown or the file is too big
}
const sideFetches = new Map<string, Promise<Side | null>>();
const sidesReady = new Map<string, Side | null>();

const textObserver = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    textObserver.unobserve(e.target);
    enrich(e.target as HTMLElement).catch((err) => console.error(err));
  }
}, { rootMargin: "1200px 0px" });

async function fetchText(rev: string, path: string): Promise<string | null> {
  const r = await fetch(api("text", { rev, path }));
  return r.ok ? r.text() : null;
}

interface SideWant { key: string | null; rev: string; path: string }

// Which sides a file needs, and the cache key of each (null: do not cache).
function wantedSides(d: Drawn): { lang: string | null; expand: boolean; old?: SideWant; new?: SideWant } {
  const f = d.view.file;
  const lang = languageFor(filePath(f));
  const expand = canExpand && f.status !== "deleted";
  const out: ReturnType<typeof wantedSides> = { lang, expand };
  if (f.binary || !f.hunks.length) return out;
  // The remerge diff's old side is git's own merge, which is in no commit.
  if (lang && f.status !== "added" && !currentRange.remerge) {
    out.old = { key: `${lang}:${currentRange.start}:${f.oldPath}`, rev: currentRange.start, path: f.oldPath };
  }
  if ((lang || expand) && f.status !== "deleted") {
    const sha = shas[d.key];
    out.new = { key: sha ? `${lang}:${sha}` : null, rev: currentRange.end, path: f.newPath };
  }
  return out;
}

function loadSide(want: SideWant, lang: string | null): Promise<Side | null> {
  const cached = want.key === null ? undefined : sideFetches.get(want.key);
  if (cached) return cached;
  const p = fetchText(want.rev, want.path).then((text) => text === null ? null : {
    lines: fileLines(text),
    hl: lang && text.length <= MAX_HIGHLIGHT ? highlightFile(text, lang) : null,
  });
  if (want.key !== null) {
    sideFetches.set(want.key, p);
    p.then((side) => sidesReady.set(want.key!, side), () => sideFetches.delete(want.key!));
  }
  return p;
}

function applySides(d: Drawn, lang: string | null, expand: boolean, old: Side | null, now: Side | null): void {
  if (lang) {
    d.view.highlight = (side, no, text): HlLine => {
      const line = (side === "old" ? old : now)?.hl?.[no - 1];
      if (line && line.map((s) => s.text).join("") === text) return line;
      return highlightLine(text, lang);
    };
  }
  if (expand && now) d.view.endLines = now.lines;
}

// Use what the cache already has. False when a side still has to be fetched.
function prepare(d: Drawn): boolean {
  const w = wantedSides(d);
  if (!w.old && !w.new) return true;
  const ready = (s?: SideWant) => !s || (s.key !== null && sidesReady.has(s.key));
  if (!ready(w.old) || !ready(w.new)) return false;
  const get = (s?: SideWant) => (s ? sidesReady.get(s.key!) ?? null : null);
  applySides(d, w.lang, w.expand, get(w.old), get(w.new));
  return true;
}

async function enrich(wrapper: HTMLElement): Promise<void> {
  const d = drawn.get(wrapper);
  if (!d) return;
  const w = wantedSides(d);
  if (!w.old && !w.new) return;
  const [old, now] = await Promise.all([
    w.old ? loadSide(w.old, w.lang) : null,
    w.new ? loadSide(w.new, w.lang) : null,
  ]);
  if (drawn.get(wrapper) !== d) return;
  applySides(d, w.lang, w.expand, old, now);
  redrawBody(wrapper);
}

$diff.addEventListener("click", (e) => {
  const btn = (e.target as Element).closest<HTMLButtonElement>(".dw-expand");
  const wrapper = btn?.closest<HTMLElement>(".dw-file");
  const d = wrapper && drawn.get(wrapper);
  if (!btn || !wrapper || !d) return;
  const next = addInterval(d.view.revealed ?? [], { from: Number(btn.dataset.from), to: Number(btn.dataset.to) });
  revealedByFile.set(revealKey(d.key), next);
  d.view.revealed = next;
  redrawBody(wrapper);
});

// --- decorating each file -------------------------------------------------

function tag(className: string, text: string, title?: string): HTMLSpanElement {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  if (title) span.title = title;
  return span;
}

// The collapse control, the counts and the review tick on each file header.
function decorateFiles(root: HTMLElement): void {
  for (const wrapper of root.querySelectorAll<HTMLElement>(".dw-file")) {
    const header = wrapper.querySelector<HTMLElement>(".dw-file-header");
    if (!header || header.querySelector(".chevron")) continue;
    // The numstat spelling (`dir/{old => new}`) is the key for collapsing.
    const path = wrapper.dataset.path!;
    // Review state and blob shas come from `git diff --raw`, keyed on the plain new path.
    const reviewKey = renameTarget(path);

    const chevron = document.createElement("button");
    chevron.className = "chevron";
    chevron.type = "button";
    chevron.setAttribute("aria-label", "collapse file");
    header.prepend(chevron);

    // The whole header is the hit target, minus the controls sitting on it.
    header.addEventListener("click", (e) => {
      if ((e.target as Element).closest(".review-tick, a")) return;
      setCollapsed(wrapper, path, !wrapper.classList.contains("collapsed"));
    });

    const info = fileInfo[path];
    if (info) {
      const stats = document.createElement("span");
      stats.className = "file-stats";
      stats.innerHTML = info.binary
        ? '<span class="dim">binary</span>'
        : `<span class="add">+${info.added}</span> <span class="del">−${info.removed}</span>`;
      header.appendChild(stats);
      if (info.image) queueImagePane(wrapper, info);
      if (info.untracked) {
        header.appendChild(tag("untracked-tag", "untracked", "not yet added to git; shown as a new file"));
      }
      if (info.noisy) {
        header.appendChild(
          tag("noise-tag", "generated", "matches noise.collapse_by_default; collapsed on first sight"));
      }
    }

    const stateForFile = currentReview[reviewKey];
    if (stateForFile !== undefined) {
      const label = document.createElement("label");
      label.className = "review-tick";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = stateForFile === "reviewed";
      label.append(box, document.createTextNode("reviewed"));
      header.appendChild(label);

      if (stateForFile === "changed") {
        header.appendChild(tag("badge-changed", "changed since you reviewed"));
        collapsed.delete(path);  // reopen it: there is something new to look at
      } else if (box.checked) {
        collapsed.add(path);
      }

      box.addEventListener("change", () => {
        setCollapsed(wrapper, path, box.checked);
        // Keep the filter honest without waiting for the next render.
        wrapper.classList.toggle("is-reviewed", box.checked);
        applyHideReviewed();
        fetch(api("reviewed", { path: reviewKey, blob_sha: shaFor(reviewKey), reviewed: String(box.checked) }),
              { method: "POST" });
      });
    }

    wrapper.classList.toggle("collapsed", collapsed.has(path));
    wrapper.classList.toggle("is-reviewed", stateForFile === "reviewed");
  }
  saveCollapsed();
  refreshToggleAll();
  refreshKeys();
  applyHideReviewed();
}

// --- images ---------------------------------------------------------------
//
// git can only say "Binary files differ" about an image, so the page fetches
// each side from /blob and shows them next to each other. The panes are added
// after the diff has painted, and each <img> is loading="lazy", so a branch
// full of screenshots costs nothing until you scroll to one.

function blobUrl(rev: string, path: string): string {
  return api("blob", { rev, path });
}

function queueImagePane(wrapper: HTMLElement, info: FileInfo): void {
  requestAnimationFrame(() => addImagePane(wrapper, info));
}

function addImagePane(wrapper: HTMLElement, info: FileInfo): void {
  const body = wrapper.querySelector(".dw-file-body");
  if (!body || wrapper.querySelector(".image-diff")) return;
  const newPath = renameTarget(info.path);
  const sides: { label: string; url: string; cls: string }[] = [];
  // The remerge diff's "before" is the mechanical merge, which exists nowhere
  // we can fetch it from, so only the resolved result is shown.
  if (info.status !== "A" && !currentRange.remerge) {
    sides.push({ label: "before", url: blobUrl(currentRange.start, info.old_path || newPath), cls: "del" });
  }
  if (info.status !== "D") {
    sides.push({ label: "after", url: blobUrl(currentRange.end, newPath), cls: "add" });
  }
  const pane = document.createElement("div");
  pane.className = "image-diff";
  for (const side of sides) {
    const fig = document.createElement("figure");
    fig.className = `image-side ${side.cls}`;
    const cap = document.createElement("figcaption");
    cap.textContent = side.label;
    const link = document.createElement("a");
    link.href = side.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.title = "open full size";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = `${side.label}: ${newPath}`;
    img.src = side.url;
    img.addEventListener("load", () => {
      cap.textContent = `${side.label} · ${img.naturalWidth}×${img.naturalHeight}`;
    });
    img.addEventListener("error", () => {
      cap.textContent = `${side.label} · could not load`;
      fig.classList.add("failed");
    });
    link.appendChild(img);
    fig.append(cap, link);
    pane.appendChild(fig);
  }
  // The pane follows the body rather than sitting in it, so drawing the body
  // again (highlighting, expanding) leaves it alone. A binary file's body is
  // only the "Binary file" note, which the pictures replace; an SVG keeps its
  // text diff and gets the pictures underneath.
  if (info.binary) body.remove();
  wrapper.appendChild(pane);
}

// git's numstat spelling of a rename, `dir/{old => new}.ext`, back to the new path.
function renameTarget(name: string): string {
  const m = name.match(/^(.*)\{(.*) => (.*)\}(.*)$/);
  if (m) return `${m[1]}${m[3]}${m[4]}`;
  return name.includes(" => ") ? name.split(" => ")[1]! : name;
}

function shaFor(path: string): string { return shas[path] || "worktree"; }

// Late in a review most files are ticked off; hiding them leaves just the work.
function applyHideReviewed(): void {
  const on = $hideReviewed.checked;
  document.body.classList.toggle("hide-reviewed", on);
  const hidden = on ? $diff.querySelectorAll(".dw-file.is-reviewed").length : 0;
  $hideReviewed.parentElement!.title = hidden ? `${hidden} reviewed files hidden` : "";
  refreshKeys();
}

// --- range controls -------------------------------------------------------

// Light up whichever shortcut matches the range that is actually selected.
function syncScopes(): void {
  for (const btn of $scopes.querySelectorAll<HTMLElement>(".scope-btn")) {
    const want = SCOPES[btn.dataset.scope!];
    const on = !!want && $start.value === want.start && $end.value === want.end;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

$scopes.addEventListener("click", (e) => {
  const btn = (e.target as Element).closest<HTMLElement>(".scope-btn");
  const want = btn && SCOPES[btn.dataset.scope!];
  if (!want) return;
  $start.value = want.start;
  $end.value = want.end;
  syncScopes();
  render();
});

$base.addEventListener("change", async () => {
  const r = await fetch(api("base", { base: $base.value }), { method: "POST" });
  if (!r.ok) {
    const { error } = (await r.json()) as { error?: string };
    status(error || `cannot resolve base ref "${$base.value}"`);
    return;
  }
  if (await loadCommits()) render();
});
$start.addEventListener("change", () => { syncScopes(); render(); });
$end.addEventListener("change", () => { syncScopes(); render(); });

if (el.dataset.live === "1") {
  const es = new EventSource(`/events/${WT}`);
  es.addEventListener("changed", () => {
    const y = window.scrollY;
    render().then(() => window.scrollTo(0, y));
  });
}

loadCommits().then((ok) => { if (ok) render(); });
