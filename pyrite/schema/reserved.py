"""Reserved field names that cannot be used in kb.yaml custom field definitions.

Protocol fields (status, priority, date, etc.) are NOT reserved here because
users legitimately need to define FieldSchema constraints on them in kb.yaml
(e.g., select options for status). Protocol fields are handled separately by
the protocol system and IndexManager column promotion.
"""

from __future__ import annotations

RESERVED_FIELD_NAMES: frozenset[str] = frozenset(
    {
        # Entry base dataclass fields
        "id",
        "title",
        "body",
        "summary",
        "tags",
        "aliases",
        "links",
        "sources",
        "provenance",
        "metadata",
        "importance",
        "lifecycle",
        "created_at",
        "updated_at",
        "kb_name",
        "file_path",
        "_schema_version",
        "extra_frontmatter",
        "_absent_default_keys",
        "_source_frontmatter",
        "_nested_metadata_keys",
        "_shadowed_nested",
        "_raw_metadata",
        "_unparsed_timestamp_keys",
        "_unrepresented_keys",
        "_create_fields",
        "_importance_given",
        # Frontmatter-only keys
        "type",
    }
)


def reserved_field_error_message(type_name: str, field_name: str) -> str:
    """Error message when a custom field declaration collides with a reserved name."""
    reserved_sorted = ", ".join(sorted(RESERVED_FIELD_NAMES))
    return (
        f"Type '{type_name}': field '{field_name}' is a reserved name and cannot be declared under fields. "
        f"Reserved names: {reserved_sorted}"
    )
