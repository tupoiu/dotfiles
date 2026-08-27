// Screenshot the running diffweb and report what the browser saw.
//
// Looking at the page is a required step before handing work back (see
// plans/README.md): tests do not show you a control rendering as a tofu box, a
// colour with no contrast, or a layout that wrapped.
//
//   node diffweb/shot.mjs                       # catalog + first diff page, both themes
//   node diffweb/shot.mjs --port 8766 --out /tmp/x
//   node diffweb/shot.mjs /profiles /w/abc123   # explicit paths
//
// Exits non-zero if the page logged an error, so it is usable as a check.

import { mkdirSync } from "node:fs";

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.error(
    "playwright is not installed. Once, from the repo root:\n" +
      "  npm --prefix diffweb install\n" +
      "  npx playwright install --with-deps chromium",
  );
  process.exit(2);
}

const argv = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i === -1 ? fallback : argv[i + 1];
};
const port = flag("port", "8765");
const out = flag("out", "/tmp/diffweb-shots");
const base = `http://127.0.0.1:${port}`;
let paths = argv.filter((a) => a.startsWith("/"));

mkdirSync(out, { recursive: true });

if (!paths.length) {
  // Default to the two pages every change tends to touch.
  const catalog = await (await fetch(base + "/")).text();
  const first = catalog.match(/\/w\/([a-f0-9]{12})/);
  paths = ["/", ...(first ? [first[0]] : [])];
}

// The bundled headless *shell* renders some glyphs as tofu boxes that a real
// browser draws correctly, so always ask for the full build.
const browser = await chromium.launch({ channel: "chromium" }).catch(async (err) => {
  console.error(`could not launch the full chromium build: ${err.message.split("\n")[0]}`);
  console.error("if it is a missing shared library: npx playwright install --with-deps chromium");
  process.exit(2);
});

let problems = 0;
for (const scheme of ["light", "dark"]) {
  const page = await (await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: scheme,
  })).newPage();

  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("response", (r) => r.status() >= 400 && errors.push(`${r.status()} ${r.url()}`));

  for (const path of paths) {
    await page.goto(base + path, { waitUntil: "networkidle" });
    // Diff pages render client-side; wait for the decoration pass to finish.
    await page
      .waitForFunction(() => document.querySelectorAll(".d2h-file-wrapper .chevron").length > 0, { timeout: 4000 })
      .catch(() => {});
    const name = (path === "/" ? "catalog" : path.replace(/[^\w]+/g, "-").replace(/^-|-$/g, ""));
    const file = `${out}/${name}-${scheme}.png`;
    await page.screenshot({ path: file, fullPage: true });
    console.log(`${file}  (${await page.title()})`);
  }

  for (const e of errors) console.error(`  [${scheme}] ${e}`);
  problems += errors.length;
  await page.close();
}

await browser.close();
console.log(problems ? `\n${problems} page error(s) - look at them` : "\nno page errors");
// Prompt at the point of use: nobody remembers the friction an hour later.
console.log("read the PNGs, then log how this tool did in diffweb/TOOLING_ROADMAP.md");
process.exit(problems ? 1 : 0);
