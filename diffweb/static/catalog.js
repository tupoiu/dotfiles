// Click a column header to sort; numeric columns sort numerically.
const table = document.getElementById("catalog");
const tbody = table.tBodies[0];
let sortKey = null, asc = true;
const NUMERIC = new Set(["ahead", "files", "churn", "reviewed"]);

table.tHead.addEventListener("click", (e) => {
  const key = e.target.closest("th")?.dataset.sort;
  if (!key) return;
  asc = key === sortKey ? !asc : true;
  sortKey = key;
  const rows = [...tbody.rows].filter((r) => r.dataset[key] !== undefined);
  rows.sort((a, b) => {
    const x = a.dataset[key], y = b.dataset[key];
    const cmp = NUMERIC.has(key) ? Number(x) - Number(y) : x.localeCompare(y);
    return asc ? cmp : -cmp;
  });
  rows.forEach((r) => tbody.appendChild(r));
});

// The refresh glyph on a "No PR" chip re-asks gh for that worktree.
// Capture phase: each row navigates from its own click handler, which would
// otherwise run first and take us off the page.
tbody.addEventListener("click", async (e) => {
  const button = e.target.closest(".pr-refresh");
  if (!button) return;
  e.stopPropagation();
  e.preventDefault();
  const cell = button.closest(".pr");
  button.classList.add("spinning");
  try {
    const r = await fetch(`/api/w/${cell.dataset.wt}/pr/refresh`, { method: "POST" });
    if (!r.ok) return;
    const { pr, checked_age } = await r.json();
    if (pr) {
      // A PR appeared since we last looked; swap the placeholder for the real chip.
      cell.innerHTML =
        `<a href="${pr.url}" target="_blank" rel="noopener" class="pr-chip pr-${pr.status}"` +
        ` title="${pr.title}" onclick="event.stopPropagation()">${pr.label}</a>` +
        (pr.opened_age ? `<span class="pr-age">${pr.opened_age}</span>` : "");
      cell.closest("tr").dataset.pr = String(pr.number);
    } else {
      cell.querySelector(".pr-checked").textContent = checked_age;
      button.title = `Checked ${checked_age} ago. Check again.`;
    }
  } finally {
    button.classList.remove("spinning");
  }
}, true);
