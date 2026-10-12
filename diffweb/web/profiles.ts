// Timestamps are stored as unix time so they stay honest across timezones;
// the page turns them into "3m ago" at read time.
const UNITS: [seconds: number, suffix: string][] = [[86400, "d"], [3600, "h"], [60, "m"], [1, "s"]];

for (const el of document.querySelectorAll<HTMLElement>(".ago")) {
  const at = Number(el.dataset.at);
  const seconds = Math.max(0, Date.now() / 1000 - at);
  const [size, suffix] = UNITS.find(([s]) => seconds >= s) ?? UNITS[UNITS.length - 1]!;
  el.textContent = `${Math.floor(seconds / size)}${suffix} ago`;
  el.title = new Date(at * 1000).toLocaleString();
}
