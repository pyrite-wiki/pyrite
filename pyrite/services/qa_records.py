"""What a QA record is, decided in one place (#1752).

`qa assess` writes a `qa_assessment` entry per assessed entry. Those records
are real, indexed, searchable entries, but they are not content: running
`qa assess` must change nothing that `qa validate`, `qa status`, `qa gaps` or
`export site` report about the content entries.

Every reader that enumerates entries in the QA services, and every exporter,
asks this module instead of spelling the type. `content_only` is the SQL
clause for "not a QA record"; `records_only` is its complement, for the
readers whose subject is the records (coverage, staleness of assessments).
"""

from __future__ import annotations

#: The entry types that are QA records, not content.
QA_RECORD_TYPES: frozenset[str] = frozenset({"qa_assessment"})


def is_qa_record(entry_type: str | None) -> bool:
    """True for an entry type that is a QA record."""
    return entry_type in QA_RECORD_TYPES


def _type_list() -> str:
    return ", ".join(f"'{t}'" for t in sorted(QA_RECORD_TYPES))


def _column(alias: str) -> str:
    return f"{alias}.entry_type" if alias else "entry_type"


def content_only(alias: str = "") -> str:
    """SQL clause: the row is content, not a QA record. ``alias`` is the table alias."""
    return f"{_column(alias)} NOT IN ({_type_list()})"


def records_only(alias: str = "") -> str:
    """SQL clause: the row is a QA record."""
    return f"{_column(alias)} IN ({_type_list()})"
