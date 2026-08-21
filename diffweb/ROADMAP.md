# diffweb roadmap

Scratchpad. Add anything, however half-formed - nothing here is committed to.

## Considered and deliberately left out of v1

- **Open-in-editor links** - click a hunk to jump to file+line in code-server or
  local VS Code via a URL handler. Cut from v1 by choice, not difficulty.
- **Inline comments** - notes pinned to a file+line, stored in the same SQLite DB.
  The thing Forgejo has that we do not.

## Next up

- ~~Make the "chips" column in the homepage also show when the PR was first made if
  it exists, or if theres "No PR ({refresh emoji} 1m)" or "No PR ({refresh} 2d)".~~
  Done - the 🔄 is clickable and re-asks gh.
- Show which Claude agents have edited files on a worktree, on the homepage.
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

- AI-generated change summary per worktree, shown on the catalog row.
- Stacked view: one section per commit rather than one flattened range.
- "What changed since I last looked" as a cross-worktree feed on the catalog page.
- Keyboard navigation between *worktrees* (`]`/`[`); within a diff it already exists.
- Word-level intra-line highlighting on the line renderer (diff2html does some of
  this already; difftastic does it properly).
- Ignore-patterns per repo, e.g. always collapse `Cargo.lock` and generated files.
- Non-git-worktree repos: the discovery code already handles plain clones, but
  nothing exercises that path yet.
- Serve over the coder port-forward with a stable URL rather than remembering 8765.
