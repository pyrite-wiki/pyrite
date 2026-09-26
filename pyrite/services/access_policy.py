"""The access policy: the one place an access decision is made (ADR-0037 §1, #383).

Framework-free: nothing here imports FastAPI, Starlette, MCP or Typer. Each
surface -- REST (`server/api.py`, through `server/authz.py`), MCP
(`server/mcp_routes.py`), the live-update socket (`server/websocket.py`) --
builds a `Principal` and asks this module; none compares roles itself.

What lives here, and nowhere else:

- **The role ladder**: `ROLES` and `ROLE_LEVELS`, and `role_at_least` /
  `lower_role` to compare on it. `tests/test_role_ladder_has_one_home.py`
  fails if the ladder is written out anywhere else under `pyrite/`.
- **The API-key role** (`resolve_api_key_role`).
- **A KB's default role**: config first, then the index registry
  (`KBRegistryService`), and a registry value only as far as
  `PyriteConfig.confined_default_role` lets an untrusted config honour it.
- **The per-KB rule** (`kb_role`): global admin, then an explicit grant, then
  the KB's default role (a self-registered user is capped at read), then the
  user's global role if it covers every KB (`global_access`); the anonymous
  visitor gets the lower of `anonymous_tier` and the KB's default role.
  `AuthService.get_kb_role` fetches the rows and asks this.
- **The effective role, the readable and writable sets**, and
  **concealment**: a KB the principal may not read answers exactly like a KB
  that does not exist (`authorize` checks existence before role).

`AuthService` keeps identity and storage (sessions, users, grants); the
policy reads grants through it and is the only thing that turns one into a
decision. Services do not take a principal (ADR-0037, decision 3): the
policy is asked at each surface's entry point.

A decision is made per call, against current grants. A surface may cache an
answer for one request or tool call (REST caches the readable set on the
request); ADR-0036 is the only longer lifetime (`/ws`).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from ..exceptions import KBNotFoundError

if TYPE_CHECKING:
    from ..config import PyriteConfig
    from ..storage.database import PyriteDB
    from .auth_service import AuthService

# =============================================================================
# The role ladder
# =============================================================================

#: Every valid role, lowest first. The one place the list is written out.
ROLES: tuple[str, ...] = ("read", "write", "admin")

#: The rung of each role. The one place the ladder is written out.
ROLE_LEVELS: dict[str, int] = {"read": 0, "write": 1, "admin": 2}


def role_level(role: str | None) -> int:
    """The rung of `role`; -1 (below every rung) for None or an unknown role."""
    return ROLE_LEVELS.get(role, -1) if role is not None else -1


def role_at_least(role: str | None, tier: str) -> bool:
    """Is `role` at or above `tier`? An unknown `tier` is above every role."""
    return role_level(role) >= ROLE_LEVELS.get(tier, 99)


def lower_role(a: str, b: str) -> str:
    """The lower of two roles on the ladder; `a` on a tie."""
    return min(a, b, key=role_level)


# =============================================================================
# The API-key role
# =============================================================================


def resolve_api_key_role(key: str | None, config: PyriteConfig) -> str | None:
    """Resolve an API key to its role (read/write/admin).

    Returns:
        - "admin" when no keys are configured and auth is disabled (open access)
        - None when no keys are configured and auth is enabled: no key is
          valid, so the caller falls through to its session or the anonymous
          tier (any key used to answer "admin" here)
        - "admin" when key matches the legacy single api_key
        - The configured role when key hash matches an api_keys entry
        - None when key is invalid or missing (auth enabled but key wrong)
    """
    has_single_key = bool(config.settings.api_key)
    has_key_list = bool(config.settings.api_keys)

    # No keys configured: open access only when auth is also disabled.
    # With auth enabled there is no valid key, so any key is refused.
    if not has_single_key and not has_key_list:
        return None if config.settings.auth.enabled else "admin"

    if not key:
        return None

    # Check api_keys list first (takes precedence)
    if has_key_list:
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        for entry in config.settings.api_keys:
            if secrets.compare_digest(key_hash, entry.get("key_hash", "")):
                return entry.get("role", "read")

    # Fall back to legacy single api_key (grants admin)
    # Compare via hash to avoid holding plaintext key in config memory
    if has_single_key:
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        stored_hash = hashlib.sha256(config.settings.api_key.encode()).hexdigest()
        if secrets.compare_digest(key_hash, stored_hash):
            return "admin"

    return None


# =============================================================================
# The per-KB rule
# =============================================================================


def kb_role(
    user_id: int | None,
    user: Mapping[str, Any] | None,
    grant: Callable[[], str | None],
    kb_default_role: str | None,
    anonymous_tier: str | None,
) -> str | None:
    """The effective role on one KB, from the rows `AuthService` fetched.

    `user` is the `local_user` row (`role`, `global_access`) for `user_id`,
    or None when there is no such row; `grant()` returns the user's explicit
    grant on the KB, or None (it is called only when needed, so a global
    admin costs one query). Resolution:

    1. A global admin: "admin".
    2. An explicit grant.
    3. The KB's default_role (not "none"): a self-registered user
       (no ``global_access``) gets read on a public KB and never more.
    4. The user's global role -- only when it covers every KB
       (``global_access``); a `none` KB stays closed without a grant.
    5. Otherwise (the anonymous visitor, `user_id` None, or a `user_id` with
       no user row): the lower of `anonymous_tier` and the KB default_role;
       None for a `none` KB or no anonymous tier. The ceiling is lowered by a
       KB, never raised.
    """
    if user_id is not None:
        if user and user["role"] == "admin":
            return "admin"

        granted = grant()
        if granted is not None:
            return granted

        if kb_default_role is not None and kb_default_role != "none":
            if user and not user["global_access"]:
                return "read"
            return kb_default_role

        if user:
            if kb_default_role == "none":
                return None
            if not user["global_access"]:
                return None
            return user["role"]

    ceiling = anonymous_tier
    if ceiling is None or kb_default_role == "none":
        return None
    if kb_default_role is None:
        return ceiling
    return lower_role(ceiling, kb_default_role)


# =============================================================================
# Principal, Action, Resource, Decision
# =============================================================================

PrincipalKind = Literal["user", "anonymous", "operator_key", "local"]


@dataclass(frozen=True)
class Principal:
    """Who is asking.

    - ``user``: a signed-in user; `role` is their global role.
    - ``anonymous``: the anonymous visitor on an auth-enabled instance;
      `role` is `anonymous_tier`.
    - ``operator_key``: a caller with no user identity -- an operator API
      key, or auth disabled -- whose `role` holds on every KB.
    - ``local``: the operator's own process (CLI, stdio MCP): admin.
    """

    kind: PrincipalKind
    role: str | None
    user_id: int | None = None

    @classmethod
    def local(cls) -> Principal:
        return cls("local", "admin")

    @classmethod
    def from_api_key(cls, role: str | None) -> Principal:
        return cls("operator_key", role)

    @classmethod
    def user(cls, user_id: int, role: str | None) -> Principal:
        return cls("user", role, user_id)

    @classmethod
    def anonymous(cls, tier: str | None) -> Principal:
        return cls("anonymous", tier)

    @property
    def scoped(self) -> bool:
        """Does this principal have an identity per-KB roles apply to?"""
        return self.kind in ("user", "anonymous")

    @property
    def per_kb(self) -> bool:
        """Can the KB change this principal's role? Not for the admin role
        (admin everywhere), nor for a principal with no identity."""
        return self.scoped and self.role != "admin"


class Action(StrEnum):
    """A closed vocabulary of what a principal may ask to do (ADR-0037 §1)."""

    KB_READ = "kb.read"
    KB_WRITE = "kb.write"
    KB_ADMIN = "kb.admin"
    INSTANCE_ADMIN = "instance.admin"
    USER_MANAGE = "user.manage"
    SETTINGS_SECRET = "settings.secret"
    SELF = "self"
    REPO_EGRESS = "repo.egress"

    @classmethod
    def at_tier(cls, tier: str) -> Action:
        """The rung action for a tier name (one of `ROLES`)."""
        return _TIER_ACTIONS[tier]


_TIER_ACTIONS = {"read": Action.KB_READ, "write": Action.KB_WRITE, "admin": Action.KB_ADMIN}
# The rung each action needs. Actions absent here are not decided in this
# theme (their routes migrate in ADR-0037 theme 3c) and are refused loudly.
_ACTION_TIERS = {
    Action.KB_READ: "read",
    Action.KB_WRITE: "write",
    Action.KB_ADMIN: "admin",
    Action.INSTANCE_ADMIN: "admin",
}


@dataclass(frozen=True)
class KB:
    """One KB, named by the request."""

    name: str


@dataclass(frozen=True)
class Row:
    """A row the request names by id; the rule applies to the row's own KB."""

    kb_name: str


