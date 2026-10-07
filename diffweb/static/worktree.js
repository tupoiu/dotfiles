const el = document.querySelector(".controls");
const WT = el.dataset.wt;
const api = (p, q) => `/api/w/${WT}/${p}?${new URLSearchParams(q)}`;

const $base = document.getElementById("base");
const $start = document.getElementById("start");
const $end = document.getElementById("end");
const $sbs = document.getElementById("sbs");
const $hideReviewed = document.getElementById("hide-reviewed");
const $hideTests = document.getElementById("hide-tests");
const $structural = document.getElementById("structural");
const $trueDiff = document.getElementById("true-diff");
const $status = document.getElementById("status");
const $summary = document.getElementById("summary");
const $diff = document.getElementById("diff");

const MERGE_BASE = "";        // empty start means "merge base with base ref"
const WORKTREE = "";          // empty end means "working tree"
const HEAD = "HEAD";          // the last commit
const BEFORE_HEAD = "HEAD~1"; // the commit before it
const UPSTREAM = "@{upstream}";  // the last commit the remote has
const INDEX = "INDEX";        // end at the staging area

// The three ranges worth a one-click shortcut, as {start, end} pairs.
const SCOPES = {
  working: { start: HEAD, end: WORKTREE },
  branch: { start: MERGE_BASE, end: WORKTREE },
  "last-commit": { start: BEFORE_HEAD, end: HEAD },
  unpushed: { start: UPSTREAM, end: HEAD },
  staged: { start: MERGE_BASE, end: INDEX },
};

$sbs.checked = localStorage.getItem("diffweb.sbs") === "1";
$sbs.addEventListener("change", () => {
  localStorage.setItem("diffweb.sbs", $sbs.checked ? "1" : "0");
  render();
});
$structural?.addEventListener("change", render);
// Only meaningful on a single merge commit; the server decides when that is
// and the box is shown or hidden from its answer. It rides in the URL so a
// merge's true diff can be linked to, but is not remembered across pages:
// the resolution is the default reading every time.
$trueDiff.checked = new URLSearchParams(location.search).get("remerge") === "0";
$trueDiff.addEventListener("change", render);
// The non-test diff. The server leaves the test files out, so the counts and
// the lazy limit are for what is on screen. It rides in the URL like the
// true-diff tick, and for the same reason is not remembered: a page that
// silently hides the tests the next time is easy to misread.
if ($hideTests) {
  $hideTests.checked = new URLSearchParams(location.search).get("tests") === "0";
  $hideTests.addEventListener("change", render);
}

$hideReviewed.checked = localStorage.getItem("diffweb.hide-reviewed") === "1";
$hideReviewed.addEventListener("change", () => {
  localStorage.setItem("diffweb.hide-reviewed", $hideReviewed.checked ? "1" : "0");
  applyHideReviewed();
});

// Which files are collapsed, remembered per worktree so a re-render (live
// reload, switching to side-by-side) does not throw the state away.
const COLLAPSE_KEY = `diffweb.collapsed.${WT}`;
let collapsed = new Set();
try {
  collapsed = new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"));
} catch { /* corrupt or unavailable storage just means nothing is collapsed */ }

function saveCollapsed() {
  try {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed]));
  } catch { /* private mode; collapsing still works for this page load */ }
}

// Lockfiles and generated code start collapsed, but only the first time we see
// them: after that the user's own choice wins, including deliberately opening one.
function seedNoise(files) {
  if (seededNoise) return;
  seededNoise = true;
  const seenKey = `diffweb.noise-seeded.${WT}`;
  let alreadySeeded = new Set();
  try {
    alreadySeeded = new Set(JSON.parse(localStorage.getItem(seenKey) || "[]"));
  } catch { /* nothing seeded yet */ }

  let changed = false;
  for (const f of files) {
    if (f.noisy && !alreadySeeded.has(f.path)) {
      collapsed.add(f.path);
      alreadySeeded.add(f.path);
      changed = true;
    }
  }
  if (!changed) return;
  saveCollapsed();
  try {
    localStorage.setItem(seenKey, JSON.stringify([...alreadySeeded]));
  } catch { /* storage unavailable; they just re-collapse next time */ }
}

