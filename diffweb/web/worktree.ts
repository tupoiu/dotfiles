import { refresh as refreshKeys } from "./keys.ts";
import { diffwebRenderOptions } from "./render-options.ts";

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
let fileInfo: Record<string, FileInfo> = {};
let currentRange = { start: "", end: "", remerge: false };  // as the server resolved them
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

const fileWrappers = () => [...$diff.querySelectorAll<HTMLElement>(".d2h-file-wrapper")];

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

// Anything that throws mid-render (a missing vendored library, a diff2html
// parse error) would otherwise leave "loading diff…" up forever.
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
  fileInfo = Object.fromEntries(files.map((f) => [f.path, f]));
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

// `target` is one file's holder in a lazy render; the whole page otherwise.
function draw(diffText: string, target?: HTMLElement): void {
  const node = target ?? $diff;
  const ui = new Diff2HtmlUI(node, diffText,
    diffwebRenderOptions({ sideBySide: $sbs.checked, drawFileList: !target }));
  ui.draw();
  ui.highlightCode();
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

// --- decorating each file -------------------------------------------------

function tag(className: string, text: string, title?: string): HTMLSpanElement {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  if (title) span.title = title;
  return span;
}

// diff2html gives us neither a collapse control nor a review affordance, so
// both get bolted onto each file header here.
function decorateFiles(root: HTMLElement): void {
  for (const wrapper of root.querySelectorAll<HTMLElement>(".d2h-file-wrapper")) {
    const nameEl = wrapper.querySelector(".d2h-file-name");
    const header = wrapper.querySelector<HTMLElement>(".d2h-file-header");
    if (!nameEl || !header) continue;
    // diff2html spells a rename `dir/{old → new}` where git's numstat says
    // `dir/{old => new}`; the numstat spelling is the key everywhere else.
    const path = nameEl.textContent!.trim().replace(" → ", " => ");
    wrapper.dataset.path = path;
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
  const body = wrapper.querySelector(".d2h-file-diff, .d2h-files-diff");
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
  // The pane follows the body rather than sitting in it: side-by-side's body
  // is a two-column flex row that would squeeze the pictures into a third.
  // A binary file's body is only the "Binary file" row, which the pictures
  // replace; an SVG keeps its text diff and gets the pictures underneath.
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
  const hidden = on ? $diff.querySelectorAll(".d2h-file-wrapper.is-reviewed").length : 0;
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
