# diffweb

A local web UI for reviewing your own in-progress work across git worktrees.

One page lists every worktree it can find; click one and you get the diff against
the merge base with its base branch, rendered with [diff2html], with a start/end
commit picker so you can narrow to a single commit or any range.

It is deliberately read-only and single-user: it binds `127.0.0.1`, shells out to
`git`, and never writes to your repos.

## Run it

```console
$ uv sync                     # installs everything into the project venv
$ poe fetch-diffweb-assets    # one-off: vendors diff2html + highlight.js
$ poe diffweb                 # http://127.0.0.1:8765
```

`poe diffweb-dev` is the same with auto-reload. On the hosted VM, forward port
8765 and open it in Chrome. There is also a `diffweb` fish function that starts
the server if it isn't already up and prints the URL.

Everything except the optional structural diff comes from `uv sync` - nothing is
installed globally and `install.sh` is untouched.

## Configuration

Optional, at `~/.config/diffweb.yaml` (override the location with `$DIFFWEB_CONFIG`).
Absent means defaults; malformed is a startup error rather than a silent fallback.

```yaml
roots: ["~/code", "~/code.*", "~/worktrees/*/*"]   # globs; every match containing .git is a worktree
server: {host: 127.0.0.1, port: 8765}
features:
  structural: false        # difftastic renderer toggle (see below)
  pr_links: true           # look up each branch's PR with `gh`
  reviewed_state: true     # per-file reviewed checkboxes
  live_reload: true        # SSE push when the worktree changes
limits:
  max_inline_diff_bytes: 1500000   # above this the page loads file-by-file
  max_inline_files: 300
noise:
  collapse_by_default: ["*.lock", "*.sum", "**/generated/**", "**/*_pb2.py",
                        "**/*.pb.go", "**/__snapshots__/**"]
tools:
  difft_path: null         # defaults to whatever `difft` is on PATH
  gh_path: null            # defaults to whatever `gh` is on PATH
  gh_timeout_seconds: 5.0
state_db: "~/.local/state/diffweb/state.db"
```

Discovery is the union of those globs and `git worktree list` run from each hit,
so a worktree registered outside your configured roots still shows up. Worktrees
whose basenames collide (several checkouts called `code`) fall back to their full
home-relative path as a label.

The base ref is auto-detected per repo - `origin/HEAD`, then `origin/master`,
`origin/main`, `master`, `main` - and can be overridden in the UI, where it is
remembered per worktree.

## Features

**Commit range.** `from` defaults to the merge base with the base ref, `to`
defaults to the working tree, so by default you see everything you have done on
this branch including uncommitted edits. Both ends are reflected in the URL.

**Reviewed state.** Tick a file to collapse it. The tick is stored against that
file's current blob sha, so when the file changes afterwards it reopens badged
"changed since you reviewed". State lives in SQLite, keyed by worktree.

**Structural diff** (`features.structural: true`). Renders the range with
[difftastic] instead of a line diff - far better for reorderings and refactors.
`difft` is a Rust binary, so `uv sync` cannot provide it:

```console
$ cargo binstall difftastic
```

Without it the toggle stays disabled and the endpoint returns that instruction
rather than an error. The structural pane is dark in both colour schemes because
difftastic's palette is tuned for a dark terminal.

**Branch context.** Each row shows how far ahead of its base the branch is, a red
`↓1` if it has fallen *behind* the base (your diff is against an old merge base),
an amber dot when there are uncommitted files, and a PR chip.

The chip is either `#4312 18w` - a link to the pull request, plus how long ago it
was opened - or `No PR (⟳ 2d)`, where the duration is how long ago we last managed
to ask. Clicking the refresh glyph asks again straight away. It is an inline SVG
rather than the 🔄 emoji, which renders as a tofu box on a machine whose font
config has no emoji coverage.

PRs come from `gh pr list --head <branch>`, stored in the state DB rather than in
memory so the age is honest across restarts, and refreshed in the background once
a stored answer is over two minutes old. Only the very first lookup blocks. gh
being missing, unauthenticated or offline is indistinguishable from "there is no
PR", which is exactly why the chip shows its age instead of claiming freshness.

**Changed lines are grouped.** Git emits a hunk as all its removals then all its
additions; diff2html's `matching` option pairs each removal with an addition and
renders `-,+,-,+` instead. Two related removals followed by their two
replacements read better, so `matching` is `none` - which costs nothing, as the
inline word-level highlighting survives it. The options live in
`static/render-options.js` so the tests bind to what actually ships.

