"""
OAuth Provider Abstraction + GitHub Implementation

Provides a Protocol-based abstraction for OAuth providers and a concrete
GitHub implementation using httpx for token exchange and profile fetching.
"""

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

import httpx  # noqa: F401 (the module this provider talks through)

from ..utils.egress import github_async_client


@dataclass
class OAuthToken:
    """Token returned from OAuth code exchange."""

    access_token: str
    token_type: str = "bearer"
    scope: str = ""


@dataclass
class OAuthProfile:
    """Normalized user profile from an OAuth provider."""

    provider: str
    provider_id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    avatar_url: str | None = None
    orgs: list[str] = field(default_factory=list)


class OAuthProvider(Protocol):
    """Protocol for OAuth providers (GitHub, Google, etc.)."""

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        """Return the URL to redirect the user to for authorization."""
        ...

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange an authorization code for an access token."""
        ...

    async def get_user_profile(self, token: OAuthToken) -> OAuthProfile:
        """Fetch the user's profile from the provider API."""
        ...


class GitHubOAuthProvider:
    """GitHub OAuth implementation."""

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    API_BASE = "https://api.github.com"
    SCOPES = "read:user read:org"

    def __init__(self, client_id: str, client_secret: str, proxy: str | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        #: The operator's ``git_proxy`` (``egress.github_async_client``).
        self.proxy = proxy

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.SCOPES,
            "state": state,
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthToken:
        async with github_async_client(proxy=self.proxy) as client:
            resp = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            raise ValueError(f"GitHub token exchange failed: {data['error_description']}")

        return OAuthToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "bearer"),
            scope=data.get("scope", ""),
        )

    async def get_user_profile(self, token: OAuthToken) -> OAuthProfile:
        headers = {
            "Authorization": f"Bearer {token.access_token}",
            "Accept": "application/json",
        }

        async with github_async_client(proxy=self.proxy) as client:
            # Fetch user info
            user_resp = await client.get(f"{self.API_BASE}/user", headers=headers)
            user_resp.raise_for_status()
            user_data = user_resp.json()

            # Fetch orgs
            orgs_resp = await client.get(f"{self.API_BASE}/user/orgs", headers=headers)
            orgs_resp.raise_for_status()
            orgs_data = orgs_resp.json()

        orgs = [org["login"] for org in orgs_data]

        return OAuthProfile(
            provider="github",
            provider_id=str(user_data["id"]),
            username=user_data["login"],
            display_name=user_data.get("name"),
            email=user_data.get("email"),
            avatar_url=user_data.get("avatar_url"),
            orgs=orgs,
        )
