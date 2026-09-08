# Tooling roadmap

Feedback on the tools you use to work on diffweb - `poe diffweb-test`,
`poe diffweb`, `poe diffweb-shot`, `poe fetch-diffweb-assets`, the fixtures,
the node render harness.

**If you used them, add an entry before you hand work back.** Say plainly
whether each tool helped, and what would have helped more. A tool nobody
complains about is either perfect or unused, and the second is far more likely.
Negative entries are the valuable ones; "worked fine" is worth writing only when
the tool did something specific for you.

This is about the *tools*, not the app. Product ideas go in `ROADMAP.md`.

Format - one entry per session, newest first:

```markdown
## YYYY-MM-DD - what you were doing
- `tool` - helped / did not help. What happened, concretely.
- **Improve:** the change that would have saved you the time.
```

---

## 2026-08-28 - range shortcuts (working tree / whole branch / last commit / unpushed / staged)

- `poe diffweb-test` - helped. 2.7s for 109 tests, and it caught a `str`/`Path`
  slip in my own new test within seconds of writing it. The vendored assets were
  already present, so the silent-skip problem from the last entry did not bite.
- `poe diffweb-shot` - helped for "are there console errors", did not help for
  "does the new toolbar row look right". The whole-page PNG of a diffweb
  worktree is 1440x86205, which downscales to an unreadable ribbon: the toolbar
  I had just built was about two pixels of it. I wrote a throwaway Playwright
  script to shoot `.topbar` in both schemes and in each of the three new states,
  which is exactly the `--select` flag the previous session asked for.
  **Improve:** `--select <css>` is now requested twice. Add it. A `--click <css>`
  (or a repeatable `--step`) would have covered the rest, since what I needed to
  see was three *states* of one element, not three pages.
- `poe diffweb-shot -- --out DIR PATH` - actively misled me. `--out` was not
  consumed as a flag, so the directory was treated as a page path and shot as a
  404, and the run exited non-zero reporting "4 page errors" that were entirely
  my own arguments. The real pages in the same run were clean.
  **Improve:** reject an unknown or mis-parsed flag up front instead of
  requesting it as a URL, and never report a made-up path as a page error.
- Ad-hoc Playwright script - the `import { chromium } from 'playwright'` only
  resolves from inside `diffweb/`, so a script in a scratch directory dies with
  `ERR_MODULE_NOT_FOUND` and you copy it into the repo to run it.
  **Improve:** a documented `node --experimental-...`-free way to run a one-off,
  or just say in the README that scratch scripts belong in `diffweb/`.
- Running a second instance - the temp-YAML dance again, plus scraping
  `/w/<id>` out of the catalog HTML and then fetching six pages to find which
  opaque id was the branch I wanted.
  **Improve:** the `--port`/`--roots` flags already asked for, and let the page
  route accept a branch or worktree *name* as well as the hash.

## 2026-08-27 - writing the contributor docs, fixing a State race

- `poe diffweb-test` - helped. ~2s for 106 tests makes it cheap enough to run
  after every edit, which is how the flaky `State` test got noticed at all.
  **Improve:** four tests skip silently when `static/vendor/` is empty, so a
  fresh checkout reports 102 passing and looks complete. The task should either
  depend on `fetch-diffweb-assets` or print a loud line naming what it skipped.
- `poe diffweb-shot` - helped, but it is new and this was its first real use.
  It found the pages clean; the loop it formalises is what previously caught the
  tofu favicon and the alternating diff lines.
  **Improve:** it can only shoot whole pages. Investigating the `build.rs` hunk
  needed a hand-written script to screenshot one `.d2h-file-wrapper`. A
  `--select <css>` flag would have replaced that script, and a `--worktree
  <name>` flag would beat scraping the catalog HTML for an id.
- Playwright setup - the single biggest time sink this session. The VM was
  reprovisioned, the browser's system libraries went with it, and the failure
  surfaced as `libnspr4.so: cannot open shared object file` from deep inside a
  launch trace. Recovery needed `npx playwright install --with-deps chromium`
  and sudo.
  **Improve:** `shot.mjs` now prints that command on a launch failure, which is
  half a fix. A `poe diffweb-doctor` that checks node, playwright, the browser,
  its libraries and the vendored assets in one go, and prints exactly what to
  run, would turn a twenty-minute detour into one line.
- Running a second instance - the temp-YAML-and-`DIFFWEB_CONFIG` dance is
  copy-paste boilerplate, repeated once per feature worktree all session.
  **Improve:** let `python -m diffweb` take `--port` and `--roots` overrides so
  a throwaway instance is one command with no file.
- `poe fetch-diffweb-assets` - helped, and vendoring means the app runs offline.
  **Improve:** it re-downloads every time and verifies nothing. Skip when the
  files are already present at the pinned version, and check a hash - the whole
  UI is those bundles.
- Test fixtures (`world`, `config`) - helped. The temp git world made every
  gitio test honest, and pointing `$DIFFWEB_CONFIG` at `tmp_path` is what keeps
  the suite off real repos.
  **Improve:** isolation is per-path and manual, so each new config option that
  writes somewhere is a fresh chance to leak into `~`. A profile log already did
  exactly that. The fixture could instead force every path-shaped setting under
  `tmp_path`, and a session-scoped check could fail the run if anything appeared
  in the real state dir.