**Keyboard.** `j`/`k` move between files (skipping any hidden by *hide reviewed*), `g`/`G` jump to first/last, `o` (or
Enter) collapses the focused file, `v` marks it reviewed, `c` collapses or
expands everything, `s` toggles side-by-side, and `?` shows the list. Shortcuts
stay out of the way while you are typing in the base-ref box or a commit select.

File headers stick below the toolbar as you scroll, so you always know which
file you are looking at - this reuses diff2html's own `stickyFileHeaders`,
overridden only to offset it past our toolbar.
**Noise control.** Lockfiles, generated code and snapshots are real changes that
are almost never worth reading. Anything matching `noise.collapse_by_default`
starts collapsed and wears a `generated` badge - but only the first time that file
is seen, so deliberately opening one sticks. Set the list to `[]` to turn it off.

Every file header shows its own `+`/`−` counts, which is what you want when the
whole diff is collapsed, and `hide reviewed` drops ticked files out of the page
entirely so that late in a review only the remaining work is on screen.

**Live reload.** The page opens an SSE stream; the server polls `HEAD` plus
`git status --porcelain=v2` every two seconds and pushes a change event, and the
page re-renders in place keeping your scroll position.

## Alternatives that were tried first

- **Forgejo** (`codeberg.org/forgejo/forgejo:9` under docker) - genuinely nice
  compare view with inline comments and review state, and it came up in about a
  minute. Two things ruled it out as the daily driver: you have to *push* a branch
  before you can look at it, and it therefore cannot show uncommitted work at all -
  which is most of what you want to look at mid-task. Worth revisiting if the goal
  ever shifts from "review my own WIP" to "review someone else's finished branch".
- **code-server + GitLens** - nothing was running on this VM to spike against, so
  this is untested rather than rejected. It would be per-repo regardless, not a
  cross-worktree catalog.
- **diff2html** itself is the renderer here, so its appearance is the baseline
  rather than a competitor.

## Layout

| file | what it does |
| --- | --- |
| `config.py` | pydantic models + YAML loading; every path the app touches comes from here |
| `gitio.py` | argv-only git wrappers, worktree discovery, ref validation |
| `state.py` | SQLite reviewed-state and per-worktree base-ref overrides |
| `app.py` | FastAPI routes |
| `static/favicon.svg` | the diff mark: a rounded square, red half and green half; also the browser icon |
| `static/render-options.js` | the diff2html options the page renders with, shared with the tests |
| `static/`, `templates/` | the UI; `static/vendor/` is gitignored, populated by `poe fetch-diffweb-assets` |
| `shot.mjs` | screenshots the running app (see below) |

# Working on diffweb

## Getting feedback

Four commands. The last one is not optional - see *Look at it* below.

```console
$ uv sync && poe fetch-diffweb-assets   # once per checkout
$ poe diffweb-test                      # ~2s, fully isolated
$ poe diffweb                           # serve on 127.0.0.1:8765
$ poe diffweb-shot                      # screenshot what is running, then read the PNGs
```

`poe diffweb-shot` drives a real browser over the catalog and the first diff
page in both colour schemes, writes PNGs to `/tmp/diffweb-shots`, prints every
console error, failed request and 4xx it saw, and exits non-zero if there were
any. Point it somewhere else with `--port`, `--out`, or explicit paths:

```console
$ poe diffweb-shot -- --port 8766 /profiles
```

It needs Playwright, which is dev-only tooling and not part of `uv sync`:

```console
$ npm --prefix diffweb install
$ npx playwright install --with-deps chromium   # needs sudo for the system libs
```

**Log whether the tooling helped.** When you are done, add an entry to
`TOOLING_ROADMAP.md` saying whether each tool you used earned its place and what
would have saved you time. The tools are only as good as the last person's
complaints, and the friction you just worked around is invisible to everyone
who did not hit it.

**Look at it.** Read the PNGs before you say you are done. Every visual bug in
this project's history got through a green test suite: a refresh control drawn
as a tofu box, changed lines rendered in the wrong order, a file that silently
never appeared in the diff. The screenshot is the only step that catches those.
Use the full `chromium` build, not `chrome-headless-shell` - `shot.mjs` already
does, because the shell renders some glyphs differently from a real browser.

## Running a second instance

Each feature tends to live on its own worktree, hosted on its own port, so point
`$DIFFWEB_CONFIG` at a throwaway config rather than editing your real one:

