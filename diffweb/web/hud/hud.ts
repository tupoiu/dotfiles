// The HUD page. Everything below the header is drawn here from /api/hud, so
// the first paint, a poll and the refresh button all share one renderer.
// Text from GitHub (titles, logins) only ever goes in through textContent.

export {};

interface Worktree {
  id: string;
  name: string;
}

interface Pr {
  id: string;
  number: number;
  title: string;
  url: string;
  repo: string;
  branch: string;
  author: string | null;
  status: string;
  created_at: number | null;
  updated_at: number | null;
  closed_at: number | null;
  ready_at: number | null;
  worktree?: Worktree | null;
}

interface Section {
  total: number;
  prs: Pr[];
}

interface Overview {
  viewer: string | null;
  cutoff: string;
  sections: { mine: Section; closed: Section; review: Section };
}

interface Activity {
  kind: "commit" | "comment" | "review";
  actor: string | null;
  at: number | null;
  state?: string;
}

interface Detail {
  decision: string | null;
  pending: { kind: "user" | "team"; name: string }[];
  last_activity: Activity | null;
  needs_reply: boolean;
}

interface Loaded<T> {
  data: T | null;
  checked_at: number | null;
  stale: boolean;
  error: string | null;
}

// Matches forge.REFRESH_AFTER_SECONDS: poll no faster than the server
// considers an answer stale.
const POLL_SECONDS = 120;

const repoOrder: string[] = JSON.parse(document.getElementById("hud-repos")!.textContent || "[]");
const checkedEl = document.getElementById("hud-checked")!;
const errorEl = document.getElementById("hud-error")!;
const refreshButton = document.getElementById("hud-refresh")!;

let overview: Loaded<Overview> | null = null;
let details: Record<string, Detail> = {};
let lastPoll = 0;

function age(seconds: number): string {
  seconds = Math.max(0, seconds);
  const steps: [limit: number, divisor: number, suffix: string][] = [
    [60, 1, "s"],
    [3600, 60, "m"],
    [86400, 3600, "h"],
    [7 * 86400, 86400, "d"],
    [365 * 86400, 7 * 86400, "w"],
  ];
  for (const [limit, divisor, suffix] of steps) {
    if (seconds < limit) return `${Math.floor(seconds / divisor)}${suffix}`;
  }
  return `${Math.floor(seconds / (365 * 86400))}y`;
}

