# Local git diff web viewer (`diffweb`)

## Context

Peter works across several git worktrees of the `~/code` monorepo (`~/code`, `~/code.sqt-234-kasli-por-service`, `~/worktrees/code/*`), each roughly one branch/ticket. Today reviewing his own in-progress work means either terminal `git diff` or one-shot `diff2html-cli` HTML dumps — no single place to see "what have I changed on each branch".

Goal: a locally-run web app, opened in Chrome over the coder port-forward, that lists every worktree and for each shows the diff against the merge base with the base branch, in diff2html-quality HTML, with start/end commit selection. Diff2html is the known-good baseline for appearance.

Decisions already made with the user:
- Custom web app is non-negotiable; alternatives get a timeboxed spike (see Phase 0) and are abandoned if fiddly.
- Worktree discovery: configurable roots **and** `git worktree list`, unioned.
- Base ref: auto-detected, overridable in the UI.
- Feature-flagged extras to build: structural (difftastic) diff toggle, reviewed/viewed state, live reload. **Not** building open-in-editor links.
- Config is a YAML file deserialised into a pydantic model.
- Dashboard (`GET /`) is a dense list/table catalog, not cards.
- Dependencies stay project-scoped: `uv sync` in this repo must be enough to run it. Nothing new goes into `install.sh` for now.
- Use libraries where convenient rather than hand-rolling.
- Keep a `diffweb/ROADMAP.md` for Peter to dump thoughts into.

## Phase 0 — Timeboxed spike of the alternatives (~45 min, optional to keep)

Purpose is calibration, not delivery. Screenshot each with Playwright (`playwright-docker` image already exists; `poe build-playwright`), keep the images in `/tmp` scratch, and write a 5-line verdict into the diffweb README. Abandon any that fights back.

1. **Forgejo** via podman (`podman` + `podman-compose` already installed). Push `~/code` branches to it, open the compare view. Judge: is the PR-style compare + inline comments worth the push step?
2. **code-server + GitLens** — likely already reachable on this VM. Judge: commit-range diff quality vs diff2html.
3. **diff2html-cli** static output as the visual baseline to match.

Steal ideas (layout, file-tree sidebar, review affordances); do not adopt them as the product. Reusing their libraries where convenient is fine — the constraint is on the product shape, not on writing everything from scratch.

## Phase 1 — Core app

New directory `diffweb/` in this repo, Python 3.13 + FastAPI + uvicorn, added as a dependency group in `pyproject.toml` (the repo already standardises on `uv` + `poe`).

```
diffweb/
  README.md
  ROADMAP.md      # free-form idea dump, owned by Peter
  __init__.py
  app.py          # FastAPI routes
  gitio.py        # subprocess wrappers around git
  config.py       # pydantic models + YAML loading
  state.py        # SQLite reviewed-state (Phase 2)
  templates/      # Jinja2: index.html, worktree.html
  static/         # vendored diff2html UMD bundle + css, highlight.js, app.js, app.css
```

**Configuration** — `diffweb/config.py` defines pydantic `BaseModel`s (`Features`, `Server`, `Limits`, top-level `DiffwebConfig`) with full defaults, loaded by `load_config(path)` via `yaml.safe_load` + `DiffwebConfig.model_validate`, falling back to the defaults when the file is absent. Default path is `~/.config/diffweb.yaml`, overridable by `$DIFFWEB_CONFIG` (which is how tests inject a temp config). Validation errors abort startup with the pydantic message. Sketch:

```yaml
roots: ["~/code", "~/code.*", "~/worktrees/*/*"]
server: {host: 127.0.0.1, port: 8765}
features: {structural: false, reviewed_state: true, live_reload: true}
limits: {max_inline_diff_bytes: 1500000, max_inline_files: 300}
```

**Discovery** (`gitio.discover(config)`): the search paths come **only** from `config.roots` — no path is hardcoded anywhere in the code outside the pydantic field default, and `discover` takes the config as an argument rather than reading a module-level singleton, so tests can point it at a temp directory. Union of
- glob expansion of `config.roots` (default `["~/code", "~/code.*", "~/worktrees/*/*"]`, `~`-expanded at load time), keeping dirs that contain `.git` (file or dir — worktrees use a `.git` file);
- `git worktree list --porcelain` run from each discovered repo, to pick up worktrees outside the globs.
Dedupe by `realpath`. Label each by directory basename with the leading repo name stripped, plus the checked-out branch from `git rev-parse --abbrev-ref HEAD`.

