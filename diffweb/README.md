# diffweb

A local web UI for reviewing your own in-progress work across git worktrees.

One page lists every worktree it can find; click one and you get the diff against
the merge base with its base branch, with a start/end commit picker so you can
narrow to a single commit or any range.

It is deliberately read-only and single-user: it binds `127.0.0.1`, shells out to
`git`, and never writes to your repos.

## Install it

You do not need these dotfiles to use diffweb. With [uv] and [bun] on PATH:

```console
$ uv tool install "git+https://github.com/tupoiu/dotfiles#subdirectory=diffweb"
$ diffweb --root '~/src/*'    # serves http://127.0.0.1:8765
```

`bun` is needed only to build, because the page scripts are bundled from
TypeScript while the package is built. A wheel built elsewhere
(`uv build diffweb/`) installs with no bun: `uv tool install ./diffweb-*.whl`.
Upgrade with `uv tool upgrade diffweb` (add `--reinstall` if the version did not change).

You also need `git`. These are optional:
- `gh`, logged in (`gh auth login`), for the PR chips and the HUD.
- `difft`, for the structural diff.

Each `--root` is a glob for the folders that hold your checkouts. Repeat it for
more. Without `--root`, diffweb looks in `~/code`, `~/code.*` and
`~/worktrees/*/*`. To keep your settings, put them in `~/.config/diffweb.yaml`
(see [Configuration](#configuration)). `diffweb --print-config` prints
the config in effect, and is a good start for that file:

```console
$ diffweb --root '~/src/*' --print-config > ~/.config/diffweb.yaml
```

`--port` and `--config PATH` also override the file for one run. `diffweb --help`
lists every flag.

The server binds `127.0.0.1`, so only you can open the page. It reads your repos
and never writes to them. Its only network use is `gh`.

## Work on it (from the dotfiles)

```console
$ uv sync                     # installs the server into the project venv
$ poe fetch-diffweb-assets    # one-off: vendors highlight.js + jsdiff, bun-installs the dev tools
$ poe diffweb                 # builds the page scripts, then serves http://127.0.0.1:8765
```

The page scripts are TypeScript in `web/`, bundled by [bun] into the gitignored
`static/dist/`, so you need `bun` on PATH.
The build itself needs nothing from `bun install`.

`poe diffweb-dev` is the same with auto-reload, and rebuilds the scripts on save. On the hosted VM, forward port
8765 and open it in Chrome. There is also a `diffweb` fish function that starts
the server if it isn't already up and prints the URL.

Everything except `bun` and the optional structural diff comes from `uv sync` and
`bun install` - nothing else is installed globally and `install.sh` is untouched.

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
  profiling: true          # stage timings for the diff path, shown at /profiles
  hud: null                # the PR dashboard at /hud; null means "on when hud.repos is set"
limits:
  max_inline_diff_bytes: 1500000   # above this the page loads file-by-file
  max_inline_files: 300
noise:
  collapse_by_default: ["*.lock", "*.sum", "**/generated/**", "**/*_pb2.py",
                        "**/*.pb.go", "**/__snapshots__/**"]
tests:                     # what "hide tests" leaves out; git glob pathspecs
  paths: ["**/test_*.py", "**/*_test.*", "**/*.test.*", "**/*.spec.*",
          "**/conftest.py", "**/tests/**", "**/test/**", "**/__tests__/**",
          "**/testdata/**"]
emphasis:                  # files drawn with a coloured rail; the first matching rule wins
  - {label: proto, paths: ["*.proto"], color: purple, header: filled}
  - {label: dependencies, paths: [pyproject.toml, Cargo.toml], color: orange}
  - {label: spec, paths: ["*SPEC*", "**/specs/**/*.md"], color: teal, header: filled}
  - {label: intent, paths: ["*INTENT*"], color: pink, header: filled}
tools:
  difft_path: null         # defaults to whatever `difft` is on PATH
  gh_path: null            # defaults to whatever `gh` is on PATH
  gh_timeout_seconds: 5.0
hud:
  repos: []                # owner/name, e.g. [acme/widgets, acme/gears]
  closed_workdays: 3       # "closed or merged" and "reviewed" recently = within this many Mon-Fri days
  limit: 50                # most PRs per section
  timeout_seconds: 20.0
state_db: "~/.local/state/diffweb/state.db"
profile_log: "~/.local/state/diffweb/profiles.jsonl"
profile_keep: 500          # older profiles are dropped
```

Discovery is the union of those globs and `git worktree list` run from each hit,
so a worktree registered outside your configured roots still shows up. Worktrees
whose basenames collide (several checkouts called `code`) fall back to their full
home-relative path as a label.

The base ref is auto-detected per worktree. The main branch comes from
`origin/HEAD`, then `origin/master`, `origin/main`, `master`, `main`. A branch
stacked on another one bases on that branch instead: diffweb lists every branch
the worktree's history is built on top of (local or remote tips that HEAD
contains and the main branch does not), nearest first, and picks the nearest.
Nothing in git records a stacked branch's parent, so this is read from history,
and a branch made off this one that this one has since moved past looks the
same as a parent. The *base* chip lists them all with the main branch last,
each with how many commits the worktree has on top of it; *other ref…* takes any
ref. A choice made there is remembered per worktree.

## Features

**Commit range.** `from` defaults to the merge base with the base ref, `to`
defaults to the working tree, so by default you see everything you have done on
this branch including uncommitted edits. Both ends are reflected in the URL.

**Range shortcuts.** A row of five buttons between the branch name and the
base/from/to controls sets the whole range in one click: *working tree*
(`HEAD` → what is on disk, i.e. everything uncommitted), *whole branch* (merge
base → working tree, the default), *last commit* (`HEAD~1` → `HEAD`, the commit
you just made and nothing else), *unpushed* (`@{upstream}` → `HEAD`, the commits
the remote has not got; a branch with no upstream says so rather than failing on
an unknown ref), and *staged* (merge base → the index, which is
`git diff --cached`). Whichever one matches the current `from`/`to` lights
up, and picking a range by hand updates it. The index is a sentinel end of a
range like the working tree is, and is never cached.

**Merge commits.** When the range is exactly one merge commit (its first parent
→ itself, which is what *last commit* gives you on a fresh merge), the page
shows `git show --remerge-diff` by default: git redoes the merge, then diffs the
conflicted result against what was committed, so what you see is only the
conflict resolution the merger wrote by hand. A clean merge is therefore an
empty diff, and the summary says which reading is on screen. Tick *true diff* to
see the whole first-parent diff instead - everything the merge brought in. The
tick rides in the URL as `remerge=0` but is not remembered: the resolution is
the default every time. Merge commits are marked `[merge]` in the from/to
pickers. A range that merely *ends* at a merge is a range, and unaffected.

**Hide tests.** Tick *hide tests* (or press `t`) to see the non-test diff: only
the code, with every file matching `tests.paths` left out. The server does the
filtering, as git pathspecs, so the file count, the `+`/`−` totals, the
reviewed count and the lazy-loading limit are all for what is on screen. The
summary says what was taken out (`tests hidden +1578 −0`), or that the range
has none, so the page never looks like the whole range. With tests shown, the
same stat (`tests +1578 −0`) says how much of the range is tests. Hover it for
the breakdown by kind: Rust, Python, TypeScript and so on, and Rust tests
inside files. It works the same
on every range, untracked files and a merge's remerge diff included. The
patterns are git's glob pathspecs, not `fnmatch` like `noise`: `*` stops at a
`/`, and a leading `**/` matches at any depth. A file renamed *into* a test path
shows as a deletion. The tick rides in the URL as `tests=0` but, like *true
diff*, is not remembered. Set `tests.paths` to `[]` to remove the control.

Rust keeps its unit tests in the file they test, so *hide tests* also cuts each
`.rs` file at its test region: from the first `#[cfg(test)]` or `mod tests`
line to the end of the file, the same rule as the `loc` command. Added lines
are judged by the new file and removed ones by the old one. A file whose only
changes were tests goes, like a test file. The counts leave the cut lines out,
the tests stat includes them (as "Rust, inside files" in its breakdown), and
expanding the last gap of a file stops where its tests start. The cut is
skipped on a merge's remerge diff and on the structural diff.

**Emphasis.** Files that deserve a closer look stand out: each `emphasis`
rule gives the files it matches a rail down the left edge in its colour, a
chip with its label in the header, and a dot in the file list. Emphasised
files come first in the diff and the file list, each group in git's order.
`header:
tinted` (the default) washes the header with the colour; `header: filled`
fills it, for the files that matter most. By default protos, specs and INTENT docs are
filled, and `pyproject.toml` and `Cargo.toml` are tinted. The colours are
orange, purple, teal and pink, each with a light and a dark shade; red, green
and blue already mean removed, added and focused. `paths` are git-style
globs, matched case-sensitively: `*` stays inside one folder, `**/` is any
number of folders (none included, so `specs/**/*.md` matches `specs/a.md`),
and a pattern with no `/` is matched against the file name, so `*SPEC*`
catches `API_SPEC.md` but not a `SPECS` folder or `x.spec.ts`. A file takes
the first rule it matches. Setting `emphasis` replaces the defaults, and
`emphasis: []` turns it off.

**Images.** git can only say `Binary files differ` about a picture, so each
image file in the diff gets a pane showing it *before* and *after*, side by side
on a checkerboard: both sides for a change or rename, one side for an added or
deleted file, and for an SVG the text diff with the two renderings underneath.
Each picture is a link to itself at full size and shows its pixel dimensions
once loaded. The pictures come from `/api/w/<id>/blob?rev=<sha|INDEX|WORKTREE>&path=`,
which serves only `image/*` types, pins worktree reads inside the worktree, and
sandboxes SVGs so nothing in one can run. The `<img>`s are added after the diff
has painted and are `loading="lazy"`, so a branch full of screenshots costs
nothing until you scroll to one; a collapsed file's pictures never load at
all. On a merge commit's remerge view only the *after* side is shown, because the
*before* is the mechanical merge, which exists nowhere we can fetch it from.

**Untracked files.** A file git has not been told about is still part of your
work, so when the range ends at the working tree it is shown as an added file
with an `untracked` tag, counted in the summary and the catalog, and can be
ticked reviewed like any other. Ignored files and nested repos are left out.

**Reviewed state.** Tick a file to collapse it. The tick is stored against that
file's current blob sha, so when the file changes afterwards it reopens badged
"changed since you reviewed". State lives in SQLite, keyed by worktree.

**Structural diff** (`features.structural: true`). Renders the range from
[difftastic]'s verdict instead of git's - far better for reorderings and
refactors, and a pure reformat becomes *File without changes*. It is drawn by
the same renderer as the line view, so collapsing, review ticks,
side-by-side, both themes and the keyboard all work: the server runs `git diff
--ext-diff` with `difft --display json`, and `structural.py` splices that into
git's own patch as ordinary unified diff text. A line difftastic calls unchanged
(reindented, rewrapped) is context, shown in its *after* spelling; a line it
calls changed is a `-`/`+` pair with word-level highlighting. Files it has
no view on - new, deleted, binary - keep git's hunks, so renames, mode changes
and image panes are untouched. The `+`/`−` counts in headers and the summary
are still git's numstat, so a reformat-only file reads `+5 −4` over an empty
body. `difft` is a Rust binary, so `uv sync` cannot provide it:

```console
$ cargo binstall difftastic
```

Without it the toggle stays disabled and the endpoint returns that instruction
rather than an error.

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
additions, and the page keeps that order: two related removals followed by their
two replacements read better than `-,+,-,+`. Side-by-side puts the first removal
next to the first addition, and so on. Within such a pair, jsdiff marks the
words that changed; a pair with little in common gets no marks.

**Expand context.** Between hunks, the header row has buttons like GitHub's:
`↓` shows 30 more lines under the hunk above, `↑` 30 more over the hunk below,
and `↕` the whole gap (one button when 30 lines or fewer are hidden). Before the
first hunk there is only `↑`, after the last only `↓`. A gap opened all the way
joins its two hunks. Expansions survive a redraw (side-by-side, live reload) and
are dropped for a file whose blob changed. Only the line diff has them, not the
structural or the remerge diff. See `INTENT-expand-context.md`.

**Highlighting.** Each side of a file is highlighted whole with highlight.js
and cut into lines, so the middle of a multi-line string or comment is coloured
as one. The page draws plain text first and fetches a file's two sides from
`/api/w/{id}/text` when the file comes near the viewport. Above
`limits.max_highlight_bytes` (1 MB) the lines are highlighted one by one instead.

**Keyboard.** `j`/`k` move between files (skipping any hidden by *hide reviewed*), `g`/`G` jump to first/last, `o` (or
Enter) collapses the focused file, `v` marks it reviewed, `c` collapses or
expands everything, `s` toggles side-by-side, `t` hides or shows test files, and
`?` shows the list. Shortcuts
stay out of the way while you are typing in a commit select.

File headers stick below the toolbar as you scroll, so you always know which
file you are looking at. The offset follows the toolbar's height, which changes
when it wraps.
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

**Profiling** (`features.profiling`). `/profiles`, linked from the catalog
header, lists recent renders newest first with a stage bar: ref resolution,
`git diff`, numstat, blob shas, serialisation, network, and render.
The last two are measured by the page and posted back, because the wait the user
complains about is mostly not the server's - on a 9-file branch it is roughly
120ms of git and 95ms of rendering. Profiles land in `profile_log` as JSONL,
capped at `profile_keep`.

**HUD** (`/hud`, on when `hud.repos` is set). One page with my pull requests
across the configured repos, linked from the header of every page:

- *My open PRs*, drafts included, grouped by repo and sorted by GitHub's
  `updatedAt`, newest first.
- *Closed or merged since* the date `closed_workdays` workdays back (Mon-Fri, no
  holiday calendar), grouped by repo.
- *Waiting for my review*: open PRs that request my review directly, oldest
  ready-for-review first, with drafts in their own list below. Team requests
  (often from CODEOWNERS) are left out on purpose, or this would list every PR
  the team owns.
  Each row has *mute 1h* and *mute 1d*: a muted PR moves into a collapsed
  *Muted* list at the end of the section, which says when it comes back and has
  an *unmute*. Mutes are kept in the state DB, keyed by GitHub's PR id, and
  dropped once they run out.
- *Recently reviewed*: other people's PRs, open or closed, that I reviewed on or
  after the same date as *Closed or merged since*. Newest review first, with my
  latest review (`approved 3h ago`, `commented 1d ago`). A PR that waits for my
  review again is in *Waiting for my review* instead. GitHub search cannot
  filter on when I reviewed, so the search finds PRs updated since the date,
  and the server keeps the ones whose latest review by me is from then on.

Each PR has the catalog's chip, linking to GitHub. When a local worktree is
checked out on the PR's branch (matched on the `origin` remote and the branch
name), a second pill links to its diff page. Two columns load after the first
paint, because they cost a second, slower query (~2.5s for 20 PRs): *last
activity* (the latest commit, comment or review, by anyone) and *reviews* (the
review decision, plus every person or team still requested). A re-request puts
a reviewer back in GitHub's `reviewRequests`, so "requested and has not reviewed
since" is that list as it is. On my own PRs, `↩ needs your reply` marks a PR
where a person other than me spoke last and it was not an approval. Bots do not
count either way.

Last of all, a third query asks for CI on my open PRs that are not drafts. A
mark after the chip gives its state - ✓ passed, ✗ failed, ● running - and links
to the PR's checks. `✓ ready to merge` marks the ones that can go in: every needed review is in
(GitHub's review decision is *approved*, or, where the repo requires no
reviews, one approval and no request for changes), nobody is still requested,
the head commit's checks roll up to *success*, and GitHub says it merges
cleanly. GitHub works out mergeability only when asked, so a PR can be one poll
late to show it, but it is never shown on an *unknown*.

The data comes from `gh api graphql`: one request with four aliased searches,
then `nodes(ids:)` for the details and again for the checks. All three are stored in the state DB. The page
paints from what is stored, asks again at once if that is over two minutes old,
then polls every two minutes while the tab is visible. The refresh glyph asks
GitHub straight away. A failed refresh keeps the old rows and says how old they
are. The code is in `hud/`, apart from the diff viewer.

### From Claude: skeleton view

The *skeleton* tickbox (key `n`) swaps the diff for its table of contents:
every public name the range adds, grouped by file, with its signature and the
first paragraph of its docstring. A name with no docstring shows *No
docstring.* in amber with an amber rule, and each file says how many of its
names are documented, so the view doubles as a docs check; *only undocumented*
leaves just the gaps. Clicking a name shows the diff at its definition. The
skeleton is for the same range as the diff, and *hide tests* takes the test
files out of both. It rides in the URL as `skeleton=1`.

Public means a Python `def` or `class` with no leading `_`, at module level or
in a public class (a function nested in a function is a local), a TypeScript
`export function` or exported class, and a Rust `pub fn`, `pub struct`, `pub
enum` or `pub trait`. Python is read with `ast`; the docs of the other two are
the `//`, `/** */` or `///` comments right above, read with line rules. A Rust method shows the type its `impl` is for (`impl Trait for X` gives `X`). A name
is added when the start of the range does not define it in that file, so a
docstring changed on an existing name is not shown. The rules are in
`skeleton.py` and the endpoint is `/api/w/{id}/skeleton`, which takes the same
`base`, `start`, `end` and `tests` as `/diff`.

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
- **[diff2html]** drew the diff until diffweb needed to put rows *inside* its
  tables for the expanders. diffweb now has its own parser and renderer
  (`web/diff/`); diff2html's parser test inputs live on in
  `tests/diffweb_parse.test.ts`, with its MIT notice.

## Layout

| file | what it does |
| --- | --- |
| `config.py` | pydantic models + YAML loading; every path the app touches comes from here |
| `gitio.py` | argv-only git wrappers, worktree discovery, ref validation |
| `structural.py` | difftastic JSON + the two blobs -> unified diff hunks, spliced into git's patch |
| `skeleton.py` | the public names a range adds, with signatures and docstrings, for the skeleton view |
| `state.py` | SQLite reviewed-state and per-worktree base-ref overrides |
| `app.py` | FastAPI routes |
| `hud/` | the PR dashboard: `github.py` (GraphQL queries + parsing), `service.py` (state DB cache, worktree matching), `routes.py`, `templates/hud.html` |
| `web/hud/hud.ts` | the HUD page script: draws every row from `/api/hud` and `/api/hud/details` |
| `static/favicon.svg` | the diff mark: a rounded square, red half and green half; also the browser icon |
| `web/*.ts` | the page scripts: one entry per page (`catalog`, `worktree`, `profiles`), bundled by bun into `static/dist/` |
| `web/diff/parse.ts` | reads git's unified diff into files, hunks and lines |
| `web/diff/render.ts` | draws parsed files as HTML strings; the gaps and expanders |
| `web/diff/highlight.ts` | highlight.js over whole files, cut into lines |
| `web/keys.ts` | keyboard navigation, imported by `worktree.ts` |
| `web/skeleton.ts` | draws the skeleton view from `/api/w/{id}/skeleton` |
| `static/`, `templates/` | the UI; `static/vendor/` and `static/dist/` are gitignored, populated by `poe fetch-diffweb-assets` and `poe diffweb-build` |
| `package.json`, `tsconfig.json` | the bun build, plus dev-only tooling: `tsc`, playwright |
| `shot.ts` | screenshots the running app (see below) |

# Working on diffweb

## Getting feedback

Four commands. The last one is not optional - see *Look at it* below.

```console
$ uv sync && poe fetch-diffweb-assets   # once per checkout
$ poe diffweb-test                      # build + tsc + pytest, ~10s, fully isolated
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

It needs Playwright, which is dev-only tooling and not part of `uv sync`. The
package comes with `poe fetch-diffweb-assets` (`bun install`); the browser is
a separate download:

```console
$ bunx --cwd diffweb playwright install chromium
$ sudo apt-get install -y libnss3 libnspr4        # or: playwright install-deps chromium
```

`ldd ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome | grep 'not found'`
lists any system libraries the browser is still missing.

**Log whether the tooling helped.** When you are done, add an entry to
`TOOLING_ROADMAP.md` saying whether each tool you used earned its place and what
would have saved you time. The tools are only as good as the last person's
complaints, and the friction you just worked around is invisible to everyone
who did not hit it.

**Look at it.** Read the PNGs before you say you are done. Every visual bug in
this project's history got through a green test suite: a refresh control drawn
as a tofu box, changed lines rendered in the wrong order, a file that silently
never appeared in the diff. The screenshot is the only step that catches those.
Use the full `chromium` build, not `chrome-headless-shell` - `shot.ts` already
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

Python changes need a restart (or use `poe diffweb-dev`); templates and CSS are
picked up on reload. TypeScript changes need `poe diffweb-build`, or are rebuilt
on save under `poe diffweb-dev`.

## Tests

`tests/test_diffweb.py`, and `tests/test_diffweb_hud.py` for the HUD, whose
fake `gh` answers both GraphQL queries from canned JSON. Two rules, both load-bearing:

- **Nothing may touch the developer's real repos or home.** The `world` fixture
  builds a temp git repo, a linked worktree and a bare origin under `tmp_path`;
  the `config` fixture writes a YAML pointing `roots`, `state_db` and any other
  path at `tmp_path` and sets `$DIFFWEB_CONFIG`. When you add a config option
  that writes anywhere, add it to that fixture - a profile log once leaked into
  `~/.local/state` precisely because it was missed.
- **The suite runs under `pytest -n 6`**, so no test may mutate shared state, and
  timing assertions must be tolerant (assert `\d+s`, not `0s`).

The parser and renderer make HTML strings, so `bun test`
(`tests/diffweb_parse.test.ts`, `tests/diffweb_render.test.ts`, part of
`poe diffweb-test`) checks them with no browser. Some Python tests also shell out
to `bun` (`tests/render_order.ts`) to assert on real rendered rows. Both use the
vendored jsdiff and highlight.js, and the Python ones skip without
`poe fetch-diffweb-assets`, so run it or you will think you have more coverage
than you do. Likewise the asset-stamping test skips without a
build, and the remerge tests skip on git older than 2.36.

What the page scripts do in a browser - the order of fetches, redraws, what
ends up on the page - is in `tests/test_diffweb_browser.py`. It runs the server
in-process and drives Chromium with Playwright, a dev dependency, so `uv sync`
installs it. The browser is a separate one-off download, and the tests skip
without it:

```console
$ uv run playwright install chromium
```

Hold or delay responses with `page.route` instead of sleeping, so a race is the
same on every run, and keep each test to a second or so.

`bun build` strips types without checking them, so `poe diffweb-typecheck`
(`tsc --noEmit`, part of `poe diffweb-test`) is the only thing that catches a
type error.

Call `app.reset_for_tests()` after rewriting the config mid-test; it drops the
cached config, state and diff cache.

## Adding a feature

The established shape, worth following:

1. A flag in `Features` (`config.py`), defaulting on unless it costs something -
   network, an external binary - in which case default off.
2. Server work in `gitio.py` (argv lists, never a shell string; every
   user-supplied ref through `resolve_ref`, which rejects anything option-like).
3. Route in `app.py`; the handler degrades rather than 500s.
4. UI in `templates/` + `web/` (+ `static/app.css`), reusing the CSS variables in
   `app.css` so it works in both themes. A new page gets its own entry point in
   the `build` script in `package.json`.
5. Tests, then `poe diffweb-shot`.
6. Update this README, `ROADMAP.md`, and `../plans/diffweb.md` - the plan's
   *Decisions / gotchas* is where a surprise goes so the next person does not
   rediscover it - and `TOOLING_ROADMAP.md` if the tools helped or hindered.

## Traps this project has already hit

- **diff2html re-ordered what git got right.** Its `matching: "lines"` paired
  each deletion with an insertion, rendering `-,+,-,+` where git emitted
  `-,-,+,+`. The own renderer keeps git's order; do not "restore" pairing in
  line-by-line for word-level highlighting, which works on the pairs anyway.
- **Read a hunk by its line counts, not by what a line looks like.** A content
  line can start with `---`, `+++` or `diff --git`. `parse.ts` stops a hunk when
  its counts run out.
- **The text for highlighting and expanding is a second fetch.** On the
  worktree the file can change between the diff and the text. The renderer
  checks every highlighted line against the diff's own text, and a file whose
  text does not match gets no expanders until the next redraw.
- **`git diff` never reports untracked files**, and `git add -N` is not
  available to us: the app promises it never writes to your repos. When the
  range ends at the working tree, `gitio.untracked_changes` lists them with
  `ls-files --others --exclude-standard` and diffs each against `/dev/null`
  with `git diff --no-index`, which exits 1 on a difference, so it cannot go
  through `run_git`. They are folded into numstat, raw changes and the patch
  text as additions with an `untracked` flag; the review sha is the blob hash
  computed in Python. For a long time the README claimed this and the code did
  not do it - a branch whose work was an entire new role showed as one file.
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
- **The browser will happily run last week's `worktree.js`.** `StaticFiles`
  sends no `Cache-Control`, so the browser picks its own freshness lifetime and
  may not revalidate at all: the new HTML arrives, the old JS runs, and a new
  control renders but does nothing. Every `/static` URL therefore goes through
  the `asset()` template global, which stamps it with the file's mtime.
- **The image pane is a sibling of `.dw-file-body`, not inside it.** Highlighting
  and expanding draw the body again, which would throw the pane away. The
  collapse rule hides the pane explicitly.
- **git names a rename three ways.** `--numstat` says `dir/{old => new}`,
  `--raw` gives `old\tnew`, and the parser gives the plain old and new paths.
  File stats and collapsing are keyed on the numstat spelling (the header
  shows it with `→`), review state and blob shas on the plain new path, which
  `rename_target()` gets from the numstat spelling.
- **difftastic's JSON is behind `DFT_UNSTABLE=yes`** and carries no line text,
  only 0-based line numbers, an `aligned_lines` layout of the whole file and
  the changed lines. Line text comes from `blob_bytes` for each side; a file
  with a trailing newline gets a phantom empty last line in difft's numbering,
  which `structural._lines` drops so it never becomes a `+`/`-` row.
- **Don't rely on an emoji for a functional control**; a box with no glyph is a
  real outcome on a bare Linux box. Use an inline SVG.

## History and plans

`ROADMAP.md` holds product ideas, including several derisked-but-unbuilt ones.
`TOOLING_ROADMAP.md` holds the same for the development tooling, written by the
people who used it - read it before you rebuild something that already annoyed
somebody.
`../plans/diffweb.md` is the living plan: ticked steps with their verification,
and a decision log of the surprises. Read the gotchas there before starting.

[bun]: https://bun.sh/
[difftastic]: https://difftastic.wilfred.me.uk/
[uv]: https://docs.astral.sh/uv/
[diff2html]: https://diff2html.xyz/