function since(at: number | null): string {
  return at === null ? "?" : age(Date.now() / 1000 - at);
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = "",
  text = "",
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

// The catalog's PR chip, then the title, then a link to the local worktree's
// diff when one is checked out on the PR's branch.
function prCell(pr: Pr, withRepo = false): HTMLTableCellElement {
  const cell = el("td", "hud-pr");
  if (withRepo) cell.append(el("span", "hud-repo", pr.repo.split("/")[1] ?? pr.repo));
  const chip = el("a", `pr-chip pr-${pr.status}`, `#${pr.number}`);
  chip.href = pr.url;
  chip.target = "_blank";
  chip.rel = "noopener";
  chip.title = `${pr.status} on GitHub`;
  cell.append(chip, el("span", "hud-title", pr.title));
  if (pr.worktree) {
    const wt = el("a", "hud-wt", pr.worktree.name);
    wt.href = `/w/${pr.worktree.id}`;
    wt.title = "Open the diff of the local worktree on this branch";
    cell.append(wt);
  }
  return cell;
}

const REVIEW_VERBS: Record<string, string> = {
  APPROVED: "approved",
  CHANGES_REQUESTED: "requested changes",
  COMMENTED: "reviewed",
  DISMISSED: "review dismissed",
};

function activityText(a: Activity, viewer: string | null): string {
  const who = a.actor === null ? "someone" : a.actor === viewer ? "you" : a.actor;
  const verb = a.kind === "commit" ? "committed" : a.kind === "comment" ? "commented"
    : REVIEW_VERBS[a.state ?? ""] ?? "reviewed";
  return `${who} ${verb} · ${since(a.at)}`;
}

// Cells filled from the details request. Until it lands they say so.
function lazyCell(kind: "activity" | "reviews", pr: Pr): HTMLTableCellElement {
  const cell = el("td", `hud-${kind} dim`, "…");
  cell.dataset.lazy = kind;
  cell.dataset.pr = pr.id;
  return cell;
}

function fillActivity(cell: HTMLElement, d: Detail, viewer: string | null, mine: boolean): void {
  cell.replaceChildren();
  if (!d.last_activity) {
    cell.textContent = "—";
    return;
  }
  cell.append(el("span", "", activityText(d.last_activity, viewer)));
  if (mine && d.needs_reply) cell.append(el("span", "hud-reply", "↩ needs your reply"));
}

const DECISIONS: Record<string, [label: string, css: string]> = {
  APPROVED: ["approved", "hud-approved"],
  CHANGES_REQUESTED: ["changes requested", "hud-changes"],
};

function fillReviews(cell: HTMLElement, d: Detail): void {
  cell.replaceChildren();
  const decision = d.decision ? DECISIONS[d.decision] : undefined;
  if (decision) cell.append(el("span", `hud-decision ${decision[1]}`, decision[0]));
  if (d.pending.length) {
    cell.append(el("span", "hud-waiting", "waiting on"));
    for (const r of d.pending) {
      const chip = el("span", `hud-reviewer hud-${r.kind}`, r.kind === "team" ? r.name : `@${r.name}`);
      chip.title = r.kind === "team" ? "team review requested" : "review requested";
      cell.append(chip);
    }
  }
  if (!cell.childNodes.length) cell.textContent = "—";
}

function applyDetails(): void {
  const viewer = overview?.data?.viewer ?? null;
  for (const cell of document.querySelectorAll<HTMLElement>("[data-lazy]")) {
    const d = details[cell.dataset.pr!];
    if (!d) continue;
    cell.classList.remove("dim");
    if (cell.dataset.lazy === "activity") fillActivity(cell, d, viewer, cell.closest("#section-mine") !== null);
    else fillReviews(cell, d);
  }
}

const NUMERIC_HEADERS = new Set(["ready for"]);

function table(headers: string[], rows: HTMLTableRowElement[]): HTMLTableElement {
  const t = el("table", "catalog hud-table");
  const head = t.createTHead().insertRow();
  for (const h of headers) head.append(el("th", NUMERIC_HEADERS.has(h) ? "num" : "", h));
  t.createTBody().append(...rows);
  return t;
}

function row(...cells: HTMLTableCellElement[]): HTMLTableRowElement {
  const tr = el("tr");
  tr.append(...cells);
  return tr;
}

// Configured order first, then anything GitHub returned that we did not list
// (a renamed repo, say), alphabetically.
function byRepo(prs: Pr[]): [string, Pr[]][] {
  const groups = new Map<string, Pr[]>();
  for (const pr of prs) groups.set(pr.repo, [...(groups.get(pr.repo) ?? []), pr]);
  const rank = (repo: string) => {
    const i = repoOrder.findIndex((r) => r.toLowerCase() === repo.toLowerCase());
    return i < 0 ? repoOrder.length : i;
  };
  return [...groups].sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

function grouped(target: HTMLElement, prs: Pr[], headers: string[], toRow: (pr: Pr) => HTMLTableRowElement): void {
  if (!prs.length) {
    target.replaceChildren(el("p", "empty", "None."));
    return;
  }
  const out: Node[] = [];
  for (const [repo, group] of byRepo(prs)) {
    out.push(el("h3", "hud-repo-name", repo), table(headers, group.map(toRow)));
  }
  target.replaceChildren(...out);
}

function flat(target: HTMLElement, prs: Pr[], headers: string[], toRow: (pr: Pr) => HTMLTableRowElement): void {
  target.replaceChildren(prs.length ? table(headers, prs.map(toRow)) : el("p", "empty", "None."));
}

function count(id: string, shown: number, total: number): void {
  document.getElementById(id)!.textContent = total > shown ? `${shown} of ${total}` : String(total);
}

const newestFirst = (key: "updated_at" | "closed_at") => (a: Pr, b: Pr) => (b[key] ?? 0) - (a[key] ?? 0);
const oldestFirst = (key: "ready_at" | "created_at") => (a: Pr, b: Pr) => (a[key] ?? 0) - (b[key] ?? 0);

function render(): void {
  const data = overview?.data;
  if (!data) return;
  const { mine, closed, review } = data.sections;

  grouped(
    document.getElementById("section-mine")!,
    [...mine.prs].sort(newestFirst("updated_at")),
    ["pr", "last activity", "reviews"],
    (pr) => row(prCell(pr), lazyCell("activity", pr), lazyCell("reviews", pr)),
  );
  count("count-mine", mine.prs.length, mine.total);

  document.getElementById("hud-cutoff")!.textContent = new Date(`${data.cutoff}T00:00`)
    .toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  grouped(
    document.getElementById("section-closed")!,
    [...closed.prs].sort(newestFirst("closed_at")),
    ["pr", "closed"],
    (pr) => row(prCell(pr), el("td", "dim", `${pr.status} ${since(pr.closed_at)} ago`)),
  );
  count("count-closed", closed.prs.length, closed.total);

  // Most overdue first: the PR that has waited longest for me is at the top.
  const reviewRow = (pr: Pr) =>
    row(
      prCell(pr, true),
      el("td", "dim", pr.author ?? "?"),
      el("td", "num", pr.status === "draft" ? "draft" : since(pr.ready_at)),
      lazyCell("activity", pr),
    );
  const headers = ["pr", "author", "ready for", "last activity"];
  const ready = review.prs.filter((p) => p.status !== "draft").sort(oldestFirst("ready_at"));
  const drafts = review.prs.filter((p) => p.status === "draft").sort(oldestFirst("created_at"));
  flat(document.getElementById("section-review")!, ready, headers, reviewRow);
  flat(document.getElementById("section-review-drafts")!, drafts, headers, reviewRow);
  count("count-review", review.prs.length, review.total);

  applyDetails();
}

function showStatus(): void {
  const parts: string[] = [];
  if (overview?.checked_at) parts.push(`checked ${since(overview.checked_at)} ago`);
  checkedEl.textContent = parts.join(" · ") || (overview ? "never checked" : "loading…");
  const error = overview?.error;
  errorEl.hidden = !error;
  if (error) {
    errorEl.textContent = overview?.data
      ? `Could not refresh: ${error}. Showing what GitHub said ${since(overview.checked_at)} ago.`
      : `Could not ask GitHub: ${error}`;
  }
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url);
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

async function loadDetails(mode: string): Promise<void> {
  const got = await getJson<Loaded<Record<string, Detail>>>(`/api/hud/details?mode=${mode}`);
  if (!got?.data) return;
  details = got.data;
  applyDetails();
  if (got.stale && mode === "cached") await loadDetails("fresh");
}

async function load(mode: "cached" | "fresh" | "force"): Promise<void> {
  lastPoll = Date.now();
  const got = await getJson<Loaded<Overview>>(`/api/hud?mode=${mode}`);
  if (got) {
    overview = got;
    render();
    showStatus();
  }
  if (!got?.data) return;
  // A stale first paint is followed straight away by a fresh one; the details
  // wait for it, so they are asked for the right set of PRs.
  if (got.stale && mode === "cached") return load("fresh");
  await loadDetails(mode);
}

refreshButton.addEventListener("click", async () => {
  refreshButton.classList.add("spinning");
  try {
    await load("force");
  } finally {
    refreshButton.classList.remove("spinning");
  }
});

// Poll only while the tab is visible, and catch up as soon as it is shown.
function maybePoll(): void {
  if (document.visibilityState === "visible" && Date.now() - lastPoll >= POLL_SECONDS * 1000) {
    void load("fresh");
  }
}
document.addEventListener("visibilitychange", maybePoll);
setInterval(() => {
  maybePoll();
  showStatus();
}, 15_000);

void load("cached");