**Base ref auto-detect** (`gitio.default_base()`): `git rev-parse --abbrev-ref origin/HEAD` → fall back to `origin/master` → `origin/main` → `master`. UI exposes it as an editable field per worktree; the value is remembered per worktree in the SQLite state.

**Diff computation**: `git merge-base <base> HEAD` for the default view; the range picker sends explicit `start`/`end`. Always produce unified diff text server-side via `git diff --no-color -M -C <start> <end>`, plus a "working tree" pseudo-ref at the end of the range that switches to `git diff <start>` (unstaged+staged included via `git diff HEAD` semantics).

**Safety**: bind `127.0.0.1` only. Every user-supplied ref is passed through `git rev-parse --verify <ref>^{commit}` before use and the resolved SHA is what reaches the diff command; paths are validated against the discovered worktree set. Use `--` separators and `subprocess.run` with a list argv, never a shell string.

**Routes**
- `GET /` — catalog: a dense sortable table, one row per worktree, columns `worktree · branch · base ref · commits ahead · files · +/− · reviewed n/m · last commit`. Rows link to the diff page. Deliberately list-shaped, not cards — the point is scanning many worktrees at a glance. Per-row stats come from a cheap `git diff --shortstat`, computed concurrently in a thread pool.
- `GET /w/{id}` — the diff page.
- `GET /api/w/{id}/commits?base=` — commit list for the range dropdowns (`git log --format` over `base..HEAD`).
- `GET /api/w/{id}/diff?start=&end=&renderer=` — returns raw unified diff (renderer=`line`) or pre-rendered structural HTML (renderer=`structural`, Phase 2).

**Rendering**: client-side `Diff2HtmlUI` from a vendored UMD bundle (a `poe fetch-diffweb-assets` task curls diff2html + highlight.js into `static/vendor/` so the app works with no CDN). This gives the file-list sidebar, side-by-side/unified toggle and syntax highlighting for free. Persist the side-by-side preference in `localStorage`.

**Range picker**: two `<select>`s — start defaults to "merge base with `<base>`", end defaults to "working tree". Both populated from the commit list; selecting a range re-fetches `/api/.../diff` and re-renders in place. Reflect the selection in the URL query string so a view is linkable/refreshable.

**Performance** (the monorepo is large): the diff endpoint takes a `files` param; when `--shortstat` says the diff exceeds a threshold (default ~1.5 MB or 300 files), the page first loads only `git diff --stat`-derived file list and fetches each file's diff lazily on expand. Cache resolved diffs keyed by `(worktree, start_sha, end_sha)` in an in-process LRU, skipping the cache whenever the range ends at the working tree.

## Phase 2 — Feature-flagged extras

All flags live under `features:` in the YAML config, read at startup, surfaced as toggles in the UI only when enabled.

