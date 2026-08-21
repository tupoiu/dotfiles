# diffweb roadmap

Scratchpad. Add anything, however half-formed - nothing here is committed to.

## Considered and deliberately left out of v1

- **Open-in-editor links** - click a hunk to jump to file+line in code-server or
  local VS Code via a URL handler. Cut from v1 by choice, not difficulty.
- **Inline comments** - notes pinned to a file+line, stored in the same SQLite DB.
  The thing Forgejo has that we do not.

## Ideas

- AI-generated change summary per worktree, shown on the catalog row.
- Stacked view: one section per commit rather than one flattened range.
- "What changed since I last looked" as a cross-worktree feed on the catalog page.
- Keyboard navigation: `j`/`k` between files, `v` to mark reviewed, `]`/`[` between worktrees.
- Word-level intra-line highlighting on the line renderer (diff2html does some of
  this already; difftastic does it properly).
- Ignore-patterns per repo, e.g. always collapse `Cargo.lock` and generated files.
- Non-git-worktree repos: the discovery code already handles plain clones, but
  nothing exercises that path yet.
- Serve over the coder port-forward with a stable URL rather than remembering 8765.
