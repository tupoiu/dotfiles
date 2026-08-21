# diffweb — plan

**Goal:** review my own in-progress work across all worktrees of the `~/code`
monorepo from one browser page, including uncommitted edits. Observable when the
catalog at `127.0.0.1:8765` lists every worktree with its churn, and clicking one
shows a diff2html diff against the merge base with a working start/end commit picker.

Living document: tick steps as they land, add gotchas as they bite.
See `diffweb/README.md` for how to run it and `diffweb/ROADMAP.md` for ideas.

## Steps

- [x] Spike the alternatives before building — Forgejo verdict recorded in `diffweb/README.md`
- [x] YAML → pydantic config, all paths derived from it (`diffweb/config.py`) — defaults, override and malformed-file tests pass
- [x] Worktree discovery: config globs ∪ `git worktree list` (`diffweb/gitio.py:95`) — finds all three real worktrees, and the temp world's linked worktree in tests
- [x] Base ref auto-detect + per-worktree UI override (`diffweb/gitio.py:136`) — resolves `origin/master`; changing it in the UI moves the merge base
- [x] Catalog table at `/` — sortable, one row per worktree, 40ms warm
- [x] Diff page: diff2html render, start/end commit picker, range in the URL — file counts match `git diff --stat` by hand
- [x] Lazy per-file loading over the size limits (`diffweb/app.py:168`) — `test_diff_endpoint_lazy_when_over_limit`
- [x] Structural diff toggle behind `features.structural` (`diffweb/gitio.py:225`) — renders difftastic output; returns install guidance, not an error, when `difft` is absent
- [x] Reviewed state in SQLite, keyed on blob sha — tick survives reload, file rebadges "changed" when the blob moves
- [x] Live reload over SSE (`diffweb/app.py:209`) — editing a tracked file updates the open page within ~2s, scroll position kept
- [x] Collapsible file diffs — header/chevron click, collapse-all, state persisted per worktree in `localStorage` (`diffweb/static/worktree.js:26`); survives re-render and reload
- [x] Isolated tests — 46 pass against a temp git world; suite passes with no `~/code` present
- [x] `uv sync` is the whole install; `install.sh` untouched
- [x] Branch context — PR chip via `gh`, behind-base count, dirty marker (`diffweb/forge.py`) — catalog shows a chip for the one branch that has a PR; every gh failure mode degrades to no chip
- [x] Keyboard navigation and sticky file headers (`diffweb/static/keys.js`) — j/k/o/v/c/s/? all driven in a browser; a header stays pinned below the toolbar while scrolling
- [x] Noise control — auto-collapse generated files, per-file churn, hide-reviewed (`diffweb/gitio.py:181`) — Cargo.lock and uv.lock start collapsed; reopening one sticks across reloads
- [x] Merge the three onto `diffweb-integrated` and serve it — 72 tests pass, all three features verified working together on :8765
- [x] PR chips carry an age — `#4312 18w` when a PR exists, `No PR (🔄 2d)` otherwise, with the 🔄 forcing a re-check — lookups persisted in SQLite so the age survives a restart; 94 tests pass and the refresh control was driven in a browser
- [x] Changed lines render as blocks, not alternating pairs (`diffweb/static/render-options.js`) — red test first, `-+-+` → `--++` on the real kasli `build.rs`, 98 tests pass
- [ ] Open-in-editor links — deliberately deferred, see ROADMAP
- [ ] code-server + GitLens comparison — never spiked, nothing was running on this VM

## Decisions / gotchas

- **Forgejo can't do the job this tool exists for.** Its compare view is better than
  ours and came up in a minute, but it only sees *pushed* branches, so uncommitted
  work — most of what you look at mid-task — is invisible. Revisit only if the goal
  shifts to reviewing finished branches.
- **`git worktree list` makes the config globs almost redundant.** Any single
  discovered repo reveals its siblings, so a bad glob degrades gracefully rather
  than hiding worktrees.
- **Two worktrees are both called `code`** (`~/code` and `~/worktrees/code/smart-isle/code`).
  Colliding basenames fall back to home-relative paths (`diffweb/gitio.py:126`).
- **`git diff --raw` reports all-zero post-image shas for uncommitted files**, so
  reviewed-state hashes the file on disk instead (`diffweb/gitio.py:207`).
