"""Validation utilities for KB schema fields."""

import re
from datetime import UTC, datetime
from typing import Any

from ..exceptions import ValidationError


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


#: The example every ``since`` refusal shows. One spelling, so the CLI, MCP and
#: the tests cannot disagree about what "a datetime" means.
SINCE_EXAMPLE = "2026-10-22T00:00:00Z"


def _since_refusal(detail: str) -> ValidationError:
    """The one refusal for a ``since`` value that cannot be read (#1268)."""
    return ValidationError(
        f"since: {detail}",
        suggestion=(
            "Pass a date in YYYY-MM-DD form, such as 2026-10-22 (read as "
            f"midnight UTC), or an ISO 8601 datetime such as {SINCE_EXAMPLE}, "
            "with or without an offset. An offset is read as the instant it "
            "names; relative forms (yesterday, 24h) are not accepted."
        ),
    )


def parse_since(value: Any) -> datetime | None:
    """Read a ``since`` parameter as the aware UTC instant it names.

    Accepted: a date (``2026-10-22``, midnight UTC), an ISO 8601 datetime with
    or without an offset (``2026-10-22T00:00:00Z``,
    ``2026-10-23T05:00:00-04:00``, ``2026-10-22 10:00``), and an absent or
    empty value, which means "no lower bound" and returns ``None``.

    Anything else is refused with ``VALIDATION_FAILED`` naming the parameter,
    the accepted forms and an example, rather than being compared as a string
    and matching nothing: an empty list for a value that was never understood
    reads as "nothing changed since then", which is a confident wrong answer.

    ``models/base.parse_datetime`` is deliberately not used: it turns an
    unparseable value into "now", which is the guess this refusal replaces.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise _since_refusal(f"expected a date or an ISO datetime, got {type(value).__name__}")
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(re.sub(r"[Zz]$", "+00:00", text))
    except ValueError:
        raise _since_refusal(f"{text!r} is not a date or an ISO datetime") from None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def filter_updated_since(entries: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Keep the entries whose ``updated_at`` is at or after ``since``.

    Compares instants, not strings: ``2026-10-23T05:00:00-04:00`` is 09:00Z,
    and a lexical comparison kept every entry updated earlier that UTC day
    (#1268). An entry whose ``updated_at`` cannot be read is dropped -- it
    cannot be shown to be newer.
    """
    kept = []
    for entry in entries:
        stamp = _updated_at_instant(entry.get("updated_at"))
        if stamp is not None and stamp >= since:
            kept.append(entry)
    return kept


def _updated_at_instant(value: Any) -> datetime | None:
    """An ``updated_at`` value as an aware UTC instant, or ``None`` if unreadable."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(re.sub(r"[Zz]$", "+00:00", value.strip()))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


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