@dataclass(frozen=True)
class Instance:
    """No KB: the principal's global role decides."""


@dataclass(frozen=True)
class User:
    """A user account (ADR-0037 theme 3c)."""

    id: int


@dataclass(frozen=True)
class AnyKB:
    """Every KB: the caller filters by `read_scope`."""


Resource = KB | Row | Instance | User | AnyKB

UNAUTHENTICATED = "UNAUTHENTICATED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class Decision:
    """Allow (`code` None), or a denial with its code."""

    code: str | None = None

    @property
    def allowed(self) -> bool:
        return self.code is None


ALLOW = Decision()


def authorize_tier(principal: Principal | None, tier: str) -> Decision:
    """The global-role check, with no KB: is the principal's own role at
    least `tier`? Needs no database, so a surface can ask it before any
    handle is open. `AccessPolicy.authorize(p, action, Instance())` is this.
    """
    if principal is None or principal.role is None:
        return Decision(UNAUTHENTICATED)
    return ALLOW if role_at_least(principal.role, tier) else Decision(FORBIDDEN)


class PolicyDeniedError(Exception):
    """`require`'s refusal for UNAUTHENTICATED and FORBIDDEN.

    A NOT_FOUND raises `KBNotFoundError` itself, so a concealed KB and a
    missing one cannot be told apart. ADR-0037 theme 2 adds the
    `AccessDenied` family to `pyrite.exceptions`; this class folds into it
    when the two land together.
    """

    def __init__(self, decision: Decision, message: str) -> None:
        super().__init__(message)
        self.decision = decision
        self.code = decision.code


