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
an amber dot when there are uncommitted files, and a chip linking to the branch's
pull request. PRs come from `gh pr list --head <branch>`, cached for two minutes.
Every failure mode - no `gh`, not logged in, offline, no PR - just means no chip.

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
| `static/`, `templates/` | the UI; `static/vendor/` is gitignored, populated by `poe fetch-diffweb-assets` |

Tests are in `tests/test_diffweb.py` and build a throwaway git repo, a linked
worktree and a bare origin under `tmp_path`. They never read your real repos and
pass on a machine with no `~/code`.

[diff2html]: https://diff2html.xyz/
[difftastic]: https://difftastic.wilfred.me.uk/
