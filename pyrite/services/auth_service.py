"""
Authentication Service for Web UI

Provides local username/password authentication with session tokens.
Uses bcrypt for password hashing and SHA-256 hashed opaque tokens for sessions.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt as _bcrypt
from sqlalchemy.exc import IntegrityError

from ..config import AuthConfig, OAuthProviderConfig
from ..exceptions import LastAdminError, RefusedValueError
from ..services.oauth_providers import OAuthProfile
from ..storage.database import PyriteDB
from .access_policy import ROLES, kb_role, role_at_least, role_level
from .credential_events import CredentialChange, publish

logger = logging.getLogger(__name__)

#: Where an operator creates the first admin. Registration and OAuth sign-up
#: stay closed until one exists.
BOOTSTRAP_HINT = "pyrite-admin user create <username> --role admin"

VALID_ROLES = ROLES

# A real bcrypt hash of a random password, checked against when a login names
# no user, so an unknown username costs the same time as a wrong password.
_DUMMY_PASSWORD_HASH = _bcrypt.hashpw(secrets.token_bytes(16), _bcrypt.gensalt()).decode()


class RegistrationClosedError(RefusedValueError):
    """Sign-up refused because the instance has no admin yet."""

    def __init__(self) -> None:
        super().__init__(
            "Registration is closed until an administrator exists. "
            f"The operator creates the first one with: {BOOTSTRAP_HINT}"
        )


class AuthService:
    """Manages local user authentication and session tokens."""

    def __init__(self, db: PyriteDB, auth_config: AuthConfig):
        self.db = db
        self.config = auth_config

    # ── Invite codes ──────────────────────────────────────────────

    def create_invite_code(
        self, created_by: str, role: str = "write", note: str = "", expires_hours: int | None = None
    ) -> dict:
        """Generate a new invite code. Only admins should call this."""
        code = secrets.token_urlsafe(16)
        now = datetime.now(UTC).isoformat()
        expires_at = None
        if expires_hours:
            expires_at = (datetime.now(UTC) + timedelta(hours=expires_hours)).isoformat()
        self.db.execute_write_sql(
            """INSERT INTO invite_code (code, created_by, created_at, expires_at, role, note)
            VALUES (:code, :created_by, :now, :expires_at, :role, :note)""",
            {
                "code": code,
                "created_by": created_by,
                "now": now,
                "expires_at": expires_at,
                "role": role,
                "note": note,
            },
        )
        return {"code": code, "role": role, "expires_at": expires_at, "note": note}

    def list_invite_codes(self) -> list[dict]:
        """List all invite codes with usage status."""
        return self.db.execute_sql(
            "SELECT code, created_by, created_at, expires_at, used_by, used_at, role, note FROM invite_code ORDER BY created_at DESC"
        )

    def validate_invite_code(self, code: str) -> dict | None:
        """Validate an invite code. Returns code info or None if invalid."""
        rows = self.db.execute_sql("SELECT * FROM invite_code WHERE code = :code", {"code": code})
        if not rows:
            return None
        row = rows[0]
        if row.get("used_by"):
            return None  # Already used
        if row.get("expires_at"):
            try:
                expires = datetime.fromisoformat(row["expires_at"])
                if datetime.now(UTC) > expires:
                    return None  # Expired
            except (ValueError, TypeError):
                pass
        return row

    def _redeem_invite_code(self, code: str, username: str) -> str:
        """Claim an invite code for ``username`` and return its role.

        Must run inside the caller's write transaction (it does not commit),
        before the user is inserted. The claim is one conditional UPDATE: of
        any number of registrations presenting one code at once, only the one
        whose UPDATE changed the row gets it; the rest see rowcount 0 and are
        refused. Raises ValueError when the code is unknown, used or expired.
        """
        now = datetime.now(UTC).isoformat()
        claimed = self.db.execute_write_sql(
            "UPDATE invite_code SET used_by = :username, used_at = :now "
            "WHERE code = :code AND used_by IS NULL "
            "AND (expires_at IS NULL OR expires_at > :now)",
            {"username": username, "now": now, "code": code},
            commit=False,
        )
        if claimed != 1:
            raise RefusedValueError("Invalid or expired invite code")
        rows = self.db.execute_sql(
            "SELECT role FROM invite_code WHERE code = :code", {"code": code}
        )
        return rows[0]["role"] if rows and rows[0]["role"] else "read"

    def delete_invite_code(self, code: str) -> bool:
        """Delete an unused invite code."""
        rows = self.db.execute_sql(
            "SELECT used_by FROM invite_code WHERE code = :code", {"code": code}
        )
        if not rows:
            return False
        if rows[0].get("used_by"):
            raise RefusedValueError("Cannot delete a used invite code")
        self.db.execute_write_sql("DELETE FROM invite_code WHERE code = :code", {"code": code})
        return True

    # ── OAuth CSRF state ──────────────────────────────────────────
    #
    # DB-backed (oauth_state table, migration v22) rather than an
    # in-memory dict, so state survives a process restart between the
    # redirect-to-provider and callback legs of the OAuth flow, and works
    # correctly if the hosted instance ever runs multiple replicas behind
    # a load balancer (oauth-state-store-persistence).

    _OAUTH_STATE_TTL_SECONDS = 300  # 5 minutes, matches the prior in-memory TTL

    @staticmethod
    def _oauth_state_key(state: str, binding: str) -> str:
        """The row key for a state bound to a browser.

        Only this digest is stored: the database holds neither the public
        ``state`` (it travels through the provider) nor the ``binding``
        (it lives in an HttpOnly cookie), so a row can be found only by a
        request that carries both.
        """
        return hashlib.sha256(f"{state}\x00{binding}".encode()).hexdigest()

    def create_oauth_state(
        self,
        flow: str = "login",
        user_id: int | None = None,
        ttl_seconds: int | None = None,
    ) -> tuple[str, str]:
        """Generate a CSRF state bound to the requesting browser.

        Returns ``(state, binding)``: ``state`` goes to the provider in the
        authorize URL; ``binding`` must be set by the caller in a
        short-lived HttpOnly cookie on the browser that started the flow,
        and handed back to ``verify_oauth_state`` on the callback.

        ``ttl_seconds`` defaults to 5 minutes; pass a negative value in
        tests to create an already-expired token.
        """
        # Probabilistic cleanup (1 in 10 calls) — same pattern as session
        # cleanup in verify_session, avoids a dedicated background task.
        if secrets.randbelow(10) == 0:
            self.cleanup_expired_oauth_states()

        ttl = self._OAUTH_STATE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=ttl)).isoformat()
        self.db.execute_write_sql(
            """INSERT INTO oauth_state (state, flow, user_id, created_at, expires_at)
            VALUES (:key, :flow, :user_id, :now, :expires_at)""",
            {
                "key": self._oauth_state_key(state, binding),
                "flow": flow,
                "user_id": user_id,
                "now": now.isoformat(),
                "expires_at": expires_at,
            },
        )
        return state, binding

    def verify_oauth_state(self, state: str, binding: str) -> dict | None:
        """Verify and consume a browser-bound CSRF state. Returns
        ``{"flow": ..., "user_id": ...}`` or ``None`` if the pair is
        unknown (wrong or missing binding included), already consumed, or
        expired.

        Single-use and race-free: the DELETE decides. Of two callbacks
        presenting the same pair at once, only the one whose DELETE removed
        the row succeeds. A mismatched binding finds no row and consumes
        nothing, so it cannot burn the legitimate browser's flow.
        """
        key = self._oauth_state_key(state, binding)
        rows = self.db.execute_sql(
            "SELECT flow, user_id, expires_at FROM oauth_state WHERE state = :key",
            {"key": key},
        )
        if not rows:
            return None
        deleted = self.db.execute_write_sql(
            "DELETE FROM oauth_state WHERE state = :key", {"key": key}
        )
        if deleted != 1:
            return None
        row = rows[0]
        if datetime.now(UTC).isoformat() >= row["expires_at"]:
            return None
        return {"flow": row["flow"], "user_id": row["user_id"]}

    def cleanup_expired_oauth_states(self) -> int:
        """Delete all expired OAuth state rows. Returns the count removed."""
        now = datetime.now(UTC).isoformat()
        return self.db.execute_write_sql(
            "DELETE FROM oauth_state WHERE expires_at < :now", {"now": now}
        )

    # ── Registration ──────────────────────────────────────────────

    def admin_exists(self) -> bool:
        """Whether any user holds the global admin role."""
        return bool(
            self.db.execute_sql("SELECT 1 AS one FROM local_user WHERE role = 'admin' LIMIT 1")
        )

    @staticmethod
    def _check_credentials(username: str, password: str) -> None:
        if not username or not password:
            raise RefusedValueError("Username and password are required")
        if len(password) < 8:
            raise RefusedValueError("Password must be at least 8 characters")

    def _insert_local_user(
        self,
        username: str,
        password_hash: str,
        role: str,
        display_name: str | None,
        global_access: bool,
    ) -> int:
        """Insert a local user inside the caller's transaction; return its id.

        A taken username is refused by the table's UNIQUE constraint, so two
        registrations racing for one name cannot both succeed.
        """
        now = datetime.now(UTC).isoformat()
        try:
            rows = self.db.execute_sql(
                """INSERT INTO local_user
                (username, display_name, password_hash, role, global_access, auth_provider,
                 created_at, updated_at)
                VALUES (:username, :display_name, :password_hash, :role, :global_access, 'local',
                 :now, :now2)
                RETURNING id""",
                {
                    "username": username,
                    "display_name": display_name,
                    "password_hash": password_hash,
                    "role": role,
                    "global_access": 1 if global_access else 0,
                    "now": now,
                    "now2": now,
                },
            )
        except IntegrityError:
            raise RefusedValueError("Username already taken") from None
        return rows[0]["id"]

    def _create_local_user(
        self,
        username: str,
        password: str,
        role: str,
        display_name: str | None,
        *,
        global_access: bool,
        invite_code: str | None = None,
    ) -> dict:
        """Hash, then claim the invite (if any) and insert the user as one
        write transaction. The invite UPDATE comes first so the transaction
        opens with a write and holds SQLite's write lock from there on.

        An invite's role was chosen by the admin who made the code, so an
        invited user's role covers every KB (``global_access``)."""
        password_hash = self._hash_password(password)  # slow: outside the lock
        session = self.db.session
        try:
            if invite_code is not None:
                role = self._redeem_invite_code(invite_code, username)
                global_access = True
            user_id = self._insert_local_user(
                username, password_hash, role, display_name, global_access
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        return {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "role": role,
            "auth_provider": "local",
            "avatar_url": None,
        }

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "read",
        display_name: str | None = None,
    ) -> dict:
        """Create a local user with ``role``: the operator's path (the CLI).

        Unlike :meth:`register` it ignores ``allow_registration`` and does not
        need an existing admin -- it is how the first admin is made.
        """
        if role not in VALID_ROLES:
            raise RefusedValueError(
                f"Invalid role: {role} (expected one of {', '.join(VALID_ROLES)})"
            )
        self._check_credentials(username, password)
        return self._create_local_user(username, password, role, display_name, global_access=True)

    def register(
        self,
        username: str,
        password: str,
        display_name: str | None = None,
        invite_code: str | None = None,
    ) -> dict:
        """Self-register a local user (the web sign-up path).

        Refused until an admin exists (:class:`RegistrationClosedError`); the
        first admin comes from :meth:`create_user`, never from being first.
        With an invite code the user gets its role, across every KB. Without
        one they get ``read`` on public KBs only -- a KB whose ``default_role``
        is set -- plus whatever an admin grants (``global_access`` 0).
        Raises ValueError if username is taken, registration is disabled, or
        invite code is required but invalid.
        """
        if not self.config.allow_registration:
            raise RefusedValueError("Registration is disabled")
        if not self.admin_exists():
            raise RegistrationClosedError()

        if self.config.require_invite_code and not invite_code:
            raise RefusedValueError("An invite code is required to register")

        self._check_credentials(username, password)

        return self._create_local_user(
            username,
            password,
            "read",
            display_name,
            global_access=False,
            invite_code=invite_code if self.config.require_invite_code else None,
        )

    def login(self, username: str, password: str) -> tuple[dict, str]:
        """Authenticate user and create session.

        Returns (user_dict, raw_token). Raises ValueError on bad credentials.
        """
        rows = self.db.execute_sql(
            "SELECT id, username, display_name, password_hash, role, auth_provider, avatar_url"
            " FROM local_user WHERE username = :username",
            {"username": username},
        )
        if not rows:
            # Pay for a password check anyway, so the response time does not
            # say whether the username exists.
            self._verify_password(password, _DUMMY_PASSWORD_HASH)
            raise RefusedValueError("Invalid username or password")

        row = rows[0]
        user_id = row["id"]
        uname = row["username"]
        display_name = row["display_name"]
        password_hash = row["password_hash"]
        role = row["role"]
        auth_provider = row["auth_provider"]
        avatar_url = row["avatar_url"]

        # Block password login for OAuth-only users
        if auth_provider != "local":
            self._verify_password(password, _DUMMY_PASSWORD_HASH)  # same cost as a miss
            raise RefusedValueError("This account uses external authentication")

        if not self._verify_password(password, password_hash):
            raise RefusedValueError("Invalid username or password")

        raw_token = self._create_session(user_id)

        user = {
            "id": user_id,
            "username": uname,
            "display_name": display_name,
            "role": role,
            "auth_provider": auth_provider,
            "avatar_url": avatar_url,
        }
        return user, raw_token

    def oauth_login(
        self, profile: OAuthProfile, provider_config: OAuthProviderConfig
    ) -> tuple[dict, str]:
        """Create or update an OAuth user and return (user_dict, raw_token).

        Raises ValueError if the user's orgs don't match allowed_orgs.
        """
        # 1. Check org restrictions
        if provider_config.allowed_orgs:
            if not set(profile.orgs) & set(provider_config.allowed_orgs):
                raise RefusedValueError(
                    "Your GitHub account is not a member of an allowed organization"
                )

        # 2. Determine role from org_tier_map or default_tier
        role = provider_config.default_tier
        mapped_by_org = False
        if provider_config.org_tier_map:
            for org in profile.orgs:
                mapped = provider_config.org_tier_map.get(org)
                if mapped:
                    mapped_by_org = True
                if mapped and role_level(mapped) > role_level(role):
                    role = mapped
        # An operator vetted this sign-up when it had to come from an allowed
        # org or matched an org the operator mapped to a role; only then does
        # the role cover KBs without a default_role. An open sign-up reads
        # public KBs only, like a web registration.
        global_access = bool(provider_config.allowed_orgs) or mapped_by_org
        vetted = global_access

        # 3. Look up existing OAuth user
        rows = self.db.execute_sql(
            "SELECT id, username, display_name, role, avatar_url"
            " FROM local_user WHERE auth_provider = :provider AND provider_id = :provider_id",
            {"provider": profile.provider, "provider_id": profile.provider_id},
        )

        now = datetime.now(UTC).isoformat()

        if rows:
            row = rows[0]
            user_id = row["id"]
            username = row["username"]
            display_name = row["display_name"]
            existing_role = row["role"]
            # Update avatar and display name
            self.db.execute_write_sql(
                "UPDATE local_user SET avatar_url = :avatar_url, display_name = :display_name,"
                " updated_at = :now WHERE id = :user_id",
                {
                    "avatar_url": profile.avatar_url,
                    "display_name": profile.display_name or display_name,
                    "now": now,
                    "user_id": user_id,
                },
            )
            role = existing_role  # preserve existing role
        else:
            # 4. New user. An unvetted sign-up obeys the same switches as web
            # registration: closed registration, or one that needs an invite
            # code (which OAuth cannot carry), creates no account.
            if not vetted and (
                not self.config.allow_registration or self.config.require_invite_code
            ):
                raise RefusedValueError(
                    "Registration is closed: this GitHub account is not in an organization "
                    "the operator allows"
                )
            username = profile.username
            conflict = self.db.execute_sql(
                "SELECT id FROM local_user WHERE username = :username",
                {"username": username},
            )
            if conflict:
                username = f"{profile.provider}:{profile.username}"

            # Sign-up waits for an operator-created admin, and nobody becomes
            # admin by being first.
            if not self.admin_exists():
                raise RegistrationClosedError()

            self.db.execute_write_sql(
                """INSERT INTO local_user
                (username, display_name, password_hash, role, global_access, auth_provider,
                 provider_id, avatar_url, created_at, updated_at)
                VALUES (:username, :display_name, '', :role, :global_access, :provider,
                 :provider_id, :avatar_url, :now, :now2)""",
                {
                    "username": username,
                    "display_name": profile.display_name,
                    "role": role,
                    "global_access": 1 if global_access else 0,
                    "provider": profile.provider,
                    "provider_id": profile.provider_id,
                    "avatar_url": profile.avatar_url,
                    "now": now,
                    "now2": now,
                },
            )
            id_rows = self.db.execute_sql(
                "SELECT id FROM local_user WHERE username = :username",
                {"username": username},
            )
            user_id = id_rows[0]["id"]

        # 5. Create session
        raw_token = self._create_session(user_id)

        user = {
            "id": user_id,
            "username": username,
            "display_name": profile.display_name,
            "role": role,
            "auth_provider": profile.provider,
            "avatar_url": profile.avatar_url,
        }
        return user, raw_token

    def verify_session(self, token: str) -> dict | None:
        """Look up session by SHA-256(token).

        Returns user dict or None if expired/invalid. Updates last_used.
        """
        found = self.verify_session_detail(token)
        return found[0] if found else None

    def verify_session_detail(self, token: str) -> tuple[dict, dict] | None:
        """``verify_session``, plus the session itself: ``(user, session)``.

        ``session`` is ``{"token_hash", "expires_at"}`` (``expires_at`` a
        timezone-aware ``datetime``). A live-update socket records both at
        its handshake so it can be closed when that session ends (ADR-0036).
        Kept out of the user dict, which endpoints return to clients.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        # Probabilistic cleanup (1 in 20 calls)
        if secrets.randbelow(20) == 0:
            self._cleanup_expired()

        rows = self.db.execute_sql(
            """SELECT s.id, s.user_id, s.expires_at,
                      u.username, u.display_name, u.role, u.auth_provider, u.avatar_url
            FROM session s JOIN local_user u ON s.user_id = u.id
            WHERE s.token_hash = :token_hash""",
            {"token_hash": token_hash},
        )

        if not rows:
            return None

        row = rows[0]
        session_id = row["id"]
        user_id = row["user_id"]
        expires_at = row["expires_at"]
        username = row["username"]
        display_name = row["display_name"]
        role = row["role"]
        auth_provider = row["auth_provider"]
        avatar_url = row["avatar_url"]

        # Check expiry
        expiry = datetime.fromisoformat(expires_at)
        if expiry < datetime.now(UTC):
            self.db.execute_write_sql(
                "DELETE FROM session WHERE id = :session_id",
                {"session_id": session_id},
            )
            publish(CredentialChange(session_hash=token_hash))
            return None

        # Update last_used
        self.db.execute_write_sql(
            "UPDATE session SET last_used = :last_used WHERE id = :session_id",
            {"last_used": datetime.now(UTC).isoformat(), "session_id": session_id},
        )

        user = {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "role": role,
            "auth_provider": auth_provider,
            "avatar_url": avatar_url,
        }
        return user, {"token_hash": token_hash, "expires_at": expiry}

    # Every method that ends a session or changes what a user may read --
    # logout, expiry and eviction, role changes, KB grants and revokes, and
    # the admin grant `create_user_ephemeral_kb` writes -- publishes a
    # `CredentialChange` after its write, so open live-update sockets opened
    # with that credential are closed (#411, ADR-0036).

    def logout(self, token: str) -> bool:
        """Delete session by token. Returns True if found."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        rowcount = self.db.execute_write_sql(
            "DELETE FROM session WHERE token_hash = :token_hash",
            {"token_hash": token_hash},
        )
        if rowcount > 0:
            publish(CredentialChange(session_hash=token_hash))
        return rowcount > 0

    def logout_all(self, user_id: int) -> int:
        """Delete all sessions for a user. Returns count deleted."""
        deleted = self.db.execute_write_sql(
            "DELETE FROM session WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        if deleted:
            publish(CredentialChange(user_id=user_id))
        return deleted

    def get_user(self, user_id: int) -> dict | None:
        """Get user by ID."""
        rows = self.db.execute_sql(
            "SELECT id, username, display_name, role, auth_provider, avatar_url, usage_tier"
            " FROM local_user WHERE id = :user_id",
            {"user_id": user_id},
        )
        if not rows:
            return None
        return rows[0]

    def set_role(self, user_id: int, role: str, global_access: bool | None = None) -> bool:
        """Set user role. Returns True if user found.

        ``global_access`` says whether the role also covers KBs without a
        default_role. None (the default) keeps what the user has: changing a
        self-registered user's role never widens their access by itself; only
        an explicit admin grant (True) does, and False takes it away.

        Raises:
            ValueError: `role` is not read, write or admin.
            LastAdminError: the change would demote the last global admin,
                leaving nobody able to manage users. The count and the update
                are one statement, so two admins demoting each other at once
                cannot both succeed.
        """
        if role not in ROLES:
            raise RefusedValueError(f"Invalid role: {role}")
        # Atomic only because the auth tables live in SQLite: SQLite
        # serializes writers, so the COUNT(*) subquery and the UPDATE it
        # gates always see a consistent snapshot within this one statement.
        # If the auth tables ever move to a database with a weaker default
        # isolation level (e.g. Postgres READ COMMITTED), this single
        # statement is no longer enough -- two concurrent demotions could
        # both read "more than one admin" before either commits. It would
        # need `SELECT ... FOR UPDATE` on the admin rows, or an advisory
        # lock, around the check-and-update.
        rowcount = self.db.execute_write_sql(
            "UPDATE local_user SET role = :role, "
            "global_access = COALESCE(:global_access, global_access), updated_at = :now "
            "WHERE id = :user_id AND ("
            "  :role = 'admin' OR role != 'admin'"
            "  OR (SELECT COUNT(*) FROM local_user WHERE role = 'admin') > 1"
            ")",
            {
                "role": role,
                "global_access": None if global_access is None else int(bool(global_access)),
                "now": datetime.now(UTC).isoformat(),
                "user_id": user_id,
            },
        )
        if rowcount > 0:
            # Publish on every role write, even an unchanged one: a separate
            # read to skip that case races a concurrent change and can miss a
            # downgrade (#433 delta cold read). An extra reconnect is the cost.
            publish(CredentialChange(user_id=user_id))
            return True
        exists = self.db.execute_sql(
            "SELECT 1 FROM local_user WHERE id = :user_id", {"user_id": user_id}
        )
        if exists:
            raise LastAdminError("Cannot demote the last admin: promote another user first")
        return False

    def set_usage_tier(self, user_id: int, usage_tier: str) -> bool:
        """Set a user's usage tier (free/pro/enterprise — a resource/
        billing axis, distinct from `role`). Returns True if user found.

        Unlike set_role, the tier name isn't validated against a fixed
        enum here — it's matched against config.settings.auth.usage_tiers
        at check time (QuotaService), and an admin may configure
        arbitrary tier names there.
        """
        rowcount = self.db.execute_write_sql(
            "UPDATE local_user SET usage_tier = :usage_tier, updated_at = :now WHERE id = :user_id",
            {"usage_tier": usage_tier, "now": datetime.now(UTC).isoformat(), "user_id": user_id},
        )
        return rowcount > 0

    def _cleanup_expired(self) -> int:
        """Delete expired sessions."""
        now = datetime.now(UTC).isoformat()
        expired = self.db.execute_sql(
            "SELECT token_hash FROM session WHERE expires_at < :now", {"now": now}
        )
        deleted = self.db.execute_write_sql(
            "DELETE FROM session WHERE expires_at < :now", {"now": now}
        )
        if deleted:
            logger.debug("Cleaned up %d expired sessions", deleted)
        # A session that expired between the two statements is deleted but
        # not announced; its socket's own recorded expiry still closes it.
        for row in expired:
            publish(CredentialChange(session_hash=row["token_hash"]))
        return deleted

    def _create_session(self, user_id: int) -> str:
        """Insert a new session for ``user_id``, evicting the oldest beyond the
        cap, and return its raw token.

        Property: at every commit point the user holds at most
        ``max_sessions_per_user`` sessions, however many logins -- password,
        OAuth or both -- run at once (#435). The insert and the eviction are
        one write transaction, and the transaction opens with the INSERT, a
        write: SQLite takes the database's single write lock at that
        statement, so the eviction that follows sees every session committed
        before it and none can be committed beside it. (A leading SELECT would
        not do: a deferred transaction's reads take no write lock, so two
        logins could read the same count.)

        Evicted sessions are announced after the commit, so a listener never
        acts on an eviction that was rolled back (#433).

        On failure, ``rollback()`` discards everything pending on the
        caller's session -- not only this method's own INSERT and eviction.
        The session is scope-owned (see ``PyriteDB.session``), so on the
        server that is only this request's own uncommitted work; a caller
        sharing a longer-lived session (the CLI, a background job) loses
        whatever else it had queued there too (#440).
        """
        raw_token, token_hash = self._generate_token()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=self.config.session_ttl_hours)
        session = self.db.session
        try:
            self.db.execute_write_sql(
                """INSERT INTO session (token_hash, user_id, created_at, expires_at, last_used)
                VALUES (:token_hash, :user_id, :created_at, :expires_at, :last_used)""",
                {
                    "token_hash": token_hash,
                    "user_id": user_id,
                    "created_at": now.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "last_used": now.isoformat(),
                },
                commit=False,
            )
            evicted = self._enforce_max_sessions(user_id, keep=token_hash)
            session.commit()
        except Exception:
            session.rollback()
            raise
        for hash_ in evicted:
            publish(CredentialChange(session_hash=hash_))
        return raw_token

    def _enforce_max_sessions(self, user_id: int, keep: str) -> list[str]:
        """Delete the user's sessions beyond the cap; return their hashes.

        Runs inside :meth:`_create_session`'s write transaction, which commits
        it. One statement, so the eviction is atomic and counts the rows it
        acts on rather than a count read earlier; RETURNING names the evicted
        sessions so their sockets can be closed. The session being created
        (``keep``) is ranked first, so a login never evicts the session it is
        about to return, whatever the clocks say; the rest go oldest first.
        A cap below one still leaves the new session.
        """
        rows = self.db.execute_sql(
            """DELETE FROM session WHERE id IN (
                SELECT id FROM session WHERE user_id = :user_id
                ORDER BY token_hash = :keep DESC, created_at DESC, id DESC
                LIMIT -1 OFFSET :cap
            ) RETURNING token_hash""",
            {"user_id": user_id, "keep": keep, "cap": max(self.config.max_sessions_per_user, 1)},
        )
        return [row["token_hash"] for row in rows]

    def _hash_password(self, password: str) -> str:
        """Hash password with bcrypt."""
        return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

    def _verify_password(self, password: str, pw_hash: str) -> bool:
        """Verify password against bcrypt hash."""
        return _bcrypt.checkpw(password.encode(), pw_hash.encode())

    def _generate_token(self) -> tuple[str, str]:
        """Generate a session token. Returns (raw_token, sha256_hash)."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        return raw_token, token_hash

    # =====================================================================
    # Per-KB Permissions
    # =====================================================================

    def get_kb_role(
        self, user_id: int | None, kb_name: str, kb_default_role: str | None = None
    ) -> str | None:
        """Resolve effective role for a user on a KB.

        Fetches the user's row and (only if needed) their grant on the KB, and
        asks the access policy's per-KB rule (`access_policy.kb_role`), which
        documents the resolution chain: global admin, explicit grant, KB
        default_role (a self-registered user capped at read), the user's
        global role only with ``global_access``, and for the anonymous
        visitor (user_id None) the lower of anonymous_tier and the KB
        default_role. The read side, REST writes, /ws and MCP all resolve
        here, so the rule holds on every surface.
        """
        user = None
        if user_id is not None:
            rows = self.db.execute_sql(
                "SELECT role, global_access FROM local_user WHERE id = :user_id",
                {"user_id": user_id},
            )
            user = rows[0] if rows else None

        def grant() -> str | None:
            perm_rows = self.db.execute_sql(
                "SELECT role FROM kb_permission WHERE user_id = :user_id AND kb_name = :kb_name",
                {"user_id": user_id, "kb_name": kb_name},
            )
            return perm_rows[0]["role"] if perm_rows else None

        return kb_role(user_id, user, grant, kb_default_role, self.config.anonymous_tier)

    def grant_kb_permission(self, user_id: int, kb_name: str, role: str, granted_by: int) -> None:
        """Grant or update a per-KB permission."""
        if role not in ROLES:
            raise RefusedValueError(f"Invalid role: {role}")
        now = datetime.now(UTC).isoformat()
        self.db.execute_write_sql(
            """INSERT INTO kb_permission (user_id, kb_name, role, granted_by, created_at)
            VALUES (:user_id, :kb_name, :role, :granted_by, :now)
            ON CONFLICT(user_id, kb_name) DO UPDATE SET role = :role2, granted_by = :granted_by2, created_at = :now2""",
            {
                "user_id": user_id,
                "kb_name": kb_name,
                "role": role,
                "granted_by": granted_by,
                "now": now,
                "role2": role,
                "granted_by2": granted_by,
                "now2": now,
            },
        )
        publish(CredentialChange(user_id=user_id))

    def revoke_kb_permission(self, user_id: int, kb_name: str) -> bool:
        """Revoke a per-KB permission. Returns True if found."""
        rowcount = self.db.execute_write_sql(
            "DELETE FROM kb_permission WHERE user_id = :user_id AND kb_name = :kb_name",
            {"user_id": user_id, "kb_name": kb_name},
        )
        if rowcount > 0:
            publish(CredentialChange(user_id=user_id))
        return rowcount > 0

    def list_kb_permissions(self, kb_name: str) -> list[dict]:
        """List all permission grants for a KB."""
        return self.db.execute_sql(
            """SELECT kp.user_id, u.username, kp.role, kp.granted_by, kp.created_at
            FROM kb_permission kp
            JOIN local_user u ON kp.user_id = u.id
            WHERE kp.kb_name = :kb_name
            ORDER BY kp.created_at""",
            {"kb_name": kb_name},
        )

    def list_users(self) -> list[dict]:
        """List all local users (excluding password hashes).

        ``global_access`` says whether the user's role covers KBs without a
        default_role; a self-registered user has False until an admin grants it.
        """
        rows = self.db.execute_sql(
            "SELECT id, username, display_name, role, global_access, auth_provider, avatar_url "
            "FROM local_user ORDER BY username"
        )
        for row in rows:
            row["global_access"] = bool(row["global_access"])
        return rows

    def get_user_kb_permissions(self, user_id: int) -> dict[str, str]:
        """Get all explicit KB grants for a user. Returns {kb_name: role}."""
        rows = self.db.execute_sql(
            "SELECT kb_name, role FROM kb_permission WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        return {r["kb_name"]: r["role"] for r in rows}

    # =====================================================================
    # GitHub Token Management
    # =====================================================================

    @staticmethod
    def _get_encryption_key() -> bytes | None:
        """Get encryption key from environment. Returns None if not configured."""
        import base64
        import hashlib
        import os

        raw_key = os.environ.get("PYRITE_ENCRYPTION_KEY")
        if not raw_key:
            return None
        # Derive a 32-byte Fernet-compatible key from the raw secret
        key_bytes = hashlib.sha256(raw_key.encode()).digest()
        return base64.urlsafe_b64encode(key_bytes)

    @staticmethod
    def _encrypt_token(token: str, key: bytes) -> str:
        """Encrypt a token string using Fernet symmetric encryption."""
        from cryptography.fernet import Fernet

        f = Fernet(key)
        return f.encrypt(token.encode()).decode()

    @staticmethod
    def _decrypt_token(encrypted: str, key: bytes) -> str:
        """Decrypt a Fernet-encrypted token string."""
        from cryptography.fernet import Fernet

        f = Fernet(key)
        return f.decrypt(encrypted.encode()).decode()

    def store_github_token(self, user_id: int, token: str, scopes: str = "public_repo") -> None:
        """Store a GitHub access token for a user. Encrypts if PYRITE_ENCRYPTION_KEY is set."""
        key = self._get_encryption_key()
        stored_value = self._encrypt_token(token, key) if key else token

        self.db.execute_write_sql(
            "UPDATE local_user SET github_access_token = :token, github_token_scopes = :scopes,"
            " updated_at = :now WHERE id = :user_id",
            {
                "token": stored_value,
                "scopes": scopes,
                "now": datetime.now(UTC).isoformat(),
                "user_id": user_id,
            },
        )

    def get_github_token_for_user(self, user_id: int) -> tuple[str | None, str | None]:
        """Get GitHub token and scopes for a user. Decrypts if encrypted. Returns (token, scopes)."""
        rows = self.db.execute_sql(
            "SELECT github_access_token, github_token_scopes FROM local_user WHERE id = :user_id",
            {"user_id": user_id},
        )
        if not rows or not rows[0]["github_access_token"]:
            scopes = rows[0]["github_token_scopes"] if rows else None
            return None, scopes

        raw_token = rows[0]["github_access_token"]
        scopes = rows[0]["github_token_scopes"]
        key = self._get_encryption_key()
        if key:
            try:
                return self._decrypt_token(raw_token, key), scopes
            except Exception:
                # DECIDED 2026-07-03 (fail-open-exception-sweep site #5):
                # fail closed. An undecryptable value here is either
                # corrupted ciphertext / a rotated key, or a legacy
                # pre-encryption plaintext row -- Fernet.decrypt raises
                # InvalidToken for both, indistinguishably. Returning the
                # raw bytes as if they were a valid token means silently
                # authenticating with corrupt-key material. Treat it as
                # no token: the caller re-auths via GitHub connect, which
                # re-stores (and properly encrypts) a fresh token -- a
                # forced reconnect is the migration path for legacy rows.
                logger.warning(
                    "GitHub token for user %s could not be decrypted; "
                    "treating as absent (user must reconnect)",
                    user_id,
                    exc_info=True,
                )
                return None, scopes
        return raw_token, scopes

    def clear_github_token(self, user_id: int) -> bool:
        """Remove stored GitHub token. Returns True if user found."""
        rowcount = self.db.execute_write_sql(
            "UPDATE local_user SET github_access_token = NULL, github_token_scopes = NULL,"
            " updated_at = :now WHERE id = :user_id",
            {"now": datetime.now(UTC).isoformat(), "user_id": user_id},
        )
        return rowcount > 0

    # =====================================================================
    # Per-User API Key Management (BYOK)
    # =====================================================================

    def store_user_api_key(
        self, user_id: int, provider: str, api_key: str, model: str = ""
    ) -> dict:
        """Store or update a user's API key (encrypted) for an LLM provider."""
        key = self._get_encryption_key()
        stored_value = self._encrypt_token(api_key, key) if key else api_key
        now = datetime.now(UTC).isoformat()

        self.db.execute_write_sql(
            """INSERT INTO user_api_key (user_id, provider, encrypted_key, model, created_at, updated_at)
            VALUES (:user_id, :provider, :encrypted_key, :model, :now, :now2)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                encrypted_key = :encrypted_key2, model = :model2, updated_at = :now3""",
            {
                "user_id": user_id,
                "provider": provider,
                "encrypted_key": stored_value,
                "model": model,
                "now": now,
                "now2": now,
                "encrypted_key2": stored_value,
                "model2": model,
                "now3": now,
            },
        )
        return {"provider": provider, "model": model, "stored": True}

    def get_user_api_key(self, user_id: int, provider: str = "") -> dict | None:
        """Get a user's API key (decrypted). If provider not specified, return the first one found."""
        if provider:
            rows = self.db.execute_sql(
                "SELECT provider, encrypted_key, model FROM user_api_key"
                " WHERE user_id = :user_id AND provider = :provider",
                {"user_id": user_id, "provider": provider},
            )
        else:
            rows = self.db.execute_sql(
                "SELECT provider, encrypted_key, model FROM user_api_key"
                " WHERE user_id = :user_id ORDER BY updated_at DESC LIMIT 1",
                {"user_id": user_id},
            )
        if not rows:
            return None

        row = rows[0]
        raw_key = row["encrypted_key"]
        enc_key = self._get_encryption_key()
        if enc_key:
            try:
                decrypted = self._decrypt_token(raw_key, enc_key)
            except Exception:
                # DECIDED 2026-07-03 (fail-open-exception-sweep site #5):
                # fail closed, same rationale as get_github_token_for_user.
                # Never hand back undecryptable bytes as if they were a
                # valid API key -- the caller must re-store the key
                # (forced re-entry is the migration path for legacy rows).
                logger.warning(
                    "API key for user %s provider %s could not be decrypted; "
                    "treating as absent (user must re-enter key)",
                    user_id,
                    row["provider"],
                    exc_info=True,
                )
                return None
        else:
            decrypted = raw_key

        return {
            "provider": row["provider"],
            "api_key": decrypted,
            "model": row["model"] or "",
        }

    def delete_user_api_key(self, user_id: int, provider: str) -> bool:
        """Delete a user's API key for a specific provider."""
        rowcount = self.db.execute_write_sql(
            "DELETE FROM user_api_key WHERE user_id = :user_id AND provider = :provider",
            {"user_id": user_id, "provider": provider},
        )
        return rowcount > 0

    def list_user_api_keys(self, user_id: int) -> list[dict]:
        """List a user's configured providers (without exposing keys)."""
        return self.db.execute_sql(
            "SELECT provider, model, created_at FROM user_api_key"
            " WHERE user_id = :user_id ORDER BY created_at",
            {"user_id": user_id},
        )

    def create_user_ephemeral_kb(
        self, user_id: int, ephemeral_service, name: str | None = None
    ) -> dict:
        """Create an ephemeral KB for a user with per-KB admin grant.

        Checks ephemeral_min_tier, ephemeral_max_per_user limits.
        Raises ValueError on policy violation.
        """
        # Check user tier against ephemeral_min_tier
        rows = self.db.execute_sql(
            "SELECT role, ephemeral_kb_count FROM local_user WHERE id = :user_id",
            {"user_id": user_id},
        )
        if not rows:
            raise RefusedValueError("User not found")

        user_role = rows[0]["role"]
        current_count = rows[0]["ephemeral_kb_count"] or 0

        min_tier = self.config.ephemeral_min_tier
        if not role_at_least(user_role, min_tier):
            raise RefusedValueError(
                f"Insufficient tier: requires '{min_tier}', your role is '{user_role}'"
            )

        # Check per-user limit
        if current_count >= self.config.ephemeral_max_per_user:
            raise RefusedValueError(
                f"Ephemeral KB limit reached ({self.config.ephemeral_max_per_user})"
            )

        # Generate name if not provided
        if not name:
            name = f"ephemeral-{user_id}-{secrets.token_hex(4)}"

        ttl = self.config.ephemeral_default_ttl
        # Private from the moment it exists: create_ephemeral_kb persists
        # default_role "none" with the KB (registry row and config.yaml).
        kb = ephemeral_service.create_ephemeral_kb(
            name, ttl=ttl, description=f"Ephemeral KB for user {user_id}"
        )

        # The creator's admin grant and the per-user count commit together.
        # If either fails, the KB goes too, and with it any grant row for it
        # (force_expire_kb -> db.unregister_kb deletes the KB's grants).
        now = datetime.now(UTC).isoformat()
        try:
            self.db.execute_write_sql(
                """INSERT INTO kb_permission (user_id, kb_name, role, granted_by, created_at)
                VALUES (:user_id, :kb_name, 'admin', :granted_by, :now)""",
                {"user_id": user_id, "kb_name": name, "granted_by": user_id, "now": now},
                commit=False,
            )
            self.db.execute_write_sql(
                "UPDATE local_user SET ephemeral_kb_count = ephemeral_kb_count + 1"
                " WHERE id = :user_id",
                {"user_id": user_id},
            )
        except BaseException:
            ephemeral_service.force_expire_kb(name)
            raise
        publish(CredentialChange(user_id=user_id))

        return {
            "name": kb.name,
            "path": str(kb.path),
            "ephemeral": True,
            "ttl": ttl,
        }
