"""GitHub queries for the HUD, through `gh api graphql`.

Two queries, so the page can paint before the slow part is known:

- `overview`: three aliased searches in one request - my open PRs, my PRs
  closed since a cutoff, and the open PRs that request my review.
- `details`: per-PR review state and recent activity, by node id.

Everything returned is plain JSON-able dicts, because it goes straight into the
state DB cache and out to the page.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from typing import Any

# The fields the first paint needs. `ready` is here and not in the details: the
# review list is ordered by it, and an order that jumps after the page has
# painted is worse than a slightly heavier first query.
_PR_FIELDS = """
fragment pr on PullRequest {
  id number title url state isDraft createdAt closedAt mergedAt updatedAt
  headRefName
  repository { nameWithOwner }
  author { login }
  ready: timelineItems(last: 1, itemTypes: [READY_FOR_REVIEW_EVENT]) {
    nodes { ... on ReadyForReviewEvent { createdAt } }
  }
}
"""

OVERVIEW_QUERY = (
    """
query($mine: String!, $closed: String!, $review: String!, $limit: Int!) {
  viewer { login }
  mine: search(query: $mine, type: ISSUE, first: $limit) { issueCount nodes { ...pr } }
  closed: search(query: $closed, type: ISSUE, first: $limit) { issueCount nodes { ...pr } }
  review: search(query: $review, type: ISSUE, first: $limit) { issueCount nodes { ...pr } }
}
"""
    + _PR_FIELDS
)

DETAILS_QUERY = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on PullRequest {
      id
      reviewDecision
      reviewRequests(first: 30) { nodes { requestedReviewer {
        __typename
        ... on User { login }
        ... on Team { slug organization { login } }
        ... on Bot { login }
        ... on Mannequin { login }
      } } }
      activity: timelineItems(last: 15, itemTypes: [PULL_REQUEST_COMMIT, ISSUE_COMMENT, PULL_REQUEST_REVIEW]) {
        nodes {
          __typename
          ... on PullRequestCommit { commit { committedDate author { user { login } } } }
          ... on IssueComment { createdAt author { __typename login } }
          ... on PullRequestReview { submittedAt state author { __typename login } }
        }
      }
    }
  }
}
"""

# `nodes(ids:)` takes at most 100 ids; smaller batches keep each request quick.
_DETAILS_BATCH = 50


class GitHubError(RuntimeError):
    """gh is missing, failed, timed out, or GitHub answered with errors."""


