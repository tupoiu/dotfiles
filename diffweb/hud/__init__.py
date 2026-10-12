"""The HUD: my pull requests across the repos in `hud.repos`, on one page.

Kept apart from the worktree diff viewer. It shares only the config, the state
DB and the chip CSS with the rest of diffweb, and it is mounted by `app.py`
through `routes.make_router`.
"""
