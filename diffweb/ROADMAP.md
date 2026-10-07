# diffweb roadmap

Scratchpad. Add anything, however half-formed - nothing here is committed to.

## Considered and deliberately left out of v1

- **Open-in-editor links** - click a hunk to jump to file+line in code-server or
  local VS Code via a URL handler. Cut from v1 by choice, not difficulty.
- **Inline comments** - notes pinned to a file+line, stored in the same SQLite DB.

## Next up

- Refactor the frontend to use TS instead of JS
- ~~Make the "chips" column in the homepage also show when the PR was first made if
  it exists, or if theres "No PR ({refresh emoji} 1m)" or "No PR ({refresh} 2d)".~~
  Done - the 🔄 is clickable and re-asks gh.
- Low prio: Show which Claude agents have edited files on a worktree, on the homepage.
  Derisked by reading `~/.claude/projects/*/*.jsonl` (2026-08-21):
  - **Feasible.** Every record carries `cwd`, `gitBranch`, `sessionId` and a
    timestamp, and `Edit`/`Write`/`NotebookEdit` tool_use blocks carry
    `file_path`. Match on the recorded `cwd`, *not* on the project directory
    name - that slug maps both `/` and `.` to `-`, so
    `code.sqt-234-x` and `code/sqt-234/x` collide.
  - Agents are identifiable: `sessionId` per session, an `ai-title` record
    giving it a human name, and `message.model` (`claude-opus-5`,
    `claude-fable-5`) per turn. `isSidechain` exists for subagents but is False
    throughout this machine's history, so subagent attribution is untested.
  - **The accuracy risk, and it is large.** Only edits made through the edit
    tools are attributable. Across this machine there are ~107 `Edit`/`Write`
    calls against ~241 Bash commands that look like they wrote a file
    (`sed -i`, heredocs, `>` redirects). Any agent that edits via Bash - which
    is the norm under some harness settings - is invisible. So the feature can
    honestly say "these agents touched these files", never "this file was
    changed by an agent" or "nothing else touched it".
  - Cost and privacy: transcripts are megabytes of JSONL holding prompts and
    source, living outside the repo. Parse lazily, cache by file mtime, and
    surface only paths, session names and timestamps - never message content.

- ~~Profile the diff-to-HTML path: the wall-clock a user waits between opening a
  worktree and seeing the diff of a heavy branch, broken down by stage, with a
  small page that shows recent profiles. Profiles are local noise - gitignore
  them.~~ Done - see `diffweb/profiling.py` and `/profiles`.

#### Claude

- **`git diff` runs twice per request.** The first thing profiling turned up:
  `gitio.diff_size_bytes` shells out for the whole diff just to measure its
  length, then `cached_diff` computes the identical diff again - 22ms each on
  the kasli worktree, about half the `diff` stage thrown away. Decide the lazy
  threshold from the numstat totals we already have, or keep the text from the
  one call.
- Parallelise the three git calls behind a diff request (`refs`, `numstat`,
  `shas`, `diff`) - they are sequential and independent, and together they are
  roughly half the user's wait.

- Refactoring cleanup: `app.py` is doing route handling, caching and PR fan-out at
  once, `worktree.js` has grown to several hundred lines of globals, and the
  gitio/app boundary leaks (`app.py` reaches for `gitio._try_git`). Split the
  client into modules and give the server a service layer between routes and git.
- Extra linting: nothing enforces style beyond the whitespace pre-commit hooks.
  Add ruff (lint + format) for Python and a formatter/linter for the JS and CSS,
  wire them into `.pre-commit-config.yaml` and a `poe lint` task, then fix the
  backlog they surface in one pass.

## Ideas

- Low prio: Image compare modes beyond side-by-side: onion-skin, swipe and a pixel-diff
  heatmap, the way GitHub offers. The blob endpoint and the per-side pane are
  in place; this is client-side work on top of them.

- AI-generated change summary per worktree, shown on the catalog row.
- HUD: A new page off the homepage which shows:
  - all of my open PRs (whether they're draft/non-draft) by repo which should be declared in the diffweb config file. Next to the name of the PR there should be a chip like we have in diffweb that is a like to the github page for the PR.
  - all of my PRs closed/merged within the last 3 workdays
  - their last activity (e.g. commit/comment)
  - if they're missing review from any specific people or teams
  - All PRs I'm tagged for review on (with age since they became "ready")
  The extra less important details should be lazy loaded. The code for this part of the app should be quite separated from the worktree diff viewer part (separate subfolder).

#### Claude

- Low prio: Merge commits: the remerge/true-diff split only applies to a single merge
  commit. A range containing several merges could offer "hide clean merges" or
  a per-commit remerge view once the stacked view exists. `--remerge-diff`
  against a chosen *second* parent (what did the merge do to the side branch?)
  is `git show --remerge-diff` already; it just needs a picker.
- Remerge is slower than a plain diff on a big repo because git actually redoes
  the merge in memory. It is cached like any other commit-to-commit diff, but
  the first hit on a large merge may be worth a "redoing merge…" status.
- "What changed since I last looked" as a cross-worktree feed on the catalog page.
- Low prio: Keyboard navigation between *worktrees* (`]`/`[`); within a diff it already exists.
- Low prio: Word-level intra-line highlighting on the line renderer (diff2html does some of
  this already; difftastic does it properly).
- Ignore-patterns per repo, e.g. always collapse `Cargo.lock` and generated files.
- Low prio: Non-git-worktree repos: the discovery code already handles plain clones, but
  nothing exercises that path yet.