def run_graphql(query: str, variables: dict[str, Any], gh_path: str | None, timeout: float) -> dict[str, Any]:
    """Run one GraphQL request and return its `data`."""
    gh = gh_path or shutil.which("gh")
    if not gh:
        raise GitHubError("gh is not installed")
    body = json.dumps({"query": query, "variables": variables})
    try:
        proc = subprocess.run(
            [gh, "api", "graphql", "--input", "-"],
            input=body,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitHubError(f"gh timed out after {timeout:g}s") from None
    except OSError as exc:
        raise GitHubError(f"gh could not run: {exc}") from None
    try:
        reply = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        reply = {}
    # gh exits non-zero on a GraphQL error but still prints the reply, and a
    # partial `data` with errors is still an error for us.
    if proc.returncode != 0 or reply.get("errors") or "data" not in reply:
        messages = [e.get("message", "") for e in reply.get("errors") or []]
        detail = "; ".join(m for m in messages if m) or proc.stderr.strip() or "no data"
        raise GitHubError(f"gh api graphql failed: {detail}")
    return reply["data"]


def workday_cutoff(today: date, workdays: int) -> date:
    """The date `workdays` workdays (Mon-Fri) before today. No holidays."""
    day = today
    left = workdays
    while left > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def search_strings(repos: list[str], cutoff: date) -> dict[str, str]:
    """The three GitHub searches. `repo:` qualifiers are ORed by GitHub."""
    scope = " ".join(f"repo:{r}" for r in repos)
    return {
        "mine": f"is:pr is:open author:@me {scope} sort:updated-desc",
        "closed": f"is:pr is:closed author:@me closed:>={cutoff.isoformat()} {scope} sort:updated-desc",
        # Only direct requests: a team request (often from CODEOWNERS) would
        # list every PR the team owns.
        "review": f"is:pr is:open user-review-requested:@me {scope} sort:created-asc",
    }


def timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pr(node: dict[str, Any]) -> dict[str, Any]:
    ready = [n for n in (node.get("ready") or {}).get("nodes") or [] if n]
    created = timestamp(node.get("createdAt"))
    state = node["state"]
    draft = bool(node.get("isDraft"))
    return {
        "id": node["id"],
        "number": node["number"],
        "title": node["title"],
        "url": node["url"],
        "repo": node["repository"]["nameWithOwner"],
        "branch": node.get("headRefName") or "",
        "author": (node.get("author") or {}).get("login"),
        # The same status names the catalog chip uses, so the CSS is shared.
        "status": "draft" if draft and state == "OPEN" else state.lower(),
        "created_at": created,
        "updated_at": timestamp(node.get("updatedAt")),
        "closed_at": timestamp(node.get("mergedAt") or node.get("closedAt")),
        # A PR that was never a draft was ready the moment it was opened.
        "ready_at": timestamp(ready[-1].get("createdAt")) if ready else created,
    }


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    found = data.get(name) or {}
    # Search nodes that are not PRs (or that we cannot see) come back as {}.
    prs = [_pr(n) for n in found.get("nodes") or [] if n and n.get("id")]
    return {"total": found.get("issueCount", len(prs)), "prs": prs}


def overview(
    repos: list[str], cutoff: date, limit: int, gh_path: str | None, timeout: float
) -> dict[str, Any]:
    queries = search_strings(repos, cutoff)
    data = run_graphql(OVERVIEW_QUERY, {**queries, "limit": limit}, gh_path, timeout)
    return {
        "viewer": (data.get("viewer") or {}).get("login"),
        "cutoff": cutoff.isoformat(),
        "sections": {name: _section(data, name) for name in ("mine", "closed", "review")},
    }


def _reviewer(raw: dict[str, Any] | None) -> dict[str, str] | None:
    if not raw:
        return None
    if raw.get("__typename") == "Team":
        org = (raw.get("organization") or {}).get("login")
        return {"kind": "team", "name": f"{org}/{raw['slug']}" if org else raw["slug"]}
    if raw.get("login"):
        return {"kind": "user", "name": raw["login"]}
    return None


def _activity_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    kind = raw.get("__typename")
    if kind == "PullRequestCommit":
        commit = raw.get("commit") or {}
        user = ((commit.get("author") or {}).get("user")) or {}
        return {"kind": "commit", "actor": user.get("login"), "bot": False,
                "at": timestamp(commit.get("committedDate"))}
    if kind == "IssueComment":
        author = raw.get("author") or {}
        return {"kind": "comment", "actor": author.get("login"),
                "bot": author.get("__typename") == "Bot", "at": timestamp(raw.get("createdAt"))}
    if kind == "PullRequestReview":
        author = raw.get("author") or {}
        # A pending review is a draft only its author can see.
        if raw.get("state") == "PENDING":
            return None
        return {"kind": "review", "state": raw.get("state"), "actor": author.get("login"),
                "bot": author.get("__typename") == "Bot", "at": timestamp(raw.get("submittedAt"))}
    return None


def needs_reply(items: list[dict[str, Any]], viewer: str | None) -> bool:
    """True when someone else spoke last and it was not a sign-off.

    Bots do not count either way: a CI comment is not a question. Neither does
    someone else's commit or approval - those do not wait on an answer.
    """
    people = [i for i in items if not i["bot"] and i["at"] is not None]
    if not people or viewer is None:
        return False
    last = max(people, key=lambda i: i["at"])
    if last["actor"] == viewer:
        return False
    return last["kind"] == "comment" or (last["kind"] == "review" and last.get("state") != "APPROVED")


def _detail(node: dict[str, Any], viewer: str | None) -> dict[str, Any]:
    requests = (node.get("reviewRequests") or {}).get("nodes") or []
    pending = [r for r in (_reviewer(n.get("requestedReviewer")) for n in requests if n) if r]
    items = [i for i in (_activity_item(n) for n in (node.get("activity") or {}).get("nodes") or [] if n) if i]
    dated = [i for i in items if i["at"] is not None]
    last = max(dated, key=lambda i: i["at"]) if dated else None
    return {
        "decision": node.get("reviewDecision"),
        "pending": pending,
        "last_activity": last,
        "needs_reply": needs_reply(items, viewer),
    }


def details(ids: list[str], viewer: str | None, gh_path: str | None, timeout: float) -> dict[str, Any]:
    """Review state and last activity per PR node id."""
    out: dict[str, Any] = {}
    for start in range(0, len(ids), _DETAILS_BATCH):
        batch = ids[start : start + _DETAILS_BATCH]
        data = run_graphql(DETAILS_QUERY, {"ids": batch}, gh_path, timeout)
        for node in data.get("nodes") or []:
            if node and node.get("id"):
                out[node["id"]] = _detail(node, viewer)
    return out