function setCollapsed(wrapper, path, value) {
  wrapper.classList.toggle("collapsed", value);
  if (value) collapsed.add(path); else collapsed.delete(path);
  saveCollapsed();
  refreshToggleAll();
}

const $toggleAll = document.getElementById("toggle-all");

function refreshToggleAll() {
  const wrappers = [...$diff.querySelectorAll(".d2h-file-wrapper")];
  const anyOpen = wrappers.some((w) => !w.classList.contains("collapsed"));
  $toggleAll.textContent = anyOpen ? "collapse all" : "expand all";
  $toggleAll.hidden = wrappers.length === 0;
  return anyOpen;
}

$toggleAll.addEventListener("click", () => {
  const wrappers = [...$diff.querySelectorAll(".d2h-file-wrapper")];
  // If anything is still open, the button closes everything; otherwise it opens.
  const anyOpen = refreshToggleAll();
  for (const w of wrappers) setCollapsed(w, w.dataset.path, anyOpen);
  refreshToggleAll();
});

// The toolbar wraps on narrow viewports, so sticky file headers cannot assume
// a fixed offset.
function trackToolbarHeight() {
  const bar = document.querySelector(".topbar");
  const apply = () =>
    document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
  apply();
  new ResizeObserver(apply).observe(bar);
}
trackToolbarHeight();

function status(text, busy) {
  $status.textContent = text;
  $status.classList.toggle("busy", !!busy);
}

function syncUrl() {
  const q = new URLSearchParams();
  if ($base.value) q.set("base", $base.value);
  if ($start.value) q.set("start", $start.value);
  if ($end.value) q.set("end", $end.value);
  if ($trueDiff.checked) q.set("remerge", "0");
  if ($hideTests?.checked) q.set("tests", "0");
  history.replaceState(null, "", `${location.pathname}?${q}`);
}

