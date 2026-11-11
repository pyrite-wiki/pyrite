"""
GitHub OAuth Authentication

Handles OAuth flow for accessing private GitHub repositories.
Supports both OAuth App and GitHub App authentication methods.
"""

import logging
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from pyrite.utils.private_files import ensure_private_dir, tighten_file, write_private_text
from pyrite.utils.yaml import dump_yaml_text, load_yaml_file

try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from .config import GitHubAuth, trusted_config_dir
from .exceptions import outside_text

logger = logging.getLogger(__name__)

# GitHub OAuth endpoints
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"

# Default OAuth App for pyrite (users can configure their own)
DEFAULT_CLIENT_ID = os.environ.get("PYRITE_GITHUB_CLIENT_ID", "")
DEFAULT_CLIENT_SECRET = os.environ.get("PYRITE_GITHUB_CLIENT_SECRET", "")

# Callback server settings
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"


def get_auth_file_path() -> Path:
    """Get path to GitHub auth credentials file."""
    # Never a repo-local .pyrite/: a tree the user merely works in neither
    # supplies their GitHub credentials nor receives them.
    config_dir = trusted_config_dir()
    ensure_private_dir(config_dir)
    return config_dir / "github_auth.yaml"


def load_github_auth() -> GitHubAuth | None:
    """Load GitHub auth from secure file."""
    auth_file = get_auth_file_path()
    if not auth_file.exists():
        return None
    tighten_file(auth_file)
    try:
        data = load_yaml_file(auth_file)
        return GitHubAuth.from_dict(data)
    except Exception as e:
        print(f"Warning: Could not load GitHub auth: {e}")
        return None


def save_github_auth(auth: GitHubAuth) -> None:
    """Save GitHub auth to secure file."""
    auth_file = get_auth_file_path()

    # Only save necessary fields (secrets)
    data = {
        "client_id": auth.client_id,
        "client_secret": auth.client_secret,
        "access_token": auth.access_token,
        "refresh_token": auth.refresh_token,
        "token_expiry": auth.token_expiry,
        "scopes": auth.scopes,
    }

    # Add GitHub App fields if present
    if auth.app_id:
        data["app_id"] = auth.app_id
    if auth.private_key_path:
        data["private_key_path"] = str(auth.private_key_path)
    if auth.installation_id:
        data["installation_id"] = auth.installation_id

    # Written to a 0600 sibling created with that mode, then renamed: the
    # token is never in a file other users can read, not even briefly.
    write_private_text(auth_file, dump_yaml_text(data))


