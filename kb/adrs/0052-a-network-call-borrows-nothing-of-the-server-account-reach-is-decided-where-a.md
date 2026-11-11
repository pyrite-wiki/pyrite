---
id: adr-0052
title: A network call borrows nothing of the server account; reach is decided where a resource is opened
type: adr
importance: 5
adr_number: 52
status: proposed
date: "2026-11-10"
---

# ADR-0052: A network call borrows nothing of the server account; reach is decided where a resource is opened

## Context

The server answers network calls in the operator's process. ADR-0043 decides
who may act on which entry; it does not decide what of the server account a
network call may draw on. Pyrite was built for one operator working locally
(design.md, "What Pyrite is"), where everything the process can reach really
is the caller's, and ADR-0043 §2 says so: "the operating system account and
the filesystem are the boundary for the local process." The HTTP server put a
second kind of caller into the same process.

Each resource channel kept the local default and started from everything the
server account can reach, then subtracted at the call site:

- `Path.read_text` reads any file;
- `git_hardening.git_env` copies the whole environment and removes a list;
- `str(exc)` is shown unless something masks it;
- a `PyriteError` class counts as "safe to show" whatever text its instance
  carries, so a raise that interpolates a caught exception launders outside
  text into a Pyrite message.

A guard a call site opts into can only be found, or missed, by grep. 0.26.2
found four channels, each over several review rounds, and each round found
the property one layer further out than the last.

## Decision

1. **One fact.** `acting.is_local_process()` is the reach. The surface's root
   binds it (CLI, stdio MCP). It is False by default and never derived from a
   role (cf. ADR-0043 P14).
2. **The property.** A network call reaches the entries of the KBs it holds a
   grant on, and nothing else the server account holds:
   - **N1 Files.** Bytes, and any fact derived from bytes (an id, existence, a
     hint, a key name, a hash), come only from files that `KBFiles` reads
     inside a KB root.
   - **N2 Environment.** A program started for a network call gets an
     environment built from an allow-list, not the server's environment minus
     a list. No credential store of the account can be reached: global or
     system git config, helpers, askpass, netrc, the ssh agent and identities,
     provider variables. The exception is a credential the operator
     configured explicitly for network git (a token or key named in Pyrite's
     config, scoped to a host or KB). That is a grant, not a borrowing: it is
     the only credential a network git command receives, and it never comes
     from the account's ambient stores (helpers, agent, netrc, global
     config). Per-user credentials are a later step for multi-user.
   - **N3 Text.** A response carries only text Pyrite wrote for a caller. Text
     from outside Pyrite (an OS error, a library, a provider, git's stderr,
     file content) belongs to the operator. It is logged, never shown to a
     network call, and never placed inside a Pyrite message.
   - **N4 Answers.** No answer to a network call (content, code, not-found
     versus refused, a count) differs by what a resource outside N1-N3 holds.
3. **Enforced at the source, deny by default, one point each.**
   - N1: `KBFiles`. Nothing that answers a caller has a raw read mode, and the
     `inside=` parameters go.
   - N2: `git_hardening.git_env` (and one `subprocess_env` for any other
     program) builds the environment from the reach itself; no caller passes
     isolation in.
   - N3: text is decided where a message is built (`public_text` for a whole
     message, `outside_text` for a cause named inside a Pyrite message), with
     the fault builders (`server/errors.error_response`, MCP
     `_protocol_refusal`, CLI `cli_error_from`) as the backstop for an
     exception that escapes. An endpoint never builds a body from an
     exception. The next step marks outside text where it is produced rather
     than where it is caught: a subprocess result whose stdout and stderr can
     only be logged, with a ratchet on every read of them, built with N2's
     `subprocess_env`.
   - N4: the locators read only through N1.
4. **Each point has a test that fails on a bypass**, an AST ratchet modelled
   on `tests/test_entry_file_writers_are_one_function.py`, with a planted
   violation for every shape:
   - N1: `tests/test_kb_reads_are_one_function.py`.
   - N2: the isolation case added to
     `tests/test_kb_repo_git_commands_are_hardened.py`.
   - N3: `tests/test_response_text_is_pyrites.py`.

   Then one behavioural canary test, `tests/test_network_calls_borrow_nothing.py`,
   parametrised from `app.routes` (`/api`) and the MCP tool table. It runs in a
   world with a canary in every channel and fails if any response carries a
   canary, or if two guesses get different answers.
5. **The local operator keeps their own reach.** The CLI shows fault text,
   uses the operator's credentials and reads what it is pointed at.

## Consequences

- N3 lands first (0.26.2): its ratchet enumerates the sites, so the work is
  driven by a test's list rather than a grep. Its behavioural partner for
  REST is `tests/test_rest_bodies_carry_no_fault_text.py`; the full canary
  test of decision 4 is still owed.
- N1 and N2 are refactors of `KBFiles` and the id locators, and of `git_env`;
  they follow in 0.26.3. Until then a few raw reads remain that are safe only
  because of who calls them, ssh identities and the agent are open to a
  network git command, and the git environment is the server's environment
  minus a list.
- A fault a network caller sees is less specific: the detail is in the server
  log, which the operator reads. This includes detail about the caller's own
  input (a query's parse error, a YAML line in an upload). Whether text the
  caller supplied, or text from a KB the caller holds a grant on, may be
  shown is open, and is decided with N1 and N4.
- What 0.26.2's N3 does not yet hold:
  - Git output that a function returns rather than raises (push, pull, merge)
    reaches a network caller with paths and credentials removed by a deny
    list, not by this rule.
  - The ratchet reads `except` bodies only. Text passed to a helper, appended
    to a list, set on an attribute or taken from a traceback is held by review,
    not by the test.
  - `OSError.strerror` is treated as fixed system text.

## Limits

These stay as `kb_files.py` states them (off POSIX, root swap). The
repository's own local config is still read. This ADR does not claim Pyrite
is a service for users who do not trust each other (design.md, "What Pyrite
is not").
Until N2 lands in 0.26.3, the ssh agent and identities remain reachable to a
network git command.
