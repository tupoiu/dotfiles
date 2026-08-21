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
