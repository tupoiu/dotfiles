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