def clear_github_auth() -> None:
    """Remove GitHub auth credentials."""
    auth_file = get_auth_file_path()
    if auth_file.exists():
        auth_file.unlink()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    def log_message(self, format: str, *args) -> None:
        """Suppress default logging."""
        pass

    def do_GET(self) -> None:
        """Handle OAuth callback."""
        parsed = urlparse(self.path)

        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)

        if "error" in params:
            self.server.oauth_error = params.get("error_description", ["Unknown error"])[0]  # type: ignore
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body>
                <h1>Authentication Failed</h1>
                <p>You can close this window.</p>
                </body></html>
            """)
            return

        if "code" not in params:
            self.send_response(400)
            self.end_headers()
            return

        # Verify state
        if params.get("state", [""])[0] != self.server.oauth_state:  # type: ignore
            self.server.oauth_error = "State mismatch"  # type: ignore
            self.send_response(400)
            self.end_headers()
            return

        self.server.oauth_code = params["code"][0]  # type: ignore

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"""
            <html><body>
            <h1>Authentication Successful!</h1>
            <p>You can close this window and return to pyrite.</p>
            <script>window.close();</script>
            </body></html>
        """)


def start_oauth_flow(
    client_id: str | None = None, client_secret: str | None = None, scopes: list | None = None
) -> tuple[bool, str]:
    """
    Start OAuth flow to authenticate with GitHub.

    Returns (success, message).
    """
    if not HAS_HTTPX:
        return False, "httpx package required for OAuth. Install with: pip install httpx"

    client_id = client_id or DEFAULT_CLIENT_ID
    client_secret = client_secret or DEFAULT_CLIENT_SECRET

    if not client_id or not client_secret:
        return False, (
            "GitHub OAuth credentials not configured. "
            "Set PYRITE_GITHUB_CLIENT_ID and PYRITE_GITHUB_CLIENT_SECRET environment variables, "
            "or use 'pyrite auth github-setup' to configure."
        )

    scopes = scopes or ["repo", "read:user"]
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}",
        "scope": " ".join(scopes),
        "state": state,
    }
    auth_url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(auth_params)}"

    # Start callback server
    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), OAuthCallbackHandler)
    server.oauth_code = None  # type: ignore
    server.oauth_error = None  # type: ignore
    server.oauth_state = state  # type: ignore
    server.timeout = 120  # 2 minute timeout

    print("\nOpening browser for GitHub authentication...")
    print(f"If browser doesn't open, visit: {auth_url}\n")

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback
    start_time = time.time()
    while server.oauth_code is None and server.oauth_error is None:  # type: ignore
        server.handle_request()
        if time.time() - start_time > 120:
            return False, "Authentication timed out"

    server.server_close()

    if server.oauth_error:  # type: ignore
        return False, f"Authentication failed: {server.oauth_error}"  # type: ignore

    # Exchange code for token
    try:
        with httpx.Client() as client:
            response = client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": server.oauth_code,  # type: ignore
                    "redirect_uri": f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_data = response.json()
    except Exception as e:
        return False, f"Failed to exchange code for token: {outside_text(e)}"

    if "error" in token_data:
        return (
            False,
            f"Token exchange failed: {token_data.get('error_description', token_data['error'])}",
        )

    # Save auth
    auth = GitHubAuth(
        client_id=client_id,
        client_secret=client_secret,
        access_token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        scopes=token_data.get("scope", "").split(",") if token_data.get("scope") else scopes,
    )
    save_github_auth(auth)

    # Get user info
    try:
        with httpx.Client() as client:
            user_response = client.get(
                f"{GITHUB_API_URL}/user",
                headers={
                    "Authorization": f"Bearer {auth.access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if user_response.status_code == 200:
                user = user_response.json()
                return True, f"Authenticated as {user.get('login', 'unknown')}"
    except Exception:
        logger.warning("Failed to verify GitHub user identity", exc_info=True)

    return True, "Authentication successful"


def check_github_auth() -> tuple[bool, str]:
    """Check if GitHub auth is valid. Returns (valid, message)."""
    auth = load_github_auth()
    if not auth:
        return False, "Not authenticated. Run 'pyrite auth github-login' to authenticate."

    if not auth.access_token:
        return (
            False,
            "No access token found. Run 'pyrite auth github-login' to authenticate.",
        )

    if not HAS_HTTPX:
        return True, "Token present (httpx not installed, cannot verify)"

    try:
        with httpx.Client() as client:
            response = client.get(
                f"{GITHUB_API_URL}/user",
                headers={
                    "Authorization": f"Bearer {auth.access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if response.status_code == 200:
                user = response.json()
                return True, f"Authenticated as {user.get('login', 'unknown')}"
            elif response.status_code == 401:
                return (
                    False,
                    "Token expired or invalid. Run 'pyrite auth github-login' to re-authenticate.",
                )
            else:
                return False, f"GitHub API error: {response.status_code}"
    except Exception as e:
        return False, f"Could not verify token: {outside_text(e)}"


def get_github_token() -> str | None:
    """Get valid GitHub access token, or None if not authenticated."""
    auth = load_github_auth()
    if auth and auth.access_token:
        return auth.access_token
    return None


def get_github_user_info(token: str) -> dict | None:
    """
    Get GitHub user info from a token.

    Returns dict with: login, id, name, email, avatar_url
    Or None if the request fails.
    """
    if not HAS_HTTPX:
        return None

    try:
        with httpx.Client() as client:
            response = client.get(
                f"{GITHUB_API_URL}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if response.status_code == 200:
                return response.json()
    except Exception:
        logger.warning("Failed to get GitHub user info", exc_info=True)
    return None
