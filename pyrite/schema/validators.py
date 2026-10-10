"""Validation utilities for KB schema fields."""

import re
from datetime import datetime
from typing import Any


def validate_date(date_str: str) -> bool:
    """Validate date string format (YYYY-MM-DD)."""
    if not date_str:
        return False
    pattern = r"^\d{4}-\d{2}-\d{2}$"
    if not re.match(pattern, date_str):
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validate_importance(importance: Any) -> bool:
    """Validate importance is 1-10."""
    try:
        val = int(importance)
        return 1 <= val <= 10
    except (ValueError, TypeError):
        return False


def validate_event_id(event_id: str) -> bool:
    """Validate event ID format (YYYY-MM-DD--slug)."""
    pattern = r"^\d{4}-\d{2}-\d{2}--[a-z0-9-]+$"
    return bool(re.match(pattern, event_id))


def generate_event_id(date: str, title: str) -> str:
    """Generate event ID from date and title."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
    return f"{date}--{slug}"


_ID_MAX_LEN = 80


def generate_entry_id(title: Any) -> str:
    """Generate an entry id (and therefore a filename stem) from a title.

    Always non-empty, always matches ``^[a-z0-9][a-z0-9-]{0,79}$``:

    - accents transliterate (``Café résumé`` -> ``cafe-resume``) instead of
      vanishing;
    - a title with nothing Latin left (Japanese, emoji, punctuation only) gets
      a short id derived from a hash of the title, stable across calls, rather
      than an empty id and a confusing "Entry must have an ID";
    - the slug is capped at 80 characters, cut at a word boundary, so the
      filename always fits.
    - a non-string YAML scalar title (``title: 1e3``, ``title: 2024``,
      ``title: true``) is stringified before slugging (#704), so the file
      is indexed instead of silently skipped; a collection title (list/dict,
      #711) is stringified the same way so the loader can report it
      instead of crashing -- ``Entry.validate()`` still flags it;

    Plain ASCII titles produce exactly what they always did, so existing ids
    do not change. Every place that turns a title into a filename must use
    this function (the ``sw new-adr`` CLI and MCP tool once let ``/`` and
    ``:`` through).
    """
    import hashlib
    import unicodedata

    if title is None:
        title_str = ""
    elif isinstance(title, str):
        title_str = title
    else:
        title_str = str(title)
    text = unicodedata.normalize("NFKD", title_str or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > _ID_MAX_LEN:
        head = slug[:_ID_MAX_LEN]
        slug = head.rsplit("-", 1)[0] if "-" in head else head
        slug = slug.strip("-")
    if not slug:
        digest = hashlib.sha1((title_str or "").encode("utf-8")).hexdigest()[:8]
        slug = f"entry-{digest}"
    return slug
