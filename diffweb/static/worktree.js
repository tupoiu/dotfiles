const el = document.querySelector(".controls");
const WT = el.dataset.wt;
const api = (p, q) => `/api/w/${WT}/${p}?${new URLSearchParams(q)}`;

const $base = document.getElementById("base");
const $start = document.getElementById("start");
const $end = document.getElementById("end");
const $sbs = document.getElementById("sbs");
const $hideReviewed = document.getElementById("hide-reviewed");
const $structural = document.getElementById("structural");
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
    const label = `${c.short}  ${c.subject}  (${c.when})`;
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
let fileInfo = {};          // path -> {added, removed, binary, noisy}
let seededNoise = false;    // auto-collapse noisy files once, not on every render

async function render() {
  syncUrl();
  status("loading diff…", true);
  const q = { base: $base.value };
  if ($start.value) q.start = $start.value;
  if ($end.value) q.end = $end.value;
  if ($structural?.checked) q.renderer = "structural";

  const r = await fetch(api("diff", q));
  const data = await r.json();
  if (!r.ok || data.error) { $diff.innerHTML = ""; $summary.innerHTML = `<span class="warn">${data.error}</span>`; status(""); return; }

  currentReview = data.review || {};
  shas = data.shas || {};
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
    (changed ? ` · <span class="warn">${changed} changed since you reviewed</span>` : "");

  if (data.renderer === "structural") {
    $diff.innerHTML = `<pre class="difft">${data.html}</pre>`;
    status("");
    return;
  }
  if (data.lazy) {
    $diff.innerHTML = "";
    $summary.innerHTML += ` · <span class="warn">diff is large — loading per file</span>`;
    await renderLazy(files, q);
    status("");
    return;
  }
  draw(data.diff);
  status("");
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
    const path = nameEl.textContent.trim();
    wrapper.dataset.path = path;
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
      if (info.noisy) {
        const tag = document.createElement("span");
        tag.className = "noise-tag";
        tag.title = "matches noise.collapse_by_default; collapsed on first sight";
        tag.textContent = "generated";
        header.appendChild(tag);
      }
    }

    const stateForFile = currentReview[path];
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
        fetch(api("reviewed", { path, blob_sha: shaFor(path), reviewed: box.checked }), { method: "POST" });
      });
    }

    wrapper.classList.toggle("collapsed", collapsed.has(path));
    wrapper.classList.toggle("is-reviewed", currentReview[path] === "reviewed");
  }
  saveCollapsed();
  refreshToggleAll();
  window.diffwebKeys?.refresh();
  applyHideReviewed();
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
