"""
Multi-KB Configuration System

Manages configuration for multiple knowledge bases with different types (events, research).
Configuration is loaded from:
1. ~/.pyrite/config.yaml (global)
2. Environment variables (overrides)
3. Individual kb.yaml files in each KB root
"""

import errno
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from pyrite.exceptions import ConfigError, ConfigFileUnreadableError, ConfigSaveRefusedError
from pyrite.utils.yaml import dump_yaml_file, load_yaml_file

logger = logging.getLogger(__name__)

load_dotenv()


class KBType(StrEnum):
    """Knowledge Base type (legacy compat — prefer string kb_type)."""

    EVENTS = "events"
    RESEARCH = "research"
    GENERIC = "generic"


@dataclass
class KBConfig:
    """Configuration for a single knowledge base."""

    name: str
    path: Path
    kb_type: str = "generic"  # Free-form string: "events", "research", "generic", etc.
    description: str = ""
    read_only: bool = False
    remote: str | None = None  # Git remote URL
    shortname: str | None = None  # Short alias for cross-KB links (e.g. "dev", "ops")
    ephemeral: bool = False  # Marks KB as temporary
    ttl: int | None = None  # TTL in seconds for ephemeral KBs
    created_at_ts: float | None = None  # Creation timestamp for TTL calculation
    default_role: str | None = None  # None=use global role, "read"=public, "none"=private

    # Repository reference (for multi-KB repos)
    repo: str | None = None  # Name of parent repo (if KB is inside a repo)
    repo_subpath: str = ""  # Relative path within repo (e.g., "timeline/events")

    # Loaded from kb.yaml if present
    schema: dict[str, Any] | None = None
    types: dict[str, Any] | None = None
    policies: dict[str, Any] | None = None

    # Internal cache — not serialized
    _schema_cache: Any = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.path = Path(self.path).expanduser().resolve()
        # Accept enum or string
        if hasattr(self.kb_type, "value"):
            self.kb_type = self.kb_type.value
        self._schema_cache = None

    @property
    def kb_yaml_path(self) -> Path:
        """Path to kb.yaml config file."""
        return self.path / "kb.yaml"

    @property
    def kb_schema(self) -> "Any":
        """Lazily load and cache the KBSchema object for this KB."""
        if self._schema_cache is None:
            from .schema import KBSchema

            schema_path = self.path / "kb.yaml"
            if schema_path.exists():
                self._schema_cache = KBSchema.from_yaml(schema_path)
            else:
                self._schema_cache = KBSchema()
        return self._schema_cache

    def invalidate_schema_cache(self) -> None:
        """Clear cached KBSchema — call after kb.yaml changes."""
        self._schema_cache = None

    @property
    def local_db_path(self) -> Path:
        """Path to local SQLite index for this KB."""
        pyrite_dir = self.path / ".pyrite"
        pyrite_dir.mkdir(exist_ok=True)
        return pyrite_dir / "index.db"

    def load_kb_yaml(self) -> bool:
        """Load kb.yaml if it exists. Returns True if loaded."""
        if self.kb_yaml_path.exists():
            data = load_yaml_file(self.kb_yaml_path)
            self.schema = data.get("schema")
            self.types = data.get("types")
            self.policies = data.get("policies")
            return True
        return False

    def validate(self) -> list[str]:
        """Validate KB configuration. Returns list of errors."""
        errors = []
        if not self.path.exists():
            errors.append(f"KB path does not exist: {self.path}")
        elif not self.path.is_dir():
            errors.append(f"KB path is not a directory: {self.path}")
        return errors


@dataclass
class Repository:
    """
    Configuration for a git repository that may contain one or more KBs.

    Supports both local and remote repos, with optional GitHub OAuth for private repos.
    """

    name: str  # Unique identifier for this repo
    path: Path  # Local path where repo is/will be cloned
    remote: str | None = None  # Git remote URL (https or ssh)
    branch: str = "main"
    auto_sync: bool = True
    sync_interval: int = 3600  # seconds

    # Authentication
    auth_method: Literal["none", "ssh", "github_oauth", "token"] = "none"
    github_app_id: str | None = None  # For GitHub App auth

    # KBs defined within this repo (populated after discovery)
    kb_paths: list[str] = field(default_factory=list)  # Relative paths to KBs

    def __post_init__(self):
        self.path = Path(self.path).expanduser().resolve()

    @property
    def is_remote(self) -> bool:
        """Check if this repo has a remote."""
        return self.remote is not None

    @property
    def is_github(self) -> bool:
        """Check if this is a GitHub repo."""
        if not self.remote:
            return False
        return "github.com" in self.remote

    def validate(self) -> list[str]:
        """Validate repository configuration."""
        errors = []
        if not self.path.exists() and not self.remote:
            errors.append(f"Repository path does not exist and no remote specified: {self.path}")
        if self.auth_method == "github_oauth" and not self.is_github:
            errors.append("GitHub OAuth auth method requires a GitHub remote URL")
        return errors


