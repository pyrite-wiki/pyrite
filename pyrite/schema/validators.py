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
      is indexed instead of silently skipped. The id comes from the value
      YAML parsed, not the source text, so spellings that parse equal
      (``1.10`` and ``1.1``, ``1e3`` and ``1000.0``, ``.inf`` and ``-.inf`` both ``inf``) collide; ``ids missing``
      reports the collision. A title that is a mapping, a sequence or NaN
      raises ``TypeError`` with a named reason (#711): the ids and index
      paths skip such a file with that reason.

    Plain ASCII string titles produce exactly what they always did. A title
    YAML reads as a falsy scalar (``false``, ``0``, ``0.0``, ``-0``) keeps the
    ``entry-da39a3ee`` empty-title id it had before #704, so upgrading moves
    no id and breaks no link; the limit is that two such files share that id,
    which ``ids missing`` reports as a collision. Every place that turns a title into a filename must use
    this function (the ``sw new-adr`` CLI and MCP tool once let ``/`` and
    ``:`` through).
    """
    import hashlib
    import math
    import unicodedata

    if isinstance(title, (list, tuple, dict)):
        raise TypeError(f"cannot derive an id: title is a {type(title).__name__}")
    if isinstance(title, float) and math.isnan(title):
        raise TypeError("cannot derive an id: title is NaN")
    if not title:
        # None, "" and falsy scalars (0, False, 0.0) all slug as the empty
        # title, as they did before #704, so no existing id moves.
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
