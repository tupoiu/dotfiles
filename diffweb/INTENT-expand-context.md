# Expand context

### Answers to Claude's questions

- **Q1 - Expand controls:** per-gap, GitHub-style, but **30 lines** per click
  ("GH's are always slightly tight").
- **Q2 - Which views:** the line diff only. Remerge diff goes on the ROADMAP.
- **Q3 - Endpoint:** a separate endpoint; `/blob` stays image-only. Changed by
  Q6 to serve the whole file, not a slice.
- **Q4 - Renderer:** our own parser and renderer. "Enumerate the tests they
  have, make our own version, and add those tests." diff2html is MIT.
- **Q5 - Class names:** `dw-*` with our own stylesheet.
- **Q6 - Highlighting:** highlight the whole file, fetched lazily as each file
  scrolls into view.
- **Q7 - Word diff:** keep jsdiff for now.
- **Q8-Q11** (test porting, `/text` scope, huge-file cap, expansions across
  live reload, small gaps): not answered; the user asked for the spec and the
  build, so Claude's recommendations stand as written above.

## Claude's spec

Written by Claude (a Claude Code session, 2026-10-14) from the answers above.
The decisions for Q8-Q11 are Claude's.

GitHub-style buttons in the diff that show the lines between hunks, so you can
read the code next to a change without leaving the page.

### What it does

- Each gap between two hunks has an expander row (the hunk header row). It shows
  **↓** (30 more lines below the hunk above), **↑** (30 more lines above the
  hunk below), and **↕ all** when more than 60 lines are hidden. A gap of 30
  lines or fewer has a single **↕** button that fills it.
- The gap before the first hunk has only ↑. The gap after the last hunk has
  only ↓, and appears when the file's text has loaded (the page must know
  where the file ends).
- A gap that is fully shown joins the two hunks. Its header row goes away.
- Expansions survive a redraw (the side-by-side toggle, live reload, the
  "hide tests" toggle). They are kept per file and dropped when the file's blob
  sha changes.
- Only on the line diff. The structural and remerge diffs draw as before, with
  no expanders (remerge is on the ROADMAP).

### How

diff2html goes. diffweb gets its own parser and renderer (`web/diff/`):

- `parse.ts` reads `git diff --no-color -M -C` output, the untracked
  `--no-index` patches and the difftastic patch, into files → hunks → lines.
- `render.ts` turns that into HTML strings, so `bun test` can check it with no
  browser. Line-by-line is one table. Side-by-side is also one table (old
  number, old code, new number, new code), so its rows cannot drift apart and an
  expanded line is one row.
- `highlight.ts` highlights each side's **whole file** once with highlight.js
  and slices the result into lines, so a multi-line string or comment is
  coloured right. Files over `limits.max_highlight_bytes` (1 MB) are drawn as
  plain text, and expansion still works.
- Word-level marks inside a changed line come from jsdiff, as before.
- Class names are `dw-*` with diffweb's own stylesheet. The vendored
  `diff2html.min.css` and `diff2html-ui.min.js` go. highlight.js and jsdiff are
  vendored as browser bundles instead (the build still needs nothing from
  `bun install`).

The page draws plain text first. When a file comes near the viewport, the page
fetches its old and new text from `GET /api/w/{id}/text?rev&path`, then draws
that file again with highlighting and the bottom expander. Expanded lines come
from the end side's text. Context lines are the same on both sides, so the old
line numbers follow from the offset of the gap.

`/text` serves any path git can read at `rev` (a commit, the index or the
worktree), through `gitio.blob_bytes`. It refuses binary content (a NUL byte),
and it is not limited to the files in the diff: the server binds localhost and
never writes.

If the fetched text does not agree with the diff (the file changed between the
two requests), that file gets no highlighting and no expanders until the next
redraw.

### Tests

- `tests/diffweb_parse.test.ts`: the diff2html parser cases that matter for git's
  output (ported inputs, MIT notice kept), plus gaps diff2html never covered:
  mode-only changes, empty files, submodules, untracked and remerge patches.
- `tests/diffweb_render.test.ts`: rows and line numbers, side-by-side pairing,
  word marks, the highlight slicing, and the expander arithmetic.
- The Python tests that render through bun (`tests/render_order.ts`) now use
  the new renderer. The new `/text` endpoint has Python tests too.
