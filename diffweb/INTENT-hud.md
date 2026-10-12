# HUD

A page off the diffweb homepage that shows the state of my pull requests across
the repos I care about.

## What it shows

- All of my open PRs, grouped by repo. Each PR has a chip that links to its
  GitHub page, as in the rest of diffweb.
- All of my PRs that were closed or merged in the last 3 workdays.
- The last activity on each PR (for example, a commit or a comment).
- The people or teams whose review is still missing on each PR.
- All PRs on which I am tagged for review, with the age since they became
  "ready".

The less important details load lazily. The HUD code stays separate from the
worktree diff viewer (in its own subfolder).

### Answers to Claude's questions

- **Q1 - Data source:** use `gh api graphql` with GitHub search, one query per
  section (`is:pr author:@me is:open repo:a repo:b …`, `review-requested:@me`,
  `is:closed closed:>=DATE`). This is 3 round-trips, however many repos there
  are. The lazy fields come from a second, heavier GraphQL query.
- **Q2 - Repos:** an explicit `hud.repos: [owner/name, …]` list in
  `diffweb.yaml`. Do not infer repos from the worktree remotes.
- **Q3 - "Last 3 workdays":** count Mon-Fri only, with no holiday calendar.
  Calculate the cutoff date in Python and send it as `closed:>=YYYY-MM-DD`.
- **Q4 - Missing reviews:** a reviewer is missing when they are requested and
  have not submitted a review, or when they were re-requested after their last
  review. GitHub's `reviewRequests` already covers both cases, because a
  re-request puts the reviewer back in that list. Show each pending person or
  team as a chip, plus an "approved / changes requested" badge from
  `reviewDecision`.
- **Q5 - Age since ready:** use the latest `ReadyForReviewEvent`. If the PR was
  never a draft, use `createdAt`. In "tagged for review", put the drafts in a
  separate subsection below the ready PRs.
- **Q6 - Lazy loading and caching:**
  - First paint: repo, title, state (draft/open/merged) and the GitHub chip.
  - Second fetch: last activity, missing reviewers and age since ready.
  - Keep both in the state DB with the stale-while-revalidate pattern from
    `forge.py` ("checked 2m ago 🔄").
  - If the head branch of a PR has a local worktree, link the row to the
    diffweb page of that worktree.
- **Q7 - Code layout:**
  - A `diffweb/hud/` Python package with its own router, gh query module and
    templates.
  - `web/hud/` TS, bundled separately.
  - Shared with the rest of diffweb: only the config, the state DB and the chip
    CSS.
  - Gated behind `features.hud`. The default is on when `hud.repos` is not
    empty.
  - The HUD links into the worktree pages, and diffweb links to the HUD (see
    Q8).
- **Q8 - Link location:** a small shared header partial on every diffweb page
  (homepage, worktree diff, `/profiles`), with a "HUD" link when the feature is
  on.
- **Q9 - Last activity:** the latest commit push, comment, review or review
  comment, by anyone, shown as for example "💬 alice · 3h". On my own PRs, show
  a "↩ needs your reply" marker when the latest activity is not mine.
- **Q10 - Ordering:** in each repo, sort my PRs by last activity, newest first.
  "Tagged for review" is one flat list, oldest ready-age first, with the repo as
  a prefix.
- **Q11 - Refresh:** refresh on page load. While the tab is visible, also
  re-poll in the background every `REFRESH_AFTER_SECONDS` (2 min). The 🔄
  button forces a refresh immediately.
- **Q12 - This file:** the design lives here, and the ROADMAP item links to it.