1. **Structural diff toggle** — `difftastic`. Run `GIT_EXTERNAL_DIFF=difft git diff --ext-diff <start> <end>` with `DFT_COLOR=always DFT_DISPLAY=side-by-side`, convert ANSI to HTML with the `ansi2html` library (add to the dependency group; don't hand-roll an SGR parser), render in a `<pre class="difft">`. Toggle sits next to the unified/side-by-side control. `difft` is a Rust binary, so it can't come from `uv sync`: resolve it from `PATH`, or from `tools.difft_path` in the YAML config, and when it's absent show an inline message with the `cargo binstall difftastic` one-liner rather than failing. The flag defaults to `false` for that reason.
2. **Reviewed / viewed state** — SQLite at `~/.local/state/diffweb/state.db`, table `reviewed(worktree, path, blob_sha, seen_at)`. A checkbox on each file header marks it reviewed against its current blob sha; the file collapses. If the blob sha later changes, it re-opens and is badged "changed since you reviewed". Dashboard cards show `n/m files reviewed`. Same DB stores the per-worktree base-ref override.
3. **Live reload** — `GET /events/{id}` SSE stream. Server-side watcher thread per open worktree polling `HEAD` sha + max mtime under the worktree (respecting `.gitignore` via `git status --porcelain=v2` rather than a raw walk) every ~2s; on change, emit an event and the page re-fetches the current range. Prefer `watchexec` (already in `install.sh`) if present, poll otherwise. Preserve scroll position and reviewed-collapse state across a reload.

## Phase 3 — Plumbing

Everything is project-scoped: **`uv sync` in this repo is the only install step.** `install.sh` is not touched.

- `pyproject.toml`: put the deps in the existing `[dependency-groups]` so plain `uv sync` picks them up — `fastapi`, `uvicorn[standard]`, `jinja2`, `pydantic`, `pyyaml`, `ansi2html`. Poe tasks (all `executor = "uv"`, so they run in the project venv):
  - `diffweb` — `uvicorn diffweb.app:app` (host/port come from the YAML config; the task passes none)
  - `diffweb-dev` — same with `--reload`
  - `fetch-diffweb-assets` — curl diff2html + highlight.js into `diffweb/static/vendor/`; run once, output gitignored
- The only non-`uv` dependency is optional `difftastic`, handled as described above — documented in the README, not installed by anything.
- `fish/functions/diffweb.fish` — `cd` to the dotfiles repo, start the server via `poe diffweb` if not already running, print the URL; mirrors the style of `claude-local.fish` / `fe-local.fish`.
- `diffweb/README.md` — what it is, how to run, YAML config reference, feature-flag list, the optional-difftastic note, and the Phase 0 verdicts (the repo's CLAUDE.md expects READMEs to carry the answers).
- `diffweb/ROADMAP.md` — seeded with the ideas that didn't make this cut (open-in-editor links, inline comments, AI change summaries, stacked-commit view, cross-worktree "what changed today" feed) as an unordered list, explicitly a scratchpad for Peter to add to.

## Files touched

- New: `diffweb/**` (incl. `README.md`, `ROADMAP.md`), `fish/functions/diffweb.fish`, `tests/test_diffweb.py`
- Modified: `pyproject.toml`, `.gitignore`

## Testing

`tests/test_diffweb.py`, run by the existing `poe test-claude` / `pytest` setup, is **fully isolated** — it never reads `~/code` or any of Peter's real worktrees, and never depends on his branch state.

A `tmp_path`-scoped fixture builds a throwaway world from scratch:
1. `git init` a temp repo with `-c user.name/-c user.email` set locally, commit a couple of known files on `master`;
2. create a feature branch with a few further commits, plus one uncommitted working-tree edit;
3. `git worktree add` a second worktree, and a bare "origin" the repo is pushed to so `origin/HEAD` and merge-base logic have something real to resolve against;
4. write a temp `diffweb.yaml` whose `roots` point at that temp directory, and set `DIFFWEB_CONFIG` (via `monkeypatch.setenv`) to it.

Because roots are config-derived, everything under test — discovery, base detection, merge-base, diffs, reviewed-state — operates entirely inside `tmp_path`. The SQLite state DB path is likewise config-derived so it lands in `tmp_path` too. Tests must pass on a machine that has no `~/code` at all; the suite runs under `pytest -n 6` (repo default), so no test may mutate shared global state.

Coverage: `gitio` unit tests (discovery incl. non-existent/non-git roots, detached HEAD, base detection with and without `origin/HEAD`, ref validation rejecting `--upload-pack=`-style injection, merge-base, shortstat parsing); `config` round-trip (defaults, override, invalid YAML raises); `state` reviewed/changed-blob transitions; and FastAPI `TestClient` smoke tests hitting every route against the temp world.

## Manual verification

1. `uv sync && poe fetch-diffweb-assets && poe diffweb`, forward port 8765, open in Chrome. Confirm no global installs were needed.
2. Catalog table lists all three worktrees from `git worktree list` (`~/code`, `~/code.sqt-234-kasli-por-service`, `~/worktrees/code/smart-isle/code`) with correct branch names; the detached-HEAD one renders without erroring.
3. Open `code.sqt-234-kasli-por-service`: diff against merge-base with `origin/master` renders with syntax highlighting; file count matches `git diff --stat $(git merge-base origin/master HEAD)` run by hand.
4. Change the range to a single commit; diff matches `git show` for that commit. Change the base ref in the UI to a different branch; the merge base and diff update.
5. Toggle structural diff on a Rust file with a moved function; confirm difftastic output renders and colours survive.
6. Mark a file reviewed, reload — it stays collapsed. Edit that file on disk — it re-opens badged as changed.
7. With live reload on, `touch`/edit a tracked file and confirm the page updates within a few seconds without losing scroll position.
8. Point a config root at a non-existent path and at a non-git directory; the app starts and skips them rather than crashing. Write a malformed `~/.config/diffweb.yaml` and confirm startup fails with a readable pydantic validation error; delete it and confirm defaults work.
9. `pytest tests/test_diffweb.py` passes (see Testing above — isolated, no dependency on `~/code`).
10. Playwright screenshot of the dashboard and a diff page at 1440px and 1024px widths, eyeballed against the diff2html baseline from Phase 0.
