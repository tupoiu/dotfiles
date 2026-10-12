// Click a column header to sort; numeric columns sort numerically.

interface Pr {
  number: number;
  url: string;
  title: string;
  label: string;
  status: string;
  opened_age: string | null;
}

interface PrRefresh {
  pr: Pr | null;
  checked_age: string;
}

const table = document.getElementById("catalog") as HTMLTableElement;
const tbody = table.tBodies[0]!;
let sortKey: string | null = null;
let asc = true;
const NUMERIC = new Set(["ahead", "files", "churn", "reviewed"]);

table.tHead!.addEventListener("click", (e) => {
  const key = (e.target as Element).closest("th")?.dataset.sort;
  if (!key) return;
  asc = key === sortKey ? !asc : true;
  sortKey = key;
  const rows = [...tbody.rows].filter((r) => r.dataset[key] !== undefined);
  rows.sort((a, b) => {
    const x = a.dataset[key]!, y = b.dataset[key]!;
    const cmp = NUMERIC.has(key) ? Number(x) - Number(y) : x.localeCompare(y);
    return asc ? cmp : -cmp;
  });
  for (const r of rows) tbody.appendChild(r);
});

// The same chip the template draws for a PR. Built from nodes, not markup,
// because the title is whatever the PR author typed.
function prChip(pr: Pr): Node[] {
  const link = document.createElement("a");
  link.href = pr.url;
  link.target = "_blank";
  link.rel = "noopener";
  link.className = `pr-chip pr-${pr.status}`;
  link.title = pr.title;
  link.textContent = pr.label;
  link.addEventListener("click", (e) => e.stopPropagation());
  if (!pr.opened_age) return [link];
  const age = document.createElement("span");
  age.className = "pr-age";
  age.title = `opened ${pr.opened_age} ago`;
  age.textContent = pr.opened_age;
  return [link, age];
}

// The refresh glyph on a "No PR" chip re-asks gh for that worktree.
// Capture phase: each row navigates from its own click handler, which would
// otherwise run first and take us off the page.
tbody.addEventListener("click", async (e) => {
  const button = (e.target as Element).closest<HTMLElement>(".pr-refresh");
  if (!button) return;
  e.stopPropagation();
  e.preventDefault();
  const cell = button.closest<HTMLElement>(".pr")!;
  button.classList.add("spinning");
  try {
    const r = await fetch(`/api/w/${cell.dataset.wt}/pr/refresh`, { method: "POST" });
    if (!r.ok) return;
    const { pr, checked_age } = (await r.json()) as PrRefresh;
    if (pr) {
      // A PR appeared since we last looked; swap the placeholder for the real chip.
      cell.replaceChildren(...prChip(pr));
      cell.closest("tr")!.dataset.pr = String(pr.number);
    } else {
      cell.querySelector(".pr-checked")!.textContent = checked_age;
      button.title = `Checked ${checked_age} ago. Check again.`;
    }
  } finally {
    button.classList.remove("spinning");
  }
}, true);
