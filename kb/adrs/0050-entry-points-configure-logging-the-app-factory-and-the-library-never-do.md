---
id: adr-0050
title: "Entry points configure logging; the app factory and the library never do"
type: adr
importance: 3
adr_number: 50
status: accepted
date: '2026-10-15'
tags: [architecture, logging, server, deployment]
links:
- target: adr-0046
  relation: related
  kb: pyrite
- target: adr-0037
  relation: related
  kb: pyrite
---

# ADR-0050: Entry points configure logging; the app factory and the library never do

> **Proposed** (2026-10-15) for #199. The maintainer accepts or rejects it.

## Context

#196 and #584 settled most of this, in code but not in a decision:

- `import pyrite` adds only a `NullHandler` (`pyrite/__init__.py:10`).
- Each command-line entry point calls `configure_entry_point_logging`
  (`pyrite/logging.py`) once: `pyrite` (WARNING, INFO for `serve`),
  `pyrite-read`, `pyrite-admin`, and `pyrite-server` (INFO,
  `pyrite/server/api.py:1513`). It installs one stderr handler on the
  `pyrite` logger with `propagate = False`.
- `create_app()` configures nothing.
- uvicorn's default `dictConfig` touches only the `uvicorn.*` loggers and
  leaves the `pyrite` handler in place (checked).

That leaves one gap. `uvicorn pyrite.server.api:app`, which the README names
as a way to run the server (`README.md:337`), runs no entry point. A
`pyrite.*` record then reaches only the `NullHandler`, so Python's last-resort
handler never fires. Checked: a WARNING on `pyrite.server.errors` under
uvicorn's default logging config prints nothing. That logger is the one REST
uses for an unhandled exception (`pyrite/server/errors.py:169`) and for the
masked cause of a `public_message` refusal (`public_text`, #848). On that
path, a 500's cause is lost.

## Decision

1. **The process's entry point decides logging, once.** Pyrite's entry
   points call `configure_entry_point_logging`. Library code, `import
   pyrite`, and `create_app()` never add a handler or set a level.
2. **Everything Pyrite logs is under the `pyrite` logger.** A host that embeds
   the app with `create_app()` configures `pyrite` (or the root logger, which
   `pyrite` propagates to when no entry point ran) like any library's
   logger.
3. **`pyrite.server.api:app` is an entry point.** The module-level `app`
   becomes lazy (a module `__getattr__`). The first time it is read, it calls
   `configure_entry_point_logging(default="INFO")` and then `create_app()`.
   So `uvicorn pyrite.server.api:app` and `pyrite-server` log the same way,
   and importing `create_app` no longer builds an app as a side effect. To
   take over logging, an operator runs `create_app` through uvicorn's
   `--factory` flag (`uvicorn --factory pyrite.server.api:create_app
   --log-config ...`).
4. **Format is the entry point's to choose.** A later
   `PYRITE_LOG_FORMAT=json` belongs in `configure_entry_point_logging`, and in
   nothing else.

## Questions for the maintainer

- **Q1. Lazy `app` or a documented gap?** Recommendation: lazy `app`
  (decision 3). It is about 15 lines, it fixes a lost-500-cause path, and it
  removes an import side effect. Counter-case: keep `app` and document that
  whoever runs `uvicorn ...:app` must pass `--log-config`. That changes no
  code, but every operator who follows the README loses the logs.

## Alternatives considered

- **`create_app()` configures when nobody has.** Rejected. "Nobody has" cannot
  be detected reliably, and an embedding host would get Pyrite's handler on
  its process.
- **Configure the root logger.** Rejected. Under uvicorn it would print every
  line twice, and it reconfigures a host.

## Consequences

- One small theme: the lazy `app`, a test that a `pyrite.*` warning reaches
  stderr under `uvicorn pyrite.server.api:app`, a test that `create_app()`
  leaves logging alone, and a README note.

## Decided

Accepted by the maintainer (Mark), 2026-10-15, through the desk.