- **diff2html ships a light-only stylesheet.** Dark mode is hand-written overrides
  against its internal class names (`diffweb/static/app.css:92`) — they are coupled
  to the vendored version, so re-check them when bumping it.
- **Side-by-side and line-by-line use different content classes** (`.d2h-files-diff`
  vs `.d2h-file-diff`). Collapsing must hide both; it silently worked in only one
  mode until a screenshot caught it.
- **diff2html's built-in `fileContentToggle` was dropped** (`diffweb/static/worktree.js:145`)
  — its "Viewed" checkbox sat next to ours and forgot itself on every re-render.
- **difftastic's palette assumes a dark terminal** and ansi2html bakes colours in as
  inline styles, so the structural pane stays dark in both themes.
- **A configured-but-missing `difft` path surfaced as a git failure**, not as "not
  installed", until an explicit `os.access` check was added.
- **Bad refs are user input, not server faults** — one exception handler maps
  `GitError`/`ValueError` to 400 with a message (`diffweb/app.py:29`); before it, a
  mistyped base ref produced a silent 500.
- **The repo is not an installed package**, so pytest needs `pythonpath = ["."]`.
- **This VM has docker, not podman**, despite `install.sh` installing podman. The 8
  pre-existing failures in `tests/test_container.py` and `tests/test_playwright_container.py`
  are that, not diffweb.

### From the three-worktree round

- **diff2html already had sticky file headers.** They looked broken only because
  it pins them at `top: 0`, underneath our own sticky toolbar, and its
  two-class selector outranked a one-class override. Check the vendored CSS for
  an existing mechanism before building one.
- **`gh pr list --head master` finds a PR on `~/code`.** A chip can therefore
  appear on a worktree you think of as "not a branch"; that is honest, not a bug.
- **The features collide in two places, both invisible until merged.** Keyboard
  nav walked files that `hide reviewed` had removed from the page, and the focus
  ring was cleared per-navigable-file so a hidden file kept it forever. Anything
  that filters the file list and anything that walks it have to agree on what
  "the files" means.
- **Naive conflict resolution silently spliced two test bodies together.** The
  merges were all "both branches appended at the same anchor", so keeping both
  sides worked for CSS and prose — but for Python it grafted one test's tail onto
  another and still parsed. The suite caught it; reading the diff would not have.
- **"No PR" and "gh is broken" are the same observation**, so the chip shows how
  long ago it last managed to ask rather than implying the answer is current.
  That pushed the PR cache out of memory and into the state DB.
- **An inline `onclick="event.stopPropagation()"` silently killed event
  delegation.** Rows navigate from their own click handler, so the refresh
  button needed a capture-phase listener; stopping propagation on the button
  itself meant the delegated handler never ran at all.
- **A control must not depend on the font having its glyph.** The 🔄 refresh
  emoji drew a tofu box: this machine has Noto Color Emoji installed, but
  `fc-match` on U+1F504 resolves to WenQuanYi Zen Hei, which has no coverage,
  and naming an emoji font stack in CSS did not rescue it. Replaced with an
  inline SVG. Only looking at a screenshot caught this - every test passed.
- **diff2html re-orders what git already got right.** Its `matching: "lines"`
  pairs each deletion with an insertion, so a hunk git emitted as `-,-,+,+`
  renders `-,+,-,+`. `matching: "none"` restores the blocks and, contrary to
  what the option name suggests, keeps the inline word-level highlighting.
- **The core diff2html bundle runs under node**, unlike the `-ui` one, which
  needs a DOM. That makes rendering assertions cheap: feed it a diff, count the
  row classes, no browser. Worth reaching for before writing a browser test.
- **The tool now finds its own worktrees**, because `~/worktrees/*/*` matches
  `~/worktrees/dotfiles/*`. Unplanned, and the best dogfooding available.

## Verify

`uv sync && poe fetch-diffweb-assets && poe diffweb`, then `uv run pytest tests/test_diffweb.py`.

**Before handing back, screenshot the page that is actually running and look at
it.** Not a fresh instance, not the tests - the server the user will open. Use
the full `chromium` build, not `chrome-headless-shell`, which renders some
glyphs differently from a real browser.

Feature branches live on `diffweb-pr-links`, `diffweb-review-ergo` and
`diffweb-noise-control`; `diffweb-integrated` is all three merged and is what
runs on :8765.
