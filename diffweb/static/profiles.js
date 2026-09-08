// Timestamps are stored as unix time so they stay honest across timezones;
// the page turns them into "3m ago" at read time.
const UNITS = [[86400, "d"], [3600, "h"], [60, "m"], [1, "s"]];

for (const el of document.querySelectorAll(".ago")) {
  const seconds = Math.max(0, Date.now() / 1000 - Number(el.dataset.at));
  const [size, suffix] = UNITS.find(([s]) => seconds >= s) || UNITS[3];
  el.textContent = `${Math.floor(seconds / size)}${suffix} ago`;
  el.title = new Date(Number(el.dataset.at) * 1000).toLocaleString();
}