#: What an unscoped caller passes as ``readable_kbs`` -- the value
#: ``ReadScope.as_set()`` returns for an unscoped scope. The read methods
#: that span KBs (link, lookup, graph, QA, wikilink, collection-query and
#: task-resolution methods in the services and storage) take ``readable_kbs``
#: as a *required* keyword with no default, so forgetting it is a
#: ``TypeError``, never "see everything"; a caller with no scope to apply
#: (the CLI, an admin or operator path, a lookup inside one already-named KB)
#: writes ``readable_kbs=UNSCOPED`` and says so. It is the one scope value,
#: spelled so the choice is visible at the call site -- not a second type.
UNSCOPED: None = None


@dataclass(frozen=True, init=False)
class ReadScope:
    """The KBs a principal may reach: a set, or unscoped.

    Only the policy makes one (`AccessPolicy.read_scope` / `write_scope`), so
    "unscoped" is a decision, never what a forgotten argument defaults to.
    """

    kbs: frozenset[str] | None

    def __init__(self, kbs: frozenset[str] | None, *, _by_policy: object) -> None:
        if _by_policy is not _POLICY:
            raise TypeError("a scope is made by AccessPolicy, not directly")
        object.__setattr__(self, "kbs", kbs)

    @property
    def unscoped(self) -> bool:
        return self.kbs is None

    def permits(self, kb_name: str) -> bool:
        return self.kbs is None or kb_name in self.kbs

    def as_set(self) -> set[str] | None:
        """A fresh mutable copy (None when unscoped), for callers that take a set."""
        return None if self.kbs is None else set(self.kbs)


class WriteScope(ReadScope):
    """The KBs a principal may write."""


_POLICY = object()


# =============================================================================
# The policy
# =============================================================================