async function loadCommits() {
  const r = await fetch(api("commits", { base: $base.value }));
  const data = await r.json();
  if (!r.ok) { status(data.error || `cannot resolve base ref "${$base.value}"`); return false; }
  const want = { start: el.dataset.start, end: el.dataset.end };
  $start.innerHTML = `<option value="">merge base with ${data.base}</option>`;
  $end.innerHTML = `<option value="">working tree</option>`
    + `<option value="${INDEX}">staging area</option>`;
  $end.appendChild(new Option("HEAD (last commit)", HEAD));
  // A literal HEAD option, rather than the newest sha, so the shortcut still
  // works on a branch sitting on its own merge base with no commits listed.
  $start.appendChild(new Option("HEAD (last commit)", HEAD));
  $start.appendChild(new Option("HEAD~1 (before the last commit)", BEFORE_HEAD));
  $start.appendChild(new Option("@{upstream} (last pushed commit)", UPSTREAM));
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

let currentReview = {};
let shas = {};
let fileInfo = {};          // path -> {added, removed, binary, noisy, image, status, old_path}
let currentRange = {};      // {start, end, remerge} as the server resolved them
let seededNoise = false;    // auto-collapse noisy files once, not on every render

// Anything that throws mid-render (a missing vendored library, a diff2html
// parse error) would otherwise leave "loading diff…" up forever.
async function render() {
  try {
    await renderDiff();
  } catch (err) {
    console.error(err);
    status("loading diff failed");
  }
}

async function renderDiff() {
  syncUrl();
  status("loading diff…", true);
  const q = { base: $base.value };
  if ($start.value) q.start = $start.value;
  if ($end.value) q.end = $end.value;
  if ($structural?.checked) q.renderer = "structural";
  if ($trueDiff.checked) q.remerge = "false";
  if ($hideTests?.checked) q.tests = "false";

  const fetchStart = performance.now();
  const r = await fetch(api("diff", q));
  const data = await r.json();
  const fetchMs = performance.now() - fetchStart;
  if (!r.ok || data.error) { $diff.innerHTML = ""; $summary.innerHTML = `<span class="warn">${data.error}</span>`; status(""); return; }

  // The tickbox exists only while the range is one merge commit.
  $trueDiff.parentElement.hidden = !data.merge;

  currentReview = data.review || {};
  shas = data.shas || {};
  currentRange = { start: data.start, end: data.end, remerge: !!data.remerge };
  fileInfo = Object.fromEntries((data.files || []).map((f) => [f.path, f]));
  seedNoise(data.files || []);
  const files = data.files || [];
  const added = files.reduce((n, f) => n + (f.added || 0), 0);
  const removed = files.reduce((n, f) => n + (f.removed || 0), 0);
  const reviewed = Object.values(currentReview).filter((v) => v === "reviewed").length;
  const changed = Object.values(currentReview).filter((v) => v === "changed").length;
  $summary.innerHTML =
    `${files.length} files · <span class="add">+${added}</span> <span class="del">−${removed}</span>` +
    ` · ${reviewed}/${files.length} reviewed` +
    (changed ? ` · <span class="warn">${changed} changed since you reviewed</span>` : "") +
    testsNote(data) + mergeNote(data) + structuralNote(data);

  const renderStart = performance.now();
  if (data.lazy) {
    $diff.innerHTML = "";
    $summary.innerHTML += ` · <span class="warn">diff is large — loading per file</span>`;
    await renderLazy(files, q);
  } else {
    draw(data.diff);
  }
  status("");
  reportProfile(data.profile_id, fetchMs, performance.now() - renderStart);
}

// Say what "hide tests" took out, so the page never looks like the whole range.
function testsNote(data) {
  const t = data.hidden_tests;
  if (!t) return "";
  if (!t.files) return ` · <span class="merge-note">no test files in this range</span>`;
  return ` · <span class="merge-note">${t.files} test file${t.files === 1 ? "" : "s"} hidden` +
    ` (<span class="add">+${t.added}</span> <span class="del">−${t.removed}</span>)</span>`;
}

// Say which of the two readings of a merge commit is on screen, since the
// remerge one can legitimately be empty (a clean merge has no resolution).
function mergeNote(data) {
  if (!data.merge) return "";
  return data.remerge
    ? ` · <span class="merge-note">merge commit: showing only the conflict resolution` +
      ` (<code>--remerge-diff</code>); tick <i>true diff</i> for everything it brought in</span>`
    : ` · <span class="merge-note">merge commit: showing the full diff against its first parent</span>`;
}

// The structural diff is drawn by the same renderer, so say what is different
// about what it shows: lines difftastic considers unchanged are context.
function structuralNote(data) {
  if (data.renderer !== "structural") return "";
  return ` · <span class="merge-note">structural: only syntactic changes are marked;` +
    ` reformatting shows as context (difftastic)</span>`;
}

// The server can time its own work but not the two stages the user waits
// longest for, so the page posts them back and the profile is the whole journey.
function reportProfile(profileId, fetchMs, renderMs) {
  if (!profileId) return;
  const body = JSON.stringify({ profile_id: profileId, fetch_ms: fetchMs, render_ms: renderMs });
  // keepalive: the report must survive the user navigating straight back out.
  fetch("/api/profile", {
    method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true,
  }).catch(() => { /* profiling is never worth breaking the page over */ });
}

function draw(diffText, target) {
  const node = target || $diff;
  const ui = new Diff2HtmlUI(node, diffText,
    diffwebRenderOptions({ sideBySide: $sbs.checked, drawFileList: !target }));
  ui.draw();
  ui.highlightCode();
  if (!target) decorateFiles(node);
}

async function renderLazy(files, q) {
  for (const f of files) {
    const holder = document.createElement("div");
    $diff.appendChild(holder);
    const r = await fetch(api("diff", { ...q, files: f.path }));
    const d = await r.json();
    if (d.diff) draw(d.diff, holder);
  }
  decorateFiles($diff);
}

// diff2html gives us neither a collapse control nor a review affordance, so
// both get bolted onto each file header here.
function decorateFiles(root) {
  for (const wrapper of root.querySelectorAll(".d2h-file-wrapper")) {
    const nameEl = wrapper.querySelector(".d2h-file-name");
    if (!nameEl) continue;
    // diff2html spells a rename `dir/{old → new}` where git's numstat says
    // `dir/{old => new}`; the numstat spelling is the key everywhere else.
    const path = nameEl.textContent.trim().replace(" → ", " => ");
    wrapper.dataset.path = path;
    // Review state and blob shas come from `git diff --raw`, keyed on the plain new path.
    const reviewKey = renameTarget(path);
    const header = wrapper.querySelector(".d2h-file-header");

    const chevron = document.createElement("button");
    chevron.className = "chevron";
    chevron.type = "button";
    chevron.setAttribute("aria-label", "collapse file");
    header.prepend(chevron);

    // The whole header is the hit target, minus the controls sitting on it.
    header.addEventListener("click", (e) => {
      if (e.target.closest(".review-tick, a")) return;
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
        const tag = document.createElement("span");
        tag.className = "untracked-tag";
        tag.title = "not yet added to git; shown as a new file";
        tag.textContent = "untracked";
        header.appendChild(tag);
      }
      if (info.noisy) {
        const tag = document.createElement("span");
        tag.className = "noise-tag";
        tag.title = "matches noise.collapse_by_default; collapsed on first sight";
        tag.textContent = "generated";
        header.appendChild(tag);
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
        const badge = document.createElement("span");
        badge.className = "badge-changed";
        badge.textContent = "changed since you reviewed";
        header.appendChild(badge);
        collapsed.delete(path);  // reopen it: there is something new to look at
      } else if (box.checked) {
        collapsed.add(path);
      }

      box.addEventListener("change", () => {
        setCollapsed(wrapper, path, box.checked);
        // Keep the filter honest without waiting for the next render.
        wrapper.classList.toggle("is-reviewed", box.checked);
        applyHideReviewed();
        fetch(api("reviewed", { path: reviewKey, blob_sha: shaFor(reviewKey), reviewed: box.checked }),
              { method: "POST" });
      });
    }

    wrapper.classList.toggle("collapsed", collapsed.has(path));
    wrapper.classList.toggle("is-reviewed", currentReview[reviewKey] === "reviewed");
  }
  saveCollapsed();
  refreshToggleAll();
  window.diffwebKeys?.refresh();
  applyHideReviewed();
}

// --- images ---------------------------------------------------------------
//
// git can only say "Binary files differ" about an image, so the page fetches
// each side from /blob and shows them next to each other. The panes are added
// after the diff has painted, and each <img> is loading="lazy", so a branch
// full of screenshots costs nothing until you scroll to one.

function blobUrl(rev, path) {
  return api("blob", { rev, path });
}

function queueImagePane(wrapper, info) {
  requestAnimationFrame(() => addImagePane(wrapper, info));
}

function addImagePane(wrapper, info) {
  const body = wrapper.querySelector(".d2h-file-diff, .d2h-files-diff");
  if (!body || wrapper.querySelector(".image-diff")) return;
  const newPath = renameTarget(info.path);
  const sides = [];
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
function renameTarget(name) {
  const m = name.match(/^(.*)\{(.*) => (.*)\}(.*)$/);
  if (m) return `${m[1]}${m[3]}${m[4]}`;
  return name.includes(" => ") ? name.split(" => ")[1] : name;
}

// Late in a review most files are ticked off; hiding them leaves just the work.
function applyHideReviewed() {
  const on = $hideReviewed.checked;
  document.body.classList.toggle("hide-reviewed", on);
  const hidden = on ? $diff.querySelectorAll(".d2h-file-wrapper.is-reviewed").length : 0;
  $hideReviewed.parentElement.title = hidden ? `${hidden} reviewed files hidden` : "";
  window.diffwebKeys?.refresh();
}

// Light up whichever shortcut matches the range that is actually selected.
const $scopes = document.getElementById("scopes");

function syncScopes() {
  for (const btn of $scopes.querySelectorAll(".scope-btn")) {
    const want = SCOPES[btn.dataset.scope];
    const on = $start.value === want.start && $end.value === want.end;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

$scopes.addEventListener("click", (e) => {
  const btn = e.target.closest(".scope-btn");
  if (!btn) return;
  const want = SCOPES[btn.dataset.scope];
  $start.value = want.start;
  $end.value = want.end;
  syncScopes();
  render();
});

function shaFor(path) { return shas[path] || "worktree"; }

$base.addEventListener("change", async () => {
  const r = await fetch(api("base", { base: $base.value }), { method: "POST" });
  if (!r.ok) {
    const { error } = await r.json();
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

loadCommits().then((ok) => ok && render());
