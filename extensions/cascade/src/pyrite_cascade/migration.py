"""Migration scripts for Cascade Series KB import.

Run once on copied files to normalize frontmatter before Pyrite indexing.

These scripts read and write with ``read_text``/``write_text``, so a file
they change comes out with LF line endings (a CRLF file is converted); a BOM
is kept.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyrite.utils.frontmatter import Frontmatter, NoFrontmatter, describe, split_frontmatter

if TYPE_CHECKING:
    from pyrite.storage.database import PyriteDB

# Folder prefixes to strip from wikilinks in research KB
_WIKILINK_PREFIXES = (
    "actors",
    "organizations",
    "events",
    "themes",
    "scenes",
    "victims",
    "statistics",
    "mechanisms",
    "sources",
    "capture-lanes",
    "research-notes",
)

_WIKILINK_PREFIX_RE = re.compile(
    r"\[\[(" + "|".join(re.escape(p) for p in _WIKILINK_PREFIXES) + r")/",
)


def _frontmatter_or_warn(md_file: Path, content: str) -> Frontmatter | None:
    """The file's frontmatter, or None. A file with none is skipped quietly; one
    that opens frontmatter it cannot close is skipped with a warning."""
    split = split_frontmatter(content)
    if isinstance(split, Frontmatter):
        return split
    if not isinstance(split, NoFrontmatter):
        print(f"WARNING: skipped {md_file}: {describe(split)}")
    return None


def inject_ids(kb_path: str | Path) -> dict[str, str]:
    """Add id: <filename-stem> to research KB files that lack an id field.

    Returns dict mapping filename to injected ID for reporting.
    """
    kb_path = Path(kb_path)
    injected: dict[str, str] = {}
    seen_ids: dict[str, Path] = {}
    collisions: list[str] = []

    for md_file in sorted(kb_path.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        stem = md_file.stem
        content = md_file.read_text(encoding="utf-8")

        # Check for ID collisions
        if stem in seen_ids:
            collisions.append(f"ID collision: '{stem}' in {seen_ids[stem]} and {md_file}")
        seen_ids[stem] = md_file

        m = _frontmatter_or_warn(md_file, content)
        if m is None:
            continue

        fm_block = m.text

        # Skip if already has an id field
        if re.search(r"^id:\s", fm_block, re.MULTILINE):
            continue

        # Inject id after the opening ---
        new_content = (
            content[: m.yaml_start]
            + f"id: {stem}\n"
            + fm_block
            + content[m.close_start : m.close_end]
            + content[m.close_end :]
        )
        md_file.write_text(new_content, encoding="utf-8")
        injected[str(md_file)] = stem

    if collisions:
        for c in collisions:
            print(f"WARNING: {c}")

    return injected


def normalize_wikilinks(kb_path: str | Path) -> int:
    """Strip folder prefixes from wikilinks in research KB files.

    [[actors/powell-lewis]] → [[powell-lewis]]
    [[organizations/ALEC]] → [[ALEC]]

    Returns count of substitutions made.
    """
    kb_path = Path(kb_path)
    total_subs = 0

    for md_file in sorted(kb_path.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        content = md_file.read_text(encoding="utf-8")
        new_content, n = _WIKILINK_PREFIX_RE.subn("[[", content)
        if n > 0:
            md_file.write_text(new_content, encoding="utf-8")
            total_subs += n

    return total_subs


def normalize_research_frontmatter(kb_path: str | Path) -> dict[str, int]:
    """Normalize frontmatter in research KB files.

    - essay_type: mechanism → type: mechanism
    - event_date: X → date: X (for events)
    - type: organization → type: cascade_org
    - Normalize research_status values
    - Normalize era values (strip quotes)

    Returns counts of each transformation.
    """
    kb_path = Path(kb_path)
    counts: dict[str, int] = {
        "essay_type_to_type": 0,
        "event_date_to_date": 0,
        "org_to_cascade_org": 0,
        "research_status_normalized": 0,
    }

    # Valid research_status values and their mappings
    status_map = {
        "stub": "stub",
        "in-progress": "in-progress",
        "in_progress": "in-progress",
        "complete": "complete",
        "comprehensive": "comprehensive",
        "active": "in-progress",
    }

    for md_file in sorted(kb_path.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        content = md_file.read_text(encoding="utf-8")
        m = _frontmatter_or_warn(md_file, content)
        if m is None:
            continue

        fm = m.text
        body = content[m.close_end :]
        changed = False

        # essay_type → type
        new_fm, n = re.subn(r"^essay_type:\s*", "type: ", fm, flags=re.MULTILINE)
        if n:
            fm = new_fm
            changed = True
            counts["essay_type_to_type"] += n

        # event_date → date (only if no date: field already exists)
        if re.search(r"^event_date:", fm, re.MULTILINE) and not re.search(
            r"^date:", fm, re.MULTILINE
        ):
            new_fm, n = re.subn(r"^event_date:", "date:", fm, flags=re.MULTILINE)
            if n:
                fm = new_fm
                changed = True
                counts["event_date_to_date"] += n

        # type: organization → type: cascade_org
        new_fm, n = re.subn(
            r"^type:\s*organization\s*$", "type: cascade_org", fm, flags=re.MULTILINE
        )
        if n:
            fm = new_fm
            changed = True
            counts["org_to_cascade_org"] += n

        # Normalize research_status (strip quotes, map synonyms)
        rs_match = re.search(r'^(research_status:\s*)(["\']?)([^"\'\n]+)\2\s*$', fm, re.MULTILINE)
        if rs_match:
            raw = rs_match.group(3).strip().lower()
            normalized = status_map.get(raw, raw)
            has_quotes = bool(rs_match.group(2))
            value_changed = normalized != raw
            if has_quotes or value_changed:
                fm = (
                    fm[: rs_match.start()] + f"research_status: {normalized}" + fm[rs_match.end() :]
                )
                changed = True
                counts["research_status_normalized"] += 1

        if changed:
            new_content = content[: m.yaml_start] + fm + content[m.close_start : m.close_end] + body
            md_file.write_text(new_content, encoding="utf-8")

    return counts


def normalize_timeline_frontmatter(kb_path: str | Path) -> dict[str, int]:
    """Normalize frontmatter in timeline KB files.

    - Add type: timeline_event to all files
    - Strip quotes from date values (YAML-safe already)

    Uses fast regex — no full YAML parse for 4,144 files.
    Returns counts of transformations.
    """
    kb_path = Path(kb_path)
    counts: dict[str, int] = {
        "type_added": 0,
        "date_unquoted": 0,
    }

    for md_file in sorted(kb_path.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        content = md_file.read_text(encoding="utf-8")
        m = _frontmatter_or_warn(md_file, content)
        if m is None:
            continue

        fm = m.text
        body = content[m.close_end :]
        changed = False

        # Add type: timeline_event if missing
        if not re.search(r"^type:", fm, re.MULTILINE):
            fm = "type: timeline_event\n" + fm
            changed = True
            counts["type_added"] += 1

        # Unquote date values: date: '2024-01-15' → date: 2024-01-15
        new_fm, n = re.subn(
            r"^(date:\s*)['\"](\d{4}-\d{2}-\d{2})['\"]",
            r"\g<1>\2",
            fm,
            flags=re.MULTILINE,
        )
        if n:
            fm = new_fm
            changed = True
            counts["date_unquoted"] += n

        if changed:
            new_content = content[: m.yaml_start] + fm + content[m.close_start : m.close_end] + body
            md_file.write_text(new_content, encoding="utf-8")

    return counts


# ---------------------------------------------------------------------------
# JI compatibility audit and backfill
# ---------------------------------------------------------------------------

_JI_DEFAULTS: dict[str, Any] = {
    "source_refs": [],
    "verification_status": "unverified",
}


def audit_ji_compat(db: PyriteDB, kb_name: str) -> dict[str, Any]:
    """Scan all timeline_event entries and report JI field coverage.

    Returns a dict with counts:
      total, with_source_refs, with_verification_status,
      fully_compatible, needs_backfill
    """
    entries = db.list_entries(kb_name=kb_name, entry_type="timeline_event", limit=100_000)

    total = len(entries)
    with_source_refs = 0
    with_verification_status = 0

    for entry in entries:
        meta = entry.get("metadata", {}) or {}
        if "source_refs" in meta:
            with_source_refs += 1
        if "verification_status" in meta:
            with_verification_status += 1

    fully_compatible = sum(
        1
        for e in entries
        if "source_refs" in (e.get("metadata") or {})
        and "verification_status" in (e.get("metadata") or {})
    )

    return {
        "total": total,
        "with_source_refs": with_source_refs,
        "with_verification_status": with_verification_status,
        "fully_compatible": fully_compatible,
        "needs_backfill": total - fully_compatible,
    }


def backfill_ji_fields(db: PyriteDB, kb_name: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Backfill JI default fields on timeline_event entries that lack them.

    For entries missing ``source_refs`` or ``verification_status`` in their
    metadata, this sets the canonical defaults (``[]`` and ``"unverified"``
    respectively).  The data already loads correctly without these — this
    just makes the stored metadata explicit.

    Returns ``{"updated": int, "skipped": int, "dry_run": bool}``.
    """
    entries = db.list_entries(kb_name=kb_name, entry_type="timeline_event", limit=100_000)

    updated = 0
    skipped = 0

    for entry in entries:
        meta = entry.get("metadata", {}) or {}
        needs_update = False

        for field in _JI_DEFAULTS:
            if field not in meta:
                needs_update = True
                break

        if not needs_update:
            skipped += 1
            continue

        if not dry_run:
            new_meta = dict(meta)
            for field, default in _JI_DEFAULTS.items():
                if field not in new_meta:
                    new_meta[field] = default

            entry_data = dict(entry)
            entry_data["metadata"] = new_meta
            db.upsert_entry(entry_data)

        updated += 1

    return {"updated": updated, "skipped": skipped, "dry_run": dry_run}
