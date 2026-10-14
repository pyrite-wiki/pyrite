"""Search the markdown files of KBs directly, without the index.

The fallback behind ``pyrite search --files`` and the CLI's recovery when the
index search fails. A substring match over file text; it never touches the
database.
"""

import logging
from dataclasses import dataclass, field

from ..config import KBConfig
from ..storage.repository import KBRepository

logger = logging.getLogger(__name__)


@dataclass
class FileSearchResult:
    """``hits`` are (kb_name, entry, path); ``scanned``/``unreadable`` count files."""

    hits: list = field(default_factory=list)
    scanned: int = 0
    unreadable: int = 0


def search_kb_files(
    kbs: list[KBConfig], query: str, entry_type: str | None, limit: int
) -> FileSearchResult:
    """Scan ``kbs`` in order for ``query`` (case-insensitive), stopping at ``limit`` hits."""
    out = FileSearchResult()
    needle = query.lower()
    for kb in kbs:
        if not kb.path.exists():
            continue

        repo = KBRepository(kb)
        for md_file in repo.list_files():
            try:
                content = md_file.read_text(encoding="utf-8")
                out.scanned += 1
                if needle in content.lower():
                    entry = repo._load_entry(md_file)

                    if entry_type and entry.entry_type != entry_type:
                        continue

                    out.hits.append((kb.name, entry, md_file))

                    if len(out.hits) >= limit:
                        break
            except Exception:
                logger.warning("Search failed for KB %s", kb.name, exc_info=True)
                out.unreadable += 1
                continue

        if len(out.hits) >= limit:
            break
    return out
