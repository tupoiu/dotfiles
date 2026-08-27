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
