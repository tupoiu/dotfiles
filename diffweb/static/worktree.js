const el = document.querySelector(".controls");
const WT = el.dataset.wt;
const api = (p, q) => `/api/w/${WT}/${p}?${new URLSearchParams(q)}`;

const $base = document.getElementById("base");
const $start = document.getElementById("start");
const $end = document.getElementById("end");
const $sbs = document.getElementById("sbs");
const $structural = document.getElementById("structural");
const $status = document.getElementById("status");
const $summary = document.getElementById("summary");
const $diff = document.getElementById("diff");

const MERGE_BASE = "";        // empty start means "merge base with base ref"
const WORKTREE = "";          // empty end means "working tree"

$sbs.checked = localStorage.getItem("diffweb.sbs") === "1";
$sbs.addEventListener("change", () => {
  localStorage.setItem("diffweb.sbs", $sbs.checked ? "1" : "0");
  render();
});
$structural?.addEventListener("change", render);

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
  $end.innerHTML = `<option value="">working tree</option>`;
  for (const c of data.commits) {
    const label = `${c.short}  ${c.subject}  (${c.when})`;
    $start.appendChild(new Option(label, c.sha));
    $end.appendChild(new Option(label, c.sha));
  }
  $start.value = want.start || MERGE_BASE;
  $end.value = want.end || WORKTREE;
  el.dataset.start = el.dataset.end = "";
  status("");
  return true;
}

let currentReview = {};
let shas = {};

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
  const ui = new Diff2HtmlUI(node, diffText, {
    drawFileList: !target,
    matching: "lines",
    outputFormat: $sbs.checked ? "side-by-side" : "line-by-line",
    fileListToggle: true,
    fileContentToggle: false,
  });
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
        fetch(api("reviewed", { path, blob_sha: shaFor(path), reviewed: box.checked }), { method: "POST" });
      });
    }

    wrapper.classList.toggle("collapsed", collapsed.has(path));
  }
  saveCollapsed();
  refreshToggleAll();
}

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
$start.addEventListener("change", render);
$end.addEventListener("change", render);

if (el.dataset.live === "1") {
  const es = new EventSource(`/events/${WT}`);
  es.addEventListener("changed", () => {
    const y = window.scrollY;
    render().then(() => window.scrollTo(0, y));
  });
}

loadCommits().then((ok) => ok && render());