@dataclass
class GitHubAuth:
    """
    GitHub OAuth configuration for accessing private repositories.

    Supports both OAuth App flow and GitHub App installation tokens.
    """

    # OAuth App credentials (for user authentication)
    client_id: str | None = None
    client_secret: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_expiry: str | None = None  # ISO datetime

    # GitHub App credentials (for app-based auth)
    app_id: str | None = None
    private_key_path: Path | None = None
    installation_id: str | None = None

    # Scopes requested
    scopes: list[str] = field(default_factory=lambda: ["repo", "read:user"])

    def __post_init__(self):
        if self.private_key_path:
            self.private_key_path = Path(self.private_key_path).expanduser().resolve()

    @property
    def has_oauth_credentials(self) -> bool:
        """Check if OAuth credentials are configured."""
        return bool(self.client_id and self.client_secret)

    @property
    def has_valid_token(self) -> bool:
        """Check if we have a valid access token."""
        if not self.access_token:
            return False
        if self.token_expiry:
            from datetime import datetime

            try:
                expiry = datetime.fromisoformat(self.token_expiry.replace("Z", "+00:00"))
                if datetime.now(expiry.tzinfo) >= expiry:
                    return False
            except Exception:
                logger.warning("Failed to parse token expiry: %s", self.token_expiry, exc_info=True)
        return True

    @property
    def has_app_credentials(self) -> bool:
        """Check if GitHub App credentials are configured."""
        return bool(self.app_id and self.private_key_path and self.installation_id)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (excluding secrets for display)."""
        return {
            "client_id": self.client_id,
            "has_access_token": bool(self.access_token),
            "token_expiry": self.token_expiry,
            "app_id": self.app_id,
            "has_private_key": bool(self.private_key_path and self.private_key_path.exists()),
            "installation_id": self.installation_id,
            "scopes": self.scopes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitHubAuth":
        """Create from dictionary."""
        return cls(
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_expiry=data.get("token_expiry"),
            app_id=data.get("app_id"),
            private_key_path=Path(data["private_key_path"])
            if data.get("private_key_path")
            else None,
            installation_id=data.get("installation_id"),
            scopes=data.get("scopes", ["repo", "read:user"]),
        )


@dataclass
class Subscription:
    """Configuration for a subscribed remote KB."""

    url: str
    local_path: Path
    auto_sync: bool = True
    sync_interval: int = 3600  # seconds
    repo: str | None = None  # Reference to parent Repository name

    def __post_init__(self):
        self.local_path = Path(self.local_path).expanduser().resolve()


@dataclass
class OAuthProviderConfig:
    """Configuration for a single OAuth provider (e.g. GitHub)."""

    client_id: str = ""
    client_secret: str = ""
    allowed_orgs: list[str] = field(default_factory=list)  # empty = allow all
    org_tier_map: dict[str, str] = field(default_factory=dict)  # org → role
    default_tier: str = "read"


@dataclass
class UsageTierConfig:
    """Resource limits for a usage tier (e.g. free, pro)."""

    max_personal_kbs: int = 1
    max_entries_per_kb: int = 500
    max_storage_mb: int = 50
    allow_private_repos: bool = False
    rate_limit_read: str = "100/minute"
    rate_limit_write: str = "30/minute"
    # llm-usage-tracking-and-quotas: per-kind daily LLM request limit.
    # None (the default) means unlimited -- self-hosted instances don't
    # get a quota unless the operator opts in by setting this.
    daily_llm_requests: int | None = None


ANONYMOUS_TIERS = ("read", "write")


def normalize_anonymous_tier(value: str | None, source: str = "auth.anonymous_tier") -> str | None:
    """The validated `anonymous_tier`: None, "read" or "write".

    "none" (any case) is accepted as None -- no anonymous access -- because
    shipped deploy configs set `PYRITE_AUTH_ANONYMOUS_TIER=none`. Anything
    else is refused: an unknown string used to be carried through as the
    visitor's role, and `admin` would have made every visitor an admin.
    """
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in ("", "none"):
        return None
    if normalized in ANONYMOUS_TIERS:
        return normalized
    raise ValueError(
        f"{source} must be one of 'read', 'write' or 'none' (no anonymous access), "
        f"got {value!r}. Anonymous visitors can never be given 'admin'."
    )


@dataclass
class AuthConfig:
    """Authentication configuration."""

    enabled: bool = False
    anonymous_tier: str | None = None
    session_ttl_hours: int = 168
    max_sessions_per_user: int = 5
    allow_registration: bool = True
    require_invite_code: bool = False
    # Rate limits on the unauthenticated auth endpoints, in the `limits`
    # syntax slowapi uses ("5/minute", several joined by ";"). Login is
    # limited per client (every attempt) and per username (failed attempts);
    # registration per client.
    login_rate_limit: str = "20/minute;200/hour"
    login_rate_limit_per_username: str = "5/minute;30/hour"
    register_rate_limit: str = "5/minute;20/hour"
    providers: dict[str, OAuthProviderConfig] = field(default_factory=dict)
    ephemeral_min_tier: str = "write"
    ephemeral_max_per_user: int = 1
    ephemeral_default_ttl: int = 86400
    ephemeral_max_ttl: int = 604800
    usage_tiers: dict[str, UsageTierConfig] = field(default_factory=dict)

    def __post_init__(self):
        self.anonymous_tier = normalize_anonymous_tier(self.anonymous_tier)


@dataclass
class Settings:
    """Global application settings."""

    default_editor: str = field(default_factory=lambda: os.environ.get("EDITOR", "vim"))
    ai_provider: Literal[
        "anthropic", "openai", "gemini", "openrouter", "ollama", "local", "stub", "none"
    ] = "stub"
    ai_model: str = "claude-sonnet-4-20250514"
    ai_api_key: str = ""
    ai_api_base: str = ""
    summary_length: int = 280
    enable_mcp: bool = True
    # Defaults live beside the config file this process resolved (#377): under
    # PYRITE_CONFIG_DIR or a repo-local .pyrite/ they are no longer ~/.pyrite.
    index_path: Path = field(default_factory=lambda: default_data_dir() / "index.db")
    host: str = "127.0.0.1"
    port: int = 8088
    # Security
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8088",
        ]
    )
    # Extra hostnames a credential-free server answers (auth disabled with no
    # API keys, or anonymous_tier "write"), beyond localhost, 127.0.0.1, ::1
    # and a non-wildcard `host`. Requests addressed to any other Host get 421
    # (pyrite/server/request_guard.py).
    allowed_hosts: list[str] = field(default_factory=list)
    api_key: str = ""  # Empty = auth disabled (backwards-compatible)
    api_keys: list[dict[str, str]] = field(default_factory=list)  # [{key_hash, role, label}]
    auth: AuthConfig = field(default_factory=AuthConfig)
    rate_limit_read: str = "100/minute"
    rate_limit_write: str = "30/minute"
    rate_limit_admin: str = "10/minute"
    mcp_rate_limit_exempt_local: bool = True
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    search_mode: str = "keyword"
    search_backend: str = "sqlite"  # "sqlite" or "postgres"
    database_url: str = ""  # PostgreSQL connection string (for postgres backend)
    workspace_path: Path = field(default_factory=lambda: default_data_dir() / "repos")
    strict_plugins: bool = False  # Raise on plugin load failures (dev/CI mode)
    prewarm_embeddings: bool = False  # Pre-load embedding model on server startup
    # Embed entries on write. Off = keyword search only, no torch import, no
    # model download; `pyrite index embed` can backfill later. Env: PYRITE_AUTO_EMBED
    auto_embed: bool = True
    # Extra Content-Security-Policy sources for the public /site pages, in CSP
    # syntax, appended to the built-in policy (script-src 'self' ...), e.g.
    # "script-src https://plausible.io 'sha256-...'; connect-src https://plausible.io"
    # for a reverse proxy that injects an analytics script. Env: PYRITE_SITE_CSP_EXTRA
    site_csp_extra: str = ""
    # White-label branding folder. None = use built-in Pyrite defaults.
    # Env override: PYRITE_BRANDING_DIR
    branding_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["PYRITE_BRANDING_DIR"]).expanduser().resolve()
            if os.environ.get("PYRITE_BRANDING_DIR")
            else None
        )
    )

    def __post_init__(self):
        self.index_path = Path(self.index_path).expanduser().resolve()
        self.workspace_path = Path(self.workspace_path).expanduser().resolve()
        if self.branding_dir is not None:
            self.branding_dir = Path(self.branding_dir).expanduser().resolve()
        # Load from environment if not set
        if not self.ai_api_key:
            self.ai_api_key = (
                os.environ.get("OPENAI_API_KEY", "")
                or os.environ.get("ANTHROPIC_API_KEY", "")
                or os.environ.get("GEMINI_API_KEY", "")
            )
        if not self.ai_api_base:
            self.ai_api_base = os.environ.get("OPENAI_API_BASE", "")
        # Default base URL for Gemini's OpenAI-compatible endpoint
        if self.ai_provider == "gemini" and not self.ai_api_base:
            self.ai_api_base = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _refuse_unresolvable(path: Path) -> None:
    """Raise when ``path`` cannot be resolved: an unknown ``~user`` or a symlink loop.

    ``Path.resolve()`` in non-strict mode raised ``RuntimeError`` on a loop
    through Python 3.12 and silently returns the unresolved path from 3.13
    on, so the check resolves strictly. A path that does not exist yet is
    fine; any other ``OSError`` (a permission error, say) is left to the
    ordinary non-strict resolve, as before.
    """
    try:
        path.expanduser().resolve(strict=True)
    except FileNotFoundError:
        return
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise
        return


@dataclass
class PyriteConfig:
    """
    Root configuration for pyrite.

    Manages multiple knowledge bases, repositories, subscriptions, and global settings.

    Structure supports:
    - Multiple KBs, each can be standalone or part of a repository
    - Repositories that contain multiple KBs (e.g., CascadeSeries with timeline + research-kb)
    - GitHub OAuth for private repository access
    - Subscriptions to remote KBs
    """

    version: str = "1.0"
    knowledge_bases: list[KBConfig] = field(default_factory=list)
    repositories: list[Repository] = field(default_factory=list)
    subscriptions: list[Subscription] = field(default_factory=list)
    github_auth: GitHubAuth | None = None
    settings: Settings = field(default_factory=Settings)

    _kb_by_name: dict[str, KBConfig] = field(default_factory=dict, repr=False)
    _repo_by_name: dict[str, Repository] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._rebuild_index()

    def _rebuild_index(self):
        """Rebuild the lookup indexes."""
        self._kb_by_name = {kb.name: kb for kb in self.knowledge_bases}
        self._repo_by_name = {repo.name: repo for repo in self.repositories}

    # Fallback lookup for DB-registered KBs not in config.yaml
    _db_kb_cache: dict[str, KBConfig] = field(default_factory=dict, repr=False)
    # Set by load_config for an untrusted repo-local config: every KB, from
    # the yaml or from the index's registry, must resolve inside this tree.
    _confine_root: Path | None = field(default=None, repr=False)

    def get_kb(self, name: str) -> KBConfig | None:
        """Get a KB by name (config.yaml first, then DB-registered KBs)."""
        kb = self._kb_by_name.get(name)
        if kb:
            return kb
        return self._db_kb_cache.get(name)

    def confined_default_role(self, value: str | None) -> str | None:
        """A default_role as this config may honour it. Under an untrusted
        repo-local config only "none" is kept: anything that opens a KB would
        come from the tree (its yaml or its own index), not from an operator
        of this server. Publishing a KB needs a trusted config."""
        if self._confine_root is not None and value != "none":
            return None
        return value

    def refuse_outside_tree(self, path: Path | str, what: str) -> None:
        """Raise ConfigError when an untrusted config would reach outside its tree."""
        if self._confine_root is None:
            return
        if not Path(path).expanduser().resolve().is_relative_to(self._confine_root):
            raise ConfigError(
                f"Refusing {what}: {path} is outside {self._confine_root}, the tree of the "
                "untrusted repo-local config in use. Point PYRITE_CONFIG_DIR at your own "
                "config to work outside it."
            )

    def kb_config_from_registry_row(self, kb_data: dict) -> KBConfig | None:
        """The one way an index registry row becomes a KBConfig.

        Returns None -- with a warning -- for a row that must not be used: no
        name or path, a path that cannot be resolved, or, under an untrusted
        repo-local config, a path outside that config's tree. Under such a
        config the row's default_role goes through confined_default_role.
        Every caller that turns a registry row into a KBConfig uses this, so a
        row refused at load is not found anywhere else either.
        """
        name = kb_data.get("name", "")
        path = kb_data.get("path", "")
        if not name or not path:
            return None
        default_role = kb_data.get("default_role")
        try:
            _refuse_unresolvable(Path(path))
            if self._confine_root is not None:
                if not Path(path).expanduser().resolve().is_relative_to(self._confine_root):
                    logger.warning(
                        "Not loading registry KB %r: its path is outside the tree of "
                        "the untrusted repo-local config (%s)",
                        name,
                        self._confine_root,
                    )
                    return None
                default_role = self.confined_default_role(default_role)
            return KBConfig(
                name=name,
                path=Path(path),
                kb_type=kb_data.get("kb_type") or "generic",
                description=kb_data.get("description") or "",
                default_role=default_role,
            )
        except (OSError, RuntimeError, ValueError):
            # A registry path that cannot be resolved (an unknown ~user, a
            # symlink loop) must not stop the load -- this runs while every
            # entry point is constructed. The KB is left out: unreachable,
            # never open.
            logger.warning(
                "Registry KB %r has a path that cannot be resolved (%s); not loading it",
                name,
                path,
                exc_info=True,
            )
            return None

    def register_db_kbs(self, db_kbs: list[dict]) -> int:
        """Register DB-added KBs as a fallback lookup (not added to knowledge_bases).

        Config.yaml KBs always take precedence. DB KBs are available via get_kb()
        but don't appear in knowledge_bases (so seed_from_config won't re-register them).

        Args:
            db_kbs: List of dicts with keys: name, path, kb_type, description
        """
        added = 0
        for kb_data in db_kbs:
            name = kb_data.get("name", "")
            if not name or name in self._kb_by_name:
                continue
            kb = self.kb_config_from_registry_row(kb_data)
            if kb is None:
                continue
            self._db_kb_cache[name] = kb
            added += 1
        return added

    def forget_db_kb(self, name: str) -> None:
        """Drop a registry KB from the fallback lookup.

        The registry's write paths call this (and `register_db_kbs` again,
        for a row that still exists) so that `get_kb`, `all_kbs` and the
        access policy never read a row older than the index's: the cache was
        once filled at startup and never refreshed, and a KB an admin closed
        stayed public until a restart.
        """
        self._db_kb_cache.pop(name, None)

    def defined_in_config(self, name: str) -> bool:
        """Is `name` a KB of config.yaml (not only of the index's registry)?"""
        return name in self._kb_by_name

    def get_kb_by_shortname(self, shortname: str) -> KBConfig | None:
        """Get a KB by its shortname alias."""
        for kb in self.knowledge_bases:
            if kb.shortname == shortname:
                return kb
        return None

    def list_kbs(self, kb_type: str | None = None) -> list[KBConfig]:
        """List all KBs, optionally filtered by type."""
        if kb_type is None:
            return self.knowledge_bases
        # Accept enum or string
        type_str = kb_type.value if hasattr(kb_type, "value") else kb_type
        return [kb for kb in self.knowledge_bases if kb.kb_type == type_str]

    def all_kbs(self) -> list[KBConfig]:
        """All KBs including DB-registered (``pyrite kb add``) ones.

        ``knowledge_bases`` holds only config.yaml KBs; DB-registered KBs live
        in the fallback cache (resolvable via :meth:`get_kb` but deliberately
        absent from ``knowledge_bases`` so seeding doesn't re-register them).
        Operations that must act on *every* KB — notably indexing — should
        enumerate via this method, not ``knowledge_bases`` directly, or they
        silently skip ``kb add`` KBs. config.yaml KBs take precedence on name.
        """
        seen = {kb.name for kb in self.knowledge_bases}
        extra = [kb for name, kb in self._db_kb_cache.items() if name not in seen]
        return [*self.knowledge_bases, *extra]

    def add_kb(self, kb: KBConfig) -> None:
        """Add a KB to the registry."""
        if kb.name in self._kb_by_name:
            raise ConfigError(f"KB with name '{kb.name}' already exists")
        self.knowledge_bases.append(kb)
        self._kb_by_name[kb.name] = kb

    def remove_kb(self, name: str) -> bool:
        """Remove a KB from the registry. Returns True if removed."""
        if name not in self._kb_by_name:
            return False
        kb = self._kb_by_name.pop(name)
        self.knowledge_bases.remove(kb)
        return True

    # Repository management
    def get_repo(self, name: str) -> Repository | None:
        """Get a repository by name."""
        return self._repo_by_name.get(name)

    def add_repo(self, repo: Repository) -> None:
        """Add a repository to the registry."""
        if repo.name in self._repo_by_name:
            raise ConfigError(f"Repository with name '{repo.name}' already exists")
        self.repositories.append(repo)
        self._repo_by_name[repo.name] = repo

    def remove_repo(self, name: str) -> bool:
        """Remove a repository from the registry. Returns True if removed."""
        if name not in self._repo_by_name:
            return False
        repo = self._repo_by_name.pop(name)
        self.repositories.remove(repo)
        return True

    def get_kbs_in_repo(self, repo_name: str) -> list[KBConfig]:
        """Get all KBs that belong to a repository."""
        return [kb for kb in self.knowledge_bases if kb.repo == repo_name]

    def validate(self) -> dict[str, list[str]]:
        """Validate all KBs and repos. Returns dict of name -> errors."""
        results = {}
        for kb in self.knowledge_bases:
            errors = kb.validate()
            if errors:
                results[f"kb:{kb.name}"] = errors
        for repo in self.repositories:
            errors = repo.validate()
            if errors:
                results[f"repo:{repo.name}"] = errors
        return results

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization."""
        result: dict[str, Any] = {
            "version": self.version,
            "knowledge_bases": [
                {
                    "name": kb.name,
                    "path": str(kb.path),
                    "kb_type": kb.kb_type,
                    "description": kb.description,
                    "read_only": kb.read_only,
                    **({"remote": kb.remote} if kb.remote else {}),
                    **({"repo": kb.repo} if kb.repo else {}),
                    **({"repo_subpath": kb.repo_subpath} if kb.repo_subpath else {}),
                    **({"shortname": kb.shortname} if kb.shortname else {}),
                    **({"ephemeral": kb.ephemeral} if kb.ephemeral else {}),
                    **({"ttl": kb.ttl} if kb.ttl else {}),
                    **({"created_at_ts": kb.created_at_ts} if kb.created_at_ts else {}),
                    **({"default_role": kb.default_role} if kb.default_role is not None else {}),
                }
                for kb in self.knowledge_bases
            ],
        }

        if self.repositories:
            result["repositories"] = [
                {
                    "name": repo.name,
                    "path": str(repo.path),
                    "remote": repo.remote,
                    "branch": repo.branch,
                    "auto_sync": repo.auto_sync,
                    "sync_interval": repo.sync_interval,
                    "auth_method": repo.auth_method,
                    **({"github_app_id": repo.github_app_id} if repo.github_app_id else {}),
                    **({"kb_paths": repo.kb_paths} if repo.kb_paths else {}),
                }
                for repo in self.repositories
            ]

        result["subscriptions"] = [
            {
                "url": sub.url,
                "local_path": str(sub.local_path),
                "auto_sync": sub.auto_sync,
                "sync_interval": sub.sync_interval,
                **({"repo": sub.repo} if sub.repo else {}),
            }
            for sub in self.subscriptions
        ]

        # GitHub auth - store in separate secure file, just reference here
        if self.github_auth and self.github_auth.has_oauth_credentials:
            result["github_auth"] = {
                "configured": True,
                "has_valid_token": self.github_auth.has_valid_token,
            }

        result["settings"] = {
            "default_editor": self.settings.default_editor,
            "ai_provider": self.settings.ai_provider,
            "ai_model": self.settings.ai_model,
            "summary_length": self.settings.summary_length,
            "enable_mcp": self.settings.enable_mcp,
            "index_path": str(self.settings.index_path),
            "host": self.settings.host,
            "port": self.settings.port,
            "cors_origins": self.settings.cors_origins,
            **(
                {"allowed_hosts": self.settings.allowed_hosts}
                if self.settings.allowed_hosts
                else {}
            ),
            **(
                {"site_csp_extra": self.settings.site_csp_extra}
                if self.settings.site_csp_extra
                else {}
            ),
            "api_key": self.settings.api_key,
            **({"api_keys": self.settings.api_keys} if self.settings.api_keys else {}),
            "auth": {
                "enabled": self.settings.auth.enabled,
                "anonymous_tier": self.settings.auth.anonymous_tier,
                "session_ttl_hours": self.settings.auth.session_ttl_hours,
                "max_sessions_per_user": self.settings.auth.max_sessions_per_user,
                "allow_registration": self.settings.auth.allow_registration,
                "require_invite_code": self.settings.auth.require_invite_code,
                "login_rate_limit": self.settings.auth.login_rate_limit,
                "login_rate_limit_per_username": self.settings.auth.login_rate_limit_per_username,
                "register_rate_limit": self.settings.auth.register_rate_limit,
                "ephemeral_min_tier": self.settings.auth.ephemeral_min_tier,
                "ephemeral_max_per_user": self.settings.auth.ephemeral_max_per_user,
                "ephemeral_default_ttl": self.settings.auth.ephemeral_default_ttl,
                "ephemeral_max_ttl": self.settings.auth.ephemeral_max_ttl,
                **(
                    {
                        "providers": {
                            name: {
                                "client_id": p.client_id,
                                "client_secret": p.client_secret,
                                "allowed_orgs": p.allowed_orgs,
                                "org_tier_map": p.org_tier_map,
                                "default_tier": p.default_tier,
                            }
                            for name, p in self.settings.auth.providers.items()
                        }
                    }
                    if self.settings.auth.providers
                    else {}
                ),
            },
            "rate_limit_read": self.settings.rate_limit_read,
            "rate_limit_write": self.settings.rate_limit_write,
            "rate_limit_admin": self.settings.rate_limit_admin,
            "mcp_rate_limit_exempt_local": self.settings.mcp_rate_limit_exempt_local,
            "embedding_model": self.settings.embedding_model,
            "embedding_dimensions": self.settings.embedding_dimensions,
            "search_mode": self.settings.search_mode,
        }

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PyriteConfig":
        """Create from dictionary (YAML loaded)."""
        knowledge_bases = []
        for kb_data in data.get("knowledge_bases", []):
            knowledge_bases.append(
                KBConfig(
                    name=kb_data["name"],
                    path=Path(kb_data["path"]),
                    kb_type=kb_data.get("kb_type", "generic"),
                    description=kb_data.get("description", ""),
                    read_only=kb_data.get("read_only", False),
                    remote=kb_data.get("remote"),
                    repo=kb_data.get("repo"),
                    repo_subpath=kb_data.get("repo_subpath", ""),
                    shortname=kb_data.get("shortname"),
                    ephemeral=kb_data.get("ephemeral", False),
                    ttl=kb_data.get("ttl"),
                    created_at_ts=kb_data.get("created_at_ts"),
                    default_role=kb_data.get("default_role"),
                )
            )
            _repair_ephemeral_default_role(knowledge_bases[-1])

        repositories = []
        for repo_data in data.get("repositories", []):
            repositories.append(
                Repository(
                    name=repo_data["name"],
                    path=Path(repo_data["path"]),
                    remote=repo_data.get("remote"),
                    branch=repo_data.get("branch", "main"),
                    auto_sync=repo_data.get("auto_sync", True),
                    sync_interval=repo_data.get("sync_interval", 3600),
                    auth_method=repo_data.get("auth_method", "none"),
                    github_app_id=repo_data.get("github_app_id"),
                    kb_paths=repo_data.get("kb_paths", []),
                )
            )

        subscriptions = []
        for sub_data in data.get("subscriptions", []):
            subscriptions.append(
                Subscription(
                    url=sub_data["url"],
                    local_path=Path(sub_data["local_path"]),
                    auto_sync=sub_data.get("auto_sync", True),
                    sync_interval=sub_data.get("sync_interval", 3600),
                    repo=sub_data.get("repo"),
                )
            )

        # Load GitHub auth from secure file if referenced
        github_auth = None
        github_auth_file = trusted_config_dir() / "github_auth.yaml"
        if github_auth_file.exists():
            try:
                auth_data = load_yaml_file(github_auth_file)
                github_auth = GitHubAuth.from_dict(auth_data)
            except Exception:
                logger.warning(
                    "Failed to load GitHub auth from %s", github_auth_file, exc_info=True
                )

        settings_data = data.get("settings", {})
        auth_data = settings_data.get("auth", {})

        # Parse OAuth providers
        providers: dict[str, OAuthProviderConfig] = {}
        for pname, pdata in auth_data.get("providers", {}).items():
            providers[pname] = OAuthProviderConfig(
                client_id=pdata.get("client_id", ""),
                client_secret=pdata.get("client_secret", ""),
                allowed_orgs=pdata.get("allowed_orgs", []),
                org_tier_map=pdata.get("org_tier_map", {}),
                default_tier=pdata.get("default_tier", "read"),
            )
        # Env var fallback for GitHub provider
        if "github" not in providers:
            gh_id = os.environ.get("PYRITE_GITHUB_CLIENT_ID", "")
            gh_secret = os.environ.get("PYRITE_GITHUB_CLIENT_SECRET", "")
            if gh_id and gh_secret:
                providers["github"] = OAuthProviderConfig(client_id=gh_id, client_secret=gh_secret)
        else:
            gh = providers["github"]
            if not gh.client_id:
                gh.client_id = os.environ.get("PYRITE_GITHUB_CLIENT_ID", "")
            if not gh.client_secret:
                gh.client_secret = os.environ.get("PYRITE_GITHUB_CLIENT_SECRET", "")

        settings_kwargs: dict[str, Any] = {}
        if settings_data.get("index_path"):
            settings_kwargs["index_path"] = Path(settings_data["index_path"])
        settings = Settings(
            **settings_kwargs,
            default_editor=settings_data.get("default_editor", os.environ.get("EDITOR", "vim")),
            ai_provider=settings_data.get("ai_provider", "stub"),
            ai_model=settings_data.get("ai_model", "claude-sonnet-4-20250514"),
            summary_length=settings_data.get("summary_length", 280),
            enable_mcp=settings_data.get("enable_mcp", True),
            host=settings_data.get("host", "127.0.0.1"),
            port=settings_data.get("port", 8088),
            cors_origins=settings_data.get(
                "cors_origins",
                ["http://localhost:3000", "http://localhost:5173", "http://localhost:8088"],
            ),
            allowed_hosts=list(settings_data.get("allowed_hosts") or []),
            api_key=settings_data.get("api_key", ""),
            api_keys=settings_data.get("api_keys", []),
            auth=AuthConfig(
                enabled=auth_data.get("enabled", False),
                anonymous_tier=auth_data.get("anonymous_tier"),
                session_ttl_hours=auth_data.get("session_ttl_hours", 168),
                max_sessions_per_user=auth_data.get("max_sessions_per_user", 5),
                allow_registration=auth_data.get("allow_registration", True),
                require_invite_code=auth_data.get("require_invite_code", False),
                login_rate_limit=auth_data.get("login_rate_limit", AuthConfig.login_rate_limit),
                login_rate_limit_per_username=auth_data.get(
                    "login_rate_limit_per_username", AuthConfig.login_rate_limit_per_username
                ),
                register_rate_limit=auth_data.get(
                    "register_rate_limit", AuthConfig.register_rate_limit
                ),
                providers=providers,
                ephemeral_min_tier=auth_data.get("ephemeral_min_tier", "write"),
                ephemeral_max_per_user=auth_data.get("ephemeral_max_per_user", 1),
                ephemeral_default_ttl=auth_data.get("ephemeral_default_ttl", 86400),
                ephemeral_max_ttl=auth_data.get("ephemeral_max_ttl", 604800),
                usage_tiers={
                    tname: UsageTierConfig(**tdata)
                    for tname, tdata in auth_data.get("usage_tiers", {}).items()
                },
            ),
            rate_limit_read=settings_data.get("rate_limit_read", "100/minute"),
            rate_limit_write=settings_data.get("rate_limit_write", "30/minute"),
            rate_limit_admin=settings_data.get("rate_limit_admin", "10/minute"),
            mcp_rate_limit_exempt_local=settings_data.get("mcp_rate_limit_exempt_local", True),
            embedding_model=settings_data.get("embedding_model", "all-MiniLM-L6-v2"),
            embedding_dimensions=settings_data.get("embedding_dimensions", 384),
            search_mode=settings_data.get("search_mode", "keyword"),
            auto_embed=settings_data.get("auto_embed", True),
            site_csp_extra=settings_data.get("site_csp_extra", "") or "",
        )

        return cls(
            version=data.get("version", "1.0"),
            knowledge_bases=knowledge_bases,
            repositories=repositories,
            subscriptions=subscriptions,
            github_auth=github_auth,
            settings=settings,
        )


# Global configuration paths
DEFAULT_CONFIG_DIR = Path("~/.pyrite").expanduser().resolve()
LOCAL_CONFIG_DIRNAME = ".pyrite"


def _home_config_dir() -> Path:
    return Path("~/.pyrite").expanduser().resolve()


def resolve_config_source(start: Path | None = None) -> tuple[Path, bool]:
    """Where this process reads its config from, and whether it is trusted.

    1. ``PYRITE_DATA_DIR`` or ``PYRITE_CONFIG_DIR`` when set -- explicit wins,
       and is trusted: the user named it.
    2. A repo-local ``.pyrite/config.yaml``, searched upward from ``start``
       (the cwd). A worktree per session (ADR-0032) needs a KB registry that
       points at *that* checkout's ``kb/``; through ``~/.pyrite`` every
       worker's ``pyrite update`` landed in the main checkout instead.
       **Untrusted**: it may belong to a cloned or downloaded tree, so only
       the keys :func:`_restrict_untrusted` lets through are used.
    3. ``~/.pyrite``, trusted.
    """
    explicit = os.environ.get("PYRITE_DATA_DIR") or os.environ.get("PYRITE_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve(), True
    home = _home_config_dir()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        local = candidate / LOCAL_CONFIG_DIRNAME
        if (local / "config.yaml").is_file():
            local = local.resolve()
            return local, local == home
    return home, True


def resolve_config_dir(start: Path | None = None) -> Path:
    """The config directory :func:`resolve_config_source` picks."""
    return resolve_config_source(start)[0]


CONFIG_DIR, _import_trusted = resolve_config_source()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
# The repo-local directory CONFIG_DIR was resolved to at import, when it was
# one: a process started inside a tree must not trust that tree's config just
# because the lookup happened early.
_UNTRUSTED_IMPORT_DIR: Path | None = None if _import_trusted else CONFIG_DIR
del _import_trusted


def current_config_source() -> tuple[Path, bool]:
    """The config file for *this call*, and whether it is trusted.

    The module-level CONFIG_DIR is fixed at import. If something pinned it --
    an env var, a repo-local directory found at import, or a test
    monkeypatching it away from ~/.pyrite -- honour that. Otherwise resolve
    again from the cwd, so a process that started elsewhere and `cd`ed into
    a worktree still finds that worktree's `.pyrite/config.yaml`.
    """
    if CONFIG_DIR != _home_config_dir():
        return CONFIG_FILE, CONFIG_DIR != _UNTRUSTED_IMPORT_DIR
    config_dir, trusted = resolve_config_source()
    return config_dir / "config.yaml", trusted


def current_config_file() -> Path:
    """The config file for *this call* (see :func:`current_config_source`)."""
    return current_config_source()[0]


def trusted_config_dir() -> Path:
    """Where credentials live: CONFIG_DIR, unless that is an untrusted
    repo-local directory, in which case ``~/.pyrite``. A tree's
    ``.pyrite/`` neither supplies the user's GitHub credentials nor receives
    them."""
    if CONFIG_DIR == _UNTRUSTED_IMPORT_DIR:
        return _home_config_dir()
    return CONFIG_DIR


def default_data_dir() -> Path:
    """Where the index and workspace live when nothing names them.

    The directory of the config file this call resolves: ``~/.pyrite`` for a
    default install (unchanged), but the ``PYRITE_CONFIG_DIR`` or repo-local
    ``.pyrite/`` directory when one is in effect. Before #377 the index stayed
    at ``~/.pyrite/index.db`` whatever the config dir, so a sandboxed
    ``pyrite kb add`` registered into the user's real index.

    Precedence for ``index_path``: ``PYRITE_DATA_DIR`` > ``settings.index_path``
    in config.yaml > this default.
    """
    return current_config_file().parent


def ensure_config_dir() -> Path:
    """Ensure the config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _apply_env_overrides(config: PyriteConfig) -> None:
    """Apply PYRITE_* environment variable overrides to config.

    Only overrides values when the env var is set. Does not clobber
    values that were explicitly set in config.yaml (except for env vars
    that are explicitly provided — env vars always win when present).
    """
    env = os.environ.get

    if val := env("PYRITE_HOST"):
        config.settings.host = val
    # Port precedence: PYRITE_PORT > PORT > config/default.
    # PORT is what Railway/Heroku inject; PYRITE_PORT stays the
    # explicit override so a compose file can pin a port even when
    # the platform also sets PORT.
    raw_port = env("PYRITE_PORT") or env("PORT")
    if raw_port:
        try:
            config.settings.port = int(raw_port)
        except ValueError as exc:
            source = "PYRITE_PORT" if env("PYRITE_PORT") else "PORT"
            raise ValueError(f"{source} must be an integer port, got {raw_port!r}") from exc
    if val := env("PYRITE_AUTH_ENABLED"):
        config.settings.auth.enabled = val.lower() in ("true", "1", "yes")
    if val := env("PYRITE_AUTH_ANONYMOUS_TIER"):
        config.settings.auth.anonymous_tier = normalize_anonymous_tier(
            val, "PYRITE_AUTH_ANONYMOUS_TIER"
        )
    if val := env("PYRITE_AUTH_ALLOW_REGISTRATION"):
        config.settings.auth.allow_registration = val.lower() in ("true", "1", "yes")
    if val := env("PYRITE_AUTH_LOGIN_RATE_LIMIT"):
        config.settings.auth.login_rate_limit = val
    if val := env("PYRITE_AUTH_LOGIN_RATE_LIMIT_PER_USERNAME"):
        config.settings.auth.login_rate_limit_per_username = val
    if val := env("PYRITE_AUTH_REGISTER_RATE_LIMIT"):
        config.settings.auth.register_rate_limit = val
    if val := env("PYRITE_CORS_ORIGINS"):
        config.settings.cors_origins = [s.strip() for s in val.split(",")]
    if val := env("PYRITE_ALLOWED_HOSTS"):
        config.settings.allowed_hosts = [s.strip() for s in val.split(",") if s.strip()]
    if val := env("PYRITE_API_KEY"):
        config.settings.api_key = val
    if val := env("PYRITE_AI_PROVIDER"):
        config.settings.ai_provider = val
    if val := env("PYRITE_AI_MODEL"):
        config.settings.ai_model = val
    if val := env("PYRITE_SEARCH_MODE"):
        config.settings.search_mode = val
    if val := env("PYRITE_STRICT_PLUGINS"):
        config.settings.strict_plugins = val.lower() in ("true", "1", "yes")
    if val := env("PYRITE_PREWARM_EMBEDDINGS"):
        config.settings.prewarm_embeddings = val.lower() in ("true", "1", "yes")
    if val := env("PYRITE_AUTO_EMBED"):
        config.settings.auto_embed = val.lower() in ("true", "1", "yes")
    if val := env("PYRITE_SITE_CSP_EXTRA"):
        config.settings.site_csp_extra = val

    # When PYRITE_DATA_DIR is set, derive index_path and workspace_path from it
    data_dir = env("PYRITE_DATA_DIR")
    if data_dir:
        data_path = Path(data_dir).expanduser().resolve()
        config.settings.index_path = data_path / "index.db"
        config.settings.workspace_path = data_path / "repos"


# What a repo-local (untrusted) config may set. Everything
# here stays inside the tree the config came from, or is a harmless switch;
# anything that changes what code runs (the embedding model, the editor, AI
# providers), where Pyrite reads or writes outside the tree, or how a server
# is exposed (host, auth, API keys, CORS) is ignored. Widening this list is a
# security decision.
_UNTRUSTED_TOP_KEYS = frozenset({"version", "knowledge_bases", "settings"})
_UNTRUSTED_KB_KEYS = frozenset({"name", "path", "kb_type", "description", "read_only", "shortname"})
# default_role is an exposure setting: from an untrusted source only "none"
# (which can only close a KB) is kept.
_UNTRUSTED_SETTINGS_KEYS = frozenset({"index_path", "auto_embed", "search_mode", "summary_length"})


def _untrusted_settings_defaults() -> dict[str, Any]:
    """Settings as ``to_dict`` writes them for a fresh config: a file Pyrite
    saved back holds these, and holding them changes nothing."""
    return PyriteConfig().to_dict()["settings"]


def _untrusted_kb_entry(kb: dict[str, Any], ignored: list[str] | None = None) -> dict[str, Any]:
    """A KB entry reduced to what an untrusted config may set."""
    kept = {k: v for k, v in kb.items() if k in _UNTRUSTED_KB_KEYS}
    if kb.get("default_role") == "none":
        kept["default_role"] = "none"
    if ignored is not None:
        for key, value in kb.items():
            if key in kept or value in (None, "", False, []):
                continue
            ignored.append(f"knowledge_bases[{kb.get('name')}].{key}")
    return kept


def _restrict_untrusted(data: Any, config_file: Path) -> tuple[dict[str, Any], list[str]]:
    """Keep only the keys an untrusted config may set. Returns the filtered
    data and the names of ignored keys whose value would have changed
    something (a key holding its default is dropped silently)."""
    if not isinstance(data, dict):
        return {}, []
    ignored: list[str] = []
    kept: dict[str, Any] = {}
    for key, value in data.items():
        if key in _UNTRUSTED_TOP_KEYS:
            kept[key] = value
        elif value:
            ignored.append(key)

    kbs = []
    for kb in kept.get("knowledge_bases") or []:
        if not isinstance(kb, dict):
            continue
        kbs.append(_untrusted_kb_entry(kb, ignored))
    if "knowledge_bases" in kept:
        kept["knowledge_bases"] = kbs

    settings = kept.get("settings")
    if isinstance(settings, dict):
        defaults = _untrusted_settings_defaults()
        for key, value in settings.items():
            if key not in _UNTRUSTED_SETTINGS_KEYS and value != defaults.get(key, None):
                ignored.append(f"settings.{key}")
        kept["settings"] = {k: v for k, v in settings.items() if k in _UNTRUSTED_SETTINGS_KEYS}
    elif "settings" in kept:
        kept["settings"] = {}
    return kept, ignored


def _contain_untrusted_paths(config: PyriteConfig, root: Path) -> list[str]:
    """Drop KBs, and reset an index path, that resolve outside ``root``
    (symlinks followed). Returns what was refused."""
    refused: list[str] = []

    def inside(path: Path) -> bool:
        return Path(path).resolve().is_relative_to(root)

    keep = []
    for kb in config.knowledge_bases:
        if inside(kb.path):
            keep.append(kb)
        else:
            refused.append(f"knowledge base {kb.name!r} (path outside {root})")
    if len(keep) != len(config.knowledge_bases):
        config.knowledge_bases = keep
        config._rebuild_index()

    # workspace_path is never read from config.yaml, so it is always the
    # default beside this config file: inside the tree.
    if not inside(config.settings.index_path):
        refused.append("settings.index_path (outside the tree)")
        config.settings.index_path = Settings().index_path
    return refused


def load_config() -> PyriteConfig:
    """
    Load configuration from config.yaml.

    Creates default config if it doesn't exist. A repo-local config is
    untrusted (see :func:`resolve_config_source`): only the keys in
    ``_UNTRUSTED_*_KEYS`` are read from it, and only paths inside its own
    tree; the rest is ignored with a warning.
    """
    ensure_config_dir()

    config_file, trusted = current_config_source()
    if config_file.exists():
        data = load_yaml_file(config_file)
        ignored: list[str] = []
        if not trusted:
            data, ignored = _restrict_untrusted(data, config_file)
        config = PyriteConfig.from_dict(data)
        if not trusted:
            root = config_file.parent.parent.resolve()
            config._confine_root = root
            ignored += _contain_untrusted_paths(config, root)
            if ignored:
                logger.warning(
                    "Ignoring %s from the untrusted repo-local config %s: a config "
                    "found by searching up from the working directory may only name "
                    "paths inside its own tree. To trust it in full, point "
                    "PYRITE_CONFIG_DIR at %s.",
                    ", ".join(ignored),
                    config_file,
                    config_file.parent,
                )
    else:
        # Create default config
        config = PyriteConfig()

    # Apply environment variable overrides
    _apply_env_overrides(config)

    # Load kb.yaml for each KB
    for kb in config.knowledge_bases:
        kb.load_kb_yaml()

    return config


def open_registration_warning(config: PyriteConfig) -> str | None:
    """The startup warning for an auth-enabled server anyone can sign up to.

    None unless auth is on and registration needs no invite code. A
    self-registered user reads only KBs whose ``default_role`` is read or
    write, so the warning names those: they are what a
    stranger gets by creating an account.
    """
    auth = config.settings.auth
    if not auth.enabled or not auth.allow_registration or auth.require_invite_code:
        return None
    public = sorted(kb.name for kb in config.all_kbs() if kb.default_role in ("read", "write"))
    readable = (
        f"they can read (never write) KBs with default_role read or write: {', '.join(public)}"
        if public
        else "no KB has default_role read or write, so they can read nothing until granted"
    )
    github = any(p.client_id for p in auth.providers.values())
    how = "an account (on the web form or through GitHub sign-in)" if github else "an account"
    advice = "Set settings.auth.allow_registration: false or require_invite_code: true to close it"
    if github:
        advice += (
            "; GitHub sign-up then creates accounts only for members of the provider's "
            "allowed_orgs or of an org in its org_tier_map"
        )
    return (
        "Auth is enabled with open registration: anyone who can reach this server "
        f"can create {how}, and {readable}. {advice}."
    )


def _repair_ephemeral_default_role(kb: KBConfig) -> None:
    """Make an ephemeral KB without an access policy private.

    Versions before this one set a user's ephemeral KB private only in the
    memory of the process that created it, so config.yaml can hold ephemeral
    KBs with no default_role -- readable by every user at their global role.
    Ephemeral KBs are private by default; restore that on every load.
    """
    if kb.ephemeral and kb.default_role is None:
        logger.warning(
            "Ephemeral KB %r has no default_role; treating it as private ('none')", kb.name
        )
        kb.default_role = "none"


def _removed_set(removed: Iterable[str]) -> frozenset[str]:
    if isinstance(removed, str | bytes):
        raise TypeError("removed= takes an iterable of KB names, not a single string")
    return frozenset(str(name) for name in removed)


def _kb_names_on_disk(config_file: Path) -> list[str]:
    """KB names the config file lists; [] when there is no file.

    A file that exists but cannot be read as a registry raises: treating it as
    empty would switch the protection off exactly when it is needed.
    """
    if not config_file.exists():
        return []

    def unreadable(why: str) -> ConfigFileUnreadableError:
        return ConfigFileUnreadableError(
            f"Refusing to overwrite {config_file}: {why}, so this save cannot check "
            "which knowledge bases it would remove. Fix or move that config.yaml; it "
            "was left unchanged. (In code: save_config(config, allow_drop=True) "
            "replaces it.)",
            config_file=config_file,
            dropped=[],
        )

    try:
        data = load_yaml_file(config_file)
    except Exception as e:
        raise unreadable(f"it could not be parsed ({type(e).__name__})") from e
    if not isinstance(data, dict):
        raise unreadable("it is not a YAML mapping")
    # An absent key is an empty registry; a present one must be a list, as
    # from_dict iterates it (null or false would fail to load, #405). An
    # untrusted repo-local file with null still *loads* (_restrict_untrusted
    # reads it as empty); this check deliberately does not branch on trust:
    # refusing to overwrite a malformed registry costs one manual fix.
    kbs = data.get("knowledge_bases", [])
    if not isinstance(kbs, list):
        raise unreadable("its knowledge_bases is not a list")
    names = []
    for kb in kbs:
        if not isinstance(kb, dict) or kb.get("name") is None or str(kb["name"]) == "":
            raise unreadable("a knowledge_bases entry has no name")
        # from_dict reads kb_data["path"]: an entry without one fails to load.
        if kb.get("path") is None:
            raise unreadable(f"the knowledge_bases entry {kb['name']!s} has no path")
        names.append(str(kb["name"]))
    return names


def check_config_save(
    config: PyriteConfig,
    *,
    removed: Iterable[str] = (),
    allow_drop: bool = False,
) -> None:
    """Raise ConfigSaveRefusedError if saving ``config`` would drop a KB the
    caller did not name.

    The one owner of the rule "a save never drops a KB the caller did not name"
    (#377). Every KB the config file lists must either be in ``config`` or be
    named in ``removed``. ``save_config`` always runs it; a service with
    destructive side effects (deleting a clone, unregistering rows) runs it
    first, *before* those side effects, with the KBs it is about to remove
    still in memory -- the result is the same either side of the removal.
    """
    removed_set = _removed_set(removed)
    if allow_drop:
        return
    real_file = current_config_file().resolve()
    # YAML reads `name: 2024` as an int; the file side is compared as str.
    keeping = {str(kb.name) for kb in config.knowledge_bases}
    dropped = [
        name
        for name in _kb_names_on_disk(real_file)
        if name not in keeping and name not in removed_set
    ]
    if dropped:
        shown = ", ".join(dropped[:5]) + (", ..." if len(dropped) > 5 else "")
        raise ConfigSaveRefusedError(
            f"Refusing to overwrite {real_file}: it lists {len(dropped)} knowledge "
            f"base(s) this process does not know about ({shown}). The file changed "
            "since this process loaded it (another command or process edited it), or "
            "this process never loaded it. Restart the server, or re-run the command, "
            "so it reads the current file. (In code: pass the names removed as "
            "removed=[...], or save_config(config, allow_drop=True) to drop them.)",
            config_file=real_file,
            dropped=dropped,
        )


def save_config(
    config: PyriteConfig,
    *,
    removed: Iterable[str] = (),
    allow_drop: bool = False,
) -> None:
    """Save configuration to config.yaml.

    Refuses (ConfigSaveRefusedError) to drop any KB the file lists that the
    caller did not name in ``removed`` -- see check_config_save. #377: a
    config never loaded from the file replaced a ~50-KB registry, silently.
    """
    check_config_save(config, removed=removed, allow_drop=allow_drop)

    ensure_config_dir()
    config_file = current_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    real_file = config_file.resolve()
    if real_file != config_file.absolute():
        logger.warning("Writing Pyrite config %s through symlink %s", real_file, config_file)
    trusted = current_config_source()[1]
    dump_yaml_file(
        config.to_dict() if trusted else _untrusted_save_data(config, config_file),
        config_file,
        atomic=True,
    )


def _untrusted_save_data(config: PyriteConfig, config_file: Path) -> dict[str, Any]:
    """What may be written back to an untrusted repo-local config.

    The KB registry (allowlisted keys only), and the file's own allowlisted
    settings exactly as they were on disk. Nothing from memory beyond the
    registry: credentials and environment-sourced values are never written.
    """
    on_disk: dict[str, Any] = {}
    if config_file.exists():
        try:
            loaded = load_yaml_file(config_file)
            if isinstance(loaded, dict) and isinstance(loaded.get("settings"), dict):
                on_disk = loaded["settings"]
        except Exception:
            logger.warning("Could not re-read %s; saving no settings", config_file)
    full = config.to_dict()
    data: dict[str, Any] = {
        "version": full.get("version", "1.0"),
        "knowledge_bases": [_untrusted_kb_entry(kb) for kb in full.get("knowledge_bases", [])],
    }
    settings = {k: v for k, v in on_disk.items() if k in _UNTRUSTED_SETTINGS_KEYS}
    if settings:
        data["settings"] = settings
    return data


def auto_discover_kbs(search_paths: list[Path] | None = None) -> list[KBConfig]:
    """
    Auto-discover KBs by looking for kb.yaml files.

    Searches in:
    - Current directory and subdirectories
    - Provided search paths
    """
    if search_paths is None:
        search_paths = [Path.cwd()]

    discovered = []

    for search_path in search_paths:
        search_path = Path(search_path).expanduser().resolve()
        if not search_path.exists():
            continue

        # Look for kb.yaml files
        for kb_yaml in search_path.rglob("kb.yaml"):
            try:
                data = load_yaml_file(kb_yaml)

                name = data.get("name", kb_yaml.parent.name)
                kb_type_str = data.get("kb_type", "generic")

                kb = KBConfig(
                    name=name,
                    path=kb_yaml.parent,
                    kb_type=kb_type_str,
                    description=data.get("description", ""),
                )
                kb.schema = data.get("schema")
                kb.types = data.get("types")
                kb.policies = data.get("policies")

                discovered.append(kb)
            except Exception as e:
                logger.warning("Could not parse %s: %s", kb_yaml, e)

    return discovered


# Legacy compatibility: expose commonly used values at module level
def get_notes_dir(kb_name: str | None = None) -> Path:
    """Get notes directory for a KB (legacy compatibility)."""
    config = load_config()
    if kb_name:
        kb = config.get_kb(kb_name)
        if kb:
            return kb.path
    # Return first KB or default
    if config.knowledge_bases:
        return config.knowledge_bases[0].path
    return Path("./data/notes").resolve()


def get_db_path(kb_name: str | None = None) -> Path:
    """Get database path for a KB (legacy compatibility)."""
    config = load_config()
    if kb_name:
        kb = config.get_kb(kb_name)
        if kb:
            return kb.local_db_path
    # Return global index
    return config.settings.index_path