```console
$ cat > /tmp/mine.yaml <<'YAML'
roots: ["~/code.*"]
server: {host: 127.0.0.1, port: 8766}
features: {live_reload: false, pr_links: false}
state_db: "/tmp/mine.db"
YAML
$ DIFFWEB_CONFIG=/tmp/mine.yaml uv run python -m diffweb
```

Python changes need a restart (or use `poe diffweb-dev`); templates, CSS and JS
are picked up on reload.

## Tests

`tests/test_diffweb.py`. Two rules, both load-bearing:

- **Nothing may touch the developer's real repos or home.** The `world` fixture
  builds a temp git repo, a linked worktree and a bare origin under `tmp_path`;
  the `config` fixture writes a YAML pointing `roots`, `state_db` and any other
  path at `tmp_path` and sets `$DIFFWEB_CONFIG`. When you add a config option
  that writes anywhere, add it to that fixture - a profile log once leaked into
  `~/.local/state` precisely because it was missed.
- **The suite runs under `pytest -n 6`**, so no test may mutate shared state, and
  timing assertions must be tolerant (assert `\d+s`, not `0s`).

Some tests shell out to `node` with diff2html's *core* bundle, which unlike the
`-ui` bundle needs no DOM, to assert on real rendered HTML without a browser
(`tests/render_order.cjs`). They skip without `poe fetch-diffweb-assets`, so run
it or you will think you have more coverage than you do.

Call `app.reset_for_tests()` after rewriting the config mid-test; it drops the
cached config, state and diff cache.

## Adding a feature

The established shape, worth following:

1. A flag in `Features` (`config.py`), defaulting on unless it costs something -
   network, an external binary - in which case default off.
2. Server work in `gitio.py` (argv lists, never a shell string; every
   user-supplied ref through `resolve_ref`, which rejects anything option-like).
3. Route in `app.py`; the handler degrades rather than 500s.
4. UI in `templates/` + `static/`, reusing the CSS variables in `app.css` so it
   works in both themes.
5. Tests, then `poe diffweb-shot`.
6. Update this README, `ROADMAP.md`, and `../plans/diffweb.md` - the plan's
   *Decisions / gotchas* is where a surprise goes so the next person does not
   rediscover it - and `TOOLING_ROADMAP.md` if the tools helped or hindered.

## Traps this project has already hit

- **diff2html re-orders what git got right.** `matching: "lines"` pairs each
  deletion with an insertion, rendering `-,+,-,+` where git emitted `-,-,+,+`.
  Fixed in `render-options.js`; do not "restore" it for word-level highlighting,
  which survives `matching: "none"` anyway.
- **The dark theme is hand-written overrides against diff2html's internal class
  names** (`app.css`). They are coupled to the vendored version - re-check them
  when bumping it. Its selectors often carry two classes, so a one-class
  override of yours will silently lose.
- **`git diff` never reports untracked files**, and `git add -N` is not
  available to us: the app promises it never writes to your repos. Untracked
  files are rendered via `git diff --no-index` instead.
- **Don't shell out twice for the same thing.** Sizing the diff with a second,
  discarded `git diff` was half the cost of the most expensive request stage.
- **Claude transcript directory names cannot be reversed** - the slug maps both
  `/` and `.` to `-`. Match on the `cwd` recorded in each record.
- **The `State` sqlite connection is shared across threads.** The catalog looks
  PRs up in a pool of eight and refreshes them in background threads. sqlite3
  connections are not safe for concurrent use even with
  `check_same_thread=False`, so *every* `State` method takes the lock, reads
  included. Dropping the lock from a read shows up as a rare
  `InterfaceError: bad parameter or other API misuse`, not as an obvious bug.
- **Inline `onclick="event.stopPropagation()"` kills event delegation.** Rows
  navigate from their own handler, so controls inside a row need a
  capture-phase listener.
- **Don't rely on an emoji for a functional control**; a box with no glyph is a
  real outcome on a bare Linux box. Use an inline SVG.

## History and plans

`ROADMAP.md` holds product ideas, including several derisked-but-unbuilt ones.
`TOOLING_ROADMAP.md` holds the same for the development tooling, written by the
people who used it - read it before you rebuild something that already annoyed
somebody.
`../plans/diffweb.md` is the living plan: ticked steps with their verification,
and a decision log of the surprises. Read the gotchas there before starting.

[diff2html]: https://diff2html.xyz/
[difftastic]: https://difftastic.wilfred.me.uk/
