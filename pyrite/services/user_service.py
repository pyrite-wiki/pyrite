"""
User Service — Identity management for collaboration.

Maps GitHub OAuth identity to local user records. Provides a sentinel
"local" user for non-authenticated setups so attribution is always available.
"""

import logging

from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)


class UserService:
    """Manages user identity for attribution and workspace membership."""

    def __init__(self, db: PyriteDB):
        self.db = db

    def get_or_create_from_github(
        self,
        github_login: str,
        github_id: int,
        display_name: str = "",
        avatar_url: str = "",
        email: str = "",
    ) -> dict:
        """Get or create a user from GitHub info. Returns user dict."""
        return self.db.upsert_user(
            github_login=github_login,
            github_id=github_id,
            display_name=display_name or github_login,
            avatar_url=avatar_url,
            email=email,
        )

    def get_local_user(self) -> dict:
        """Get the sentinel 'local' user for non-authenticated setups."""
        return self.db.get_local_user()

    def get_current_user(self, token: str | None = None, proxy: str | None = None) -> dict:
        """
        Get the current user — GitHub user if authenticated, else local sentinel.

        `token` is the caller's own GitHub token. Without one, the token
        ``pyrite auth github-login`` stored is used only by the local process:
        it is the operator's, and a network caller is never identified by it
        (ADR-0052 N2). `proxy` is the operator's ``git_proxy``.
        """
        try:
            from ..github_auth import get_github_token, get_github_user_info
            from .acting import is_local_process

            if not token and is_local_process():
                token = get_github_token()
            if token:
                info = get_github_user_info(token, proxy=proxy)
                if info:
                    return self.get_or_create_from_github(
                        github_login=info["login"],
                        github_id=info["id"],
                        display_name=info.get("name", info["login"]),
                        avatar_url=info.get("avatar_url", ""),
                        email=info.get("email", ""),
                    )
        except Exception as e:
            logger.debug("Could not get GitHub user: %s", e)

        return self.get_local_user()

    def resolve_git_author(self, name: str, email: str) -> str | None:
        """
        Try to match a git commit author to a known GitHub user.

        Checks email first (most reliable), then name heuristics.
        Returns github_login or None.
        """
        # Search across all users for matching email
        rows = self.db._raw_conn.execute(
            "SELECT github_login FROM user WHERE email = ? AND github_id != 0",
            (email,),
        ).fetchall()
        if rows:
            return rows[0][0]

        # Try matching by GitHub noreply email pattern: 12345+user@users.noreply.github.com
        if "noreply.github.com" in email:
            parts = email.split("@")[0]
            if "+" in parts:
                login = parts.split("+", 1)[1]
            else:
                login = parts
            existing = self.db.get_user(github_login=login)
            if existing:
                return login

        return None