class AccessPolicy:
    """Every access decision, for one config and one database handle."""

    def __init__(
        self,
        config: PyriteConfig,
        db: PyriteDB,
        auth_service: AuthService | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self._auth_service = auth_service
        self._registry = None

    @property
    def auth_service(self) -> AuthService:
        """The identity store the policy reads users and grants through.

        A surface that resolves a credential (a session token) uses this one
        rather than building a second.
        """
        if self._auth_service is None:
            from .auth_service import AuthService

            self._auth_service = AuthService(self.db, self.config.settings.auth)
        return self._auth_service

    def _kb_registry(self):
        if self._registry is None:
            from .kb_registry_service import KBRegistryService

            self._registry = KBRegistryService(self.config, self.db)
        return self._registry

    # -- facts about a KB -----------------------------------------------------

    def kb_exists(self, kb_name: str) -> bool:
        """Is `kb_name` a KB this instance knows -- in config or the registry?"""
        if self.config.get_kb(kb_name):
            return True
        return self._kb_registry().is_registered(kb_name)

    def kb_default_role(self, kb_name: str) -> str | None:
        """A KB's default_role: config first, then the registry.

        Under an untrusted config the index belongs to the tree: a registry
        value that opens a KB is ignored (`PyriteConfig.confined_default_role`).
        """
        kb_config = self.config.get_kb(kb_name)
        if kb_config and kb_config.default_role is not None:
            return kb_config.default_role
        stored = self._kb_registry().registered_default_role(kb_name)
        if stored is None:
            return None
        return self.config.confined_default_role(stored)

    # -- roles -----------------------------------------------------------------

    def user_kb_role(self, user_id: int | None, kb_name: str) -> str | None:
        """The per-KB rule for a user (`user_id` None: the anonymous visitor)."""
        return self.auth_service.get_kb_role(user_id, kb_name, self.kb_default_role(kb_name))

    def effective_kb_role(self, principal: Principal | None, kb_name: str) -> str | None:
        """The principal's role on `kb_name`; None when it has none.

        A principal with the admin role, and one with no identity (an operator
        key, auth disabled, the local process), has its role on every KB; a
        user or the anonymous visitor walks the per-KB rule.
        """
        if principal is None or principal.role is None:
            return None
        if not principal.per_kb:
            return principal.role
        return self.user_kb_role(principal.user_id, kb_name)

    def kbs_at_tier(self, principal: Principal, tier: str) -> set[str] | None:
        """The KBs where the principal's effective role is at least `tier`, or
        None when it is not scoped (the admin role, no identity)."""
        if not principal.per_kb:
            return None
        result: set[str] = set()
        for kb in self.config.all_kbs():
            if role_at_least(self.user_kb_role(principal.user_id, kb.name), tier):
                result.add(kb.name)
        return result

    def read_scope(self, principal: Principal) -> ReadScope:
        kbs = self.kbs_at_tier(principal, "read")
        return ReadScope(None if kbs is None else frozenset(kbs), _by_policy=_POLICY)

    def write_scope(self, principal: Principal) -> WriteScope:
        kbs = self.kbs_at_tier(principal, "write")
        return WriteScope(None if kbs is None else frozenset(kbs), _by_policy=_POLICY)

    # -- decisions -------------------------------------------------------------

    def authorize(
        self, principal: Principal | None, action: Action, resource: Resource
    ) -> Decision:
        """Allow, or deny with UNAUTHENTICATED, FORBIDDEN or NOT_FOUND.

        For a KB or a row's KB, existence comes before role (§4): NOT_FOUND
        when the KB does not exist *or* the principal cannot read it -- the
        two must answer alike, or the answer is an oracle for private KB
        names -- and FORBIDDEN when it can read it but not at `action`'s rung.
        """
        tier = _ACTION_TIERS.get(action)
        if tier is None:
            raise NotImplementedError(f"{action} is not decided by the policy yet (ADR-0037 3c)")
        if principal is None or principal.role is None:
            return Decision(UNAUTHENTICATED)

        if isinstance(resource, KB | Row):
            kb_name = resource.name if isinstance(resource, KB) else resource.kb_name
            if not self.kb_exists(kb_name):
                return Decision(NOT_FOUND)
            effective = self.effective_kb_role(principal, kb_name)
            if role_at_least(effective, tier):
                return ALLOW
            if not role_at_least(effective, "read"):
                return Decision(NOT_FOUND)
            return Decision(FORBIDDEN)

        if isinstance(resource, Instance):
            return authorize_tier(principal, tier)

        if isinstance(resource, AnyKB):
            # The scope does the narrowing; any authenticated principal may ask.
            return ALLOW

        raise NotImplementedError(f"{resource!r} is not decided by the policy yet (ADR-0037 3c)")

    def require(self, principal: Principal | None, action: Action, resource: Resource) -> None:
        """`authorize`, raising on a denial: `KBNotFoundError` for NOT_FOUND
        (the same exception, and message, a missing KB gets), `PolicyDeniedError`
        otherwise."""
        decision = self.authorize(principal, action, resource)
        if decision.allowed:
            return
        if decision.code == NOT_FOUND:
            kb_name = (
                resource.name if isinstance(resource, KB) else getattr(resource, "kb_name", "")
            )
            raise KBNotFoundError(f"KB '{kb_name}' not found")
        raise PolicyDeniedError(decision, f"{action} refused: {decision.code}")
