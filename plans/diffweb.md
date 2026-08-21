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

## Verify

`uv sync && poe fetch-diffweb-assets && poe diffweb`, then `uv run pytest tests/test_diffweb.py`.
