---
id: adr-0036
type: adr
title: "A live-update socket lives no longer than the credential that opened it"
adr_number: 36
status: accepted
date: 2026-09-25
---

# ADR-0036: A live-update socket lives no longer than the credential that opened it

> **Lags the code (2026-10-03).** Consequences say KB-wide access changes are
> "not yet covered". KB-policy epochs now exist
> (`announce_kb_policy_change` in `pyrite/services/credential_events.py`), so they
> are covered. The decision is unchanged. Evidence: `kb/designs/adr-audit-2026-10.md`.

## Context

`/ws` pushes live-update events (entry created, updated, deleted; KB synced) to
the web UI. Since #218/#323 every socket is authenticated at the handshake and
carries the set of KBs its owner may read; `ConnectionManager.broadcast` sends an
event naming a KB only to sockets that may read it. The backlog item
`authenticate-and-scope-ws-218` and the module docstring of
`pyrite/server/websocket.py` fixed one further rule: the readable set is resolved
**once, at connect, and fixed for the life of the connection**. Re-resolving per
event would put a database walk per socket per event on the event loop.

That rule has a consequence #411 names: a client that keeps its socket open keeps
receiving events scoped to a credential that no longer holds. After logout,
session expiry, a role downgrade or a revoked KB grant, the old socket still
receives entry ids and KB names from KBs the user can no longer read. #336 fixed
the well-behaved browser (it reopens its socket when the signed-in identity
changes); a client that does not cooperate was untouched. No ADR recorded the
fixed-for-life rule. ADR-0031 §3 (frontends are scoped clients) is the nearest
host, but it is about what a frontend may see, not how long a transport lives.

The credential changes in the service layer (`AuthService`); the sockets live in
the server layer. `AuthService` must not import `pyrite.server`. Several changes
also happen with no endpoint at all: `logout_all`, eviction of the oldest session
by `_enforce_max_sessions`, and the deletion of expired sessions by
`_cleanup_expired` and `verify_session`.

## Decision

**A live-update socket lives no longer than the credential that opened it.** The
readable set is still resolved once, at connect. When the session that opened a
socket ends, or the user's role or KB grants change, the server closes that
user's affected sockets. A client that wants live updates reconnects and is
scoped afresh.

The guarantee covers **per-user credential changes made in the server process**:
after one, no event reaches a socket opened with the old credential. It does not
yet cover KB-wide access changes or changes made from another process; those
are tracked separately.

1. **Each socket records its credential.** `resolve_socket_scope` returns a
   `SocketScope`: the readable set, and for a session-authenticated socket the
   user id, the session's token hash and its `expires_at`
   (`AuthService.verify_session_detail`, through `mcp_routes._resolve_credential`).
   Operator-key, auth-disabled and anonymous sockets carry no revocable
   credential and are never closed by this mechanism.

2. **The service layer publishes; the server layer listens.** A small listener
   registry, `pyrite/services/credential_events.py`, carries a
   `CredentialChange` naming either one session (`session_hash`) or one user
   (`user_id`). `AuthService` publishes after each write that ends a session or
   changes what a user may read:

   | Change | Published as |
   |---|---|
   | `logout` | the session |
   | session found expired by `verify_session`, or swept by `_cleanup_expired` | the session |
   | session evicted by `_enforce_max_sessions` | the session |
   | `logout_all` | the user |
   | `set_role` (any change that took effect) | the user |
   | `grant_kb_permission`, `revoke_kb_permission`, the creator's admin grant in `create_user_ephemeral_kb` | the user |

   The app subscribes `websocket.on_credential_change` at startup. The listener
   runs on the thread that made the change and hands the change to the server's
   event loop (the one captured at startup, #326), in order with any event
   broadcast after it. On the loop, `ConnectionManager.revoke` removes the
   matching sockets from the manager at once and then sends each a close frame
   (code 1008, Policy Violation, the code a refused handshake uses). A listener
   that raises is logged and skipped: a broken listener never fails the write.

   Closing on *any* role or grant change, an upgrade included, keeps one rule
   and costs a reconnect; the new socket carries the new scope. Setting a user
   to the role they already hold is not a change and closes nothing.

3. **Expiry needs no event.** `broadcast` skips and closes a socket whose
   recorded expiry has passed, before sending, so an expired socket receives
   nothing even if no sweep has run. A periodic sweep (`expiry_sweep`, every 60
   seconds, started at startup and cancelled at shutdown) makes the close
   prompt on a quiet server.

4. **A change during the handshake is not lost.** The handshake reads the
   manager's revocation epoch before it resolves the credential; `connect`
   compares it after `accept` and, if any change was processed in between,
   closes a revocable socket instead of registering it. The comparison and the
   registration have no `await` between them.

The alternative, explicit calls from each endpoint to the socket manager, was
rejected: it misses every change made inside the service with no endpoint
(`logout_all`, eviction, expiry cleanup), and makes each new endpoint that
changes a credential responsible for remembering the sockets.

## Consequences

- The #218/#323 rule "fixed for the life of the connection" is superseded: the
  readable set is still fixed, but the connection's life is bounded by its
  credential. A client sees a close and reconnects (the web client already
  reconnects after any close of a socket that had opened, #336).
- `AuthService` gains one import, of a module with no dependencies, and one
  `publish` call per credential-changing write. Nothing in `pyrite/services`
  imports `pyrite/server`.
- Revocation is precise per session or per user. The handshake check is
  coarser: any change processed during a handshake closes that (revocable) new
  socket, which then reconnects. Changes are rare; the cost is one reconnect.
- **In-process only.** A change made by another process against the same
  database (a CLI command, a second server worker) reaches no socket in this
  one. The recorded expiry still bounds a session socket's life; a role or
  grant change from another process takes effect on the next reconnect.
  Cross-process delivery is a follow-up if Pyrite ever runs more than one
  server process per database.
- **Not yet covered, tracked separately:** KB-wide access changes. The
  mechanism extends to them by publishing a change that names a KB rather than
  a user.
- **API-key sockets are not revocable.** A socket opened with an operator API
  key (header or `api_key` query parameter) carries no user or session, so no
  `CredentialChange` names it. Keys come from configuration, and rotating a
  key takes effect on sockets only when the server restarts.
- **An expired tab retries until the client notices.** A browser tab whose
  session expired has its socket closed; its reconnect is then refused at the
  handshake, and it keeps retrying with the backoff until the web client
  notices the expiry and follows the new identity (#420).
- Pinned by `tests/test_websocket_credential_lifetime.py`, which drives a real
  WebSocket client for each trigger.

## References

- #411 (this decision), #336 (the web client follows its identity), #218 and
  #323 (authenticated, scoped sockets), #326 (the loop captured at startup)
- Backlog item `authenticate-and-scope-ws-218`
- ADR-0031 §3 (frontends are scoped clients)
