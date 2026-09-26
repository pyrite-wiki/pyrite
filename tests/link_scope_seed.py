"""A world with links across a readable and a private KB (P-R4, P-R5).

Shared by the link-scoping tests on every surface: storage, the services,
REST and MCP. Built on the ADR-0037 characterization world, so the KBs and
principals are the ones the goldens use, plus a handful of entries whose
links cross from one KB into the other:

- ``private-spy`` (PRIVATE) links to the readable note, so the readable
  note has a backlink from a KB most callers cannot read.
- ``readable-pointer`` (READABLE) links to the private note and to a
  missing entry in the private KB: the two outlinks must read the same.
- ``readable-a`` -> ``readable-b``: an edge wholly inside the readable KB,
  which a scoped caller must still see.
- ``shadowed-id`` exists in PRIVATE and in READ_ONLY (readable), and
  PRIVATE comes first in config order, so a lookup without a KB must skip
  the private twin and find the readable one.
"""

from __future__ import annotations

from pyrite.services.kb_service import KBService
from tests.characterization.world import (
    PRIVATE,
    PRIVATE_ENTRY,
    READ_ONLY,
    READABLE,
    READABLE_ENTRY,
)

__all__ = [
    "MISSING_TARGET",
    "PRIVATE",
    "PRIVATE_ENTRY",
    "PRIVATE_SPY",
    "PRIVATE_SPY_TITLE",
    "READ_ONLY",
    "READABLE",
    "READABLE_ENTRY",
    "READABLE_POINTER",
    "SHADOWED",
    "seed_links",
]

PRIVATE_SPY = "private-spy"
PRIVATE_SPY_TITLE = "Private spy dossier"
READABLE_POINTER = "readable-pointer"
MISSING_TARGET = "no-such-entry"
SHADOWED = "shadowed-id"


def seed_links(world) -> None:
    svc = KBService(world.config, world.db)
    svc.create_entry(
        PRIVATE,
        PRIVATE_SPY,
        PRIVATE_SPY_TITLE,
        "note",
        f"watching [[{READABLE}:{READABLE_ENTRY}]]\n\n## Secret heading\n\nthe code is 1234\n",
    )
    svc.create_entry(
        READABLE,
        READABLE_POINTER,
        "Readable pointer",
        "note",
        f"see [[{PRIVATE}:{PRIVATE_ENTRY}]] and [[{PRIVATE}:{MISSING_TARGET}]]",
    )
    svc.create_entry(READABLE, "readable-a", "Readable A", "note", "to [[readable-b]]")
    svc.create_entry(READABLE, "readable-b", "Readable B", "note", "plain")
    svc.create_entry(PRIVATE, SHADOWED, "Private twin", "note", "private twin body")
    # READ_ONLY refuses KBService writes by design; written to disk and
    # synced, the way the characterization world seeds it.
    (world.tmpdir / READ_ONLY / f"{SHADOWED}.md").write_text(
        f"---\nid: {SHADOWED}\ntype: note\ntitle: Readable twin\n---\n\nreadable twin body\n"
    )
    world.index_worker.submit_sync(READ_ONLY)
    world.index_worker.wait_for_idle(timeout=10)
