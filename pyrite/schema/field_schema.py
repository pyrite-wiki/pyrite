"""Field and type schema definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .validators import validate_date

if TYPE_CHECKING:
    from ..models import Entry

_TEMPLATE_RE = re.compile(r"\{(\w+)\}")


def expand_subdirectory_template(template: str, entry: Entry) -> str:
    """Expand {field} placeholders in a subdirectory template from entry fields.

    Resolution order for each placeholder:
      1. Dataclass attribute (getattr) — covers core type fields like status, date
      2. entry.metadata[field] — covers GenericEntry custom fields
      3. Fallback to literal "_unknown"

    Enum values are converted via .value. The result is sanitized to prevent
    path traversal (no "..", no leading "/").
    """
    if not template or "{" not in template:
        return template

    def _replace(match: re.Match) -> str:
        field_name = match.group(1)
        value = getattr(entry, field_name, None)
        if value is None:
            md = getattr(entry, "metadata", None)
            if md is not None:
                value = md.get(field_name)
        if value is None:
            return "_unknown"
        if hasattr(value, "value"):
            value = value.value
        s = str(value).strip().lower().replace(" ", "-")
        s = s.replace("..", "").replace("/", "-").strip("-") or "_unknown"
        return s

    result = _TEMPLATE_RE.sub(_replace, template)
    return result.lstrip("/")


@dataclass
class FieldSchema:
    """Schema definition for a single typed field.

    Supports 10 field types: text, number, date, datetime, checkbox,
    select, multi-select, object-ref, list, tags.
    """

    name: str
    field_type: str = "text"
    required: bool = False
    default: Any = None
    description: str = ""
    options: list[str] = field(default_factory=list)  # for select/multi-select
    allow_other: bool = (
        False  # when True, unknown select/multi-select values produce warnings not errors
    )
    items: dict[str, Any] = field(default_factory=dict)  # for list type
    constraints: dict[str, Any] = field(default_factory=dict)  # min, max, format, target_type
    since_version: int | None = None
    #: kb.yaml gave both ``options:`` and its alias ``values:`` with different
    #: lists; ``options`` won. Not serialized: `schema validate` reports it (#555).
    options_conflict: bool = field(default=False, compare=False, repr=False)

    VALID_TYPES = frozenset(
        [
            "text",
            "number",
            "date",
            "datetime",
            "checkbox",
            "select",
            "multi-select",
            "object-ref",
            "list",
            "tags",
        ]
    )

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> FieldSchema:
        """Parse a field definition from kb.yaml."""
        constraints = {}
        for key in ("format", "min_length", "max_length", "min", "max", "target_type"):
            if key in data:
                constraints[key] = data[key]

        # `values:` is a permanent alias of `options:` (#555): the kb-lifecycle
        # skill taught it and real KBs use it, and the parser used to drop it,
        # so a declared enum constrained nothing. `options:` is canonical and
        # wins when both are given.
        options, conflict = _options_or_values(data)
        items = data.get("items") or {}
        if isinstance(items, dict) and "options" not in items and "values" in items:
            items = {
                **{k: v for k, v in items.items() if k != "values"},
                "options": items["values"],
            }

        return cls(
            name=name,
            field_type=data.get("type", "text"),
            required=data.get("required", False),
            default=data.get("default"),
            description=data.get("description", ""),
            options=options,
            allow_other=data.get("allow_other", False),
            items=items,
            constraints=constraints,
            since_version=data.get("since_version"),
            options_conflict=conflict,
        )

    def allowed_values(self) -> list[Any]:
        """The declared enum for this field: ``options`` (or ``values``), else
        ``items.options`` (or ``items.values``) for a list; empty when none."""
        if self.options:
            return list(self.options)
        if isinstance(self.items, dict):
            return list(self.items.get("options") or [])
        return []

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for agent schema and API responses."""
        result: dict[str, Any] = {"type": self.field_type}
        if self.required:
            result["required"] = True
        if self.default is not None:
            result["default"] = self.default
        if self.description:
            result["description"] = self.description
        if self.options:
            result["options"] = self.options
        if self.allow_other:
            result["allow_other"] = True
        if self.items:
            result["items"] = self.items
        if self.since_version is not None:
            result["since_version"] = self.since_version
        result.update(self.constraints)
        return result


def _options_or_values(data: dict[str, Any]) -> tuple[list[Any], bool]:
    """``(options, conflict)`` for a field definition, reading the ``values`` alias."""
    options = data.get("options")
    values = data.get("values")
    if options is None:
        return list(values or []), False
    return list(options), values is not None and list(values) != list(options)


@dataclass
class EndpointSpec:
    """Schema for an edge-type endpoint."""

    field: str  # which entry field maps to this endpoint
    accepts: list[str] = field(default_factory=list)  # accepted entry types


@dataclass
class TypeSchema:
    """Schema definition for an entry type (core or custom)."""

    name: str
    description: str = ""
    required: list[str] = field(default_factory=lambda: ["title"])
    optional: list[str] = field(default_factory=list)
    subdirectory: str | None = None  # None = use default; "" = explicit KB root
    file_pattern: str = ""  # e.g. "{date}--{slug}.md" for custom filenames
    fields: dict[str, FieldSchema] = field(default_factory=dict)
    protocols: list[str] = field(default_factory=list)  # ADR-0017: e.g. ["temporal", "assignable"]
    layout: str = ""  # "document" or "record"
    ai_instructions: str = ""
    field_descriptions: dict[str, str] = field(default_factory=dict)
    display: dict[str, Any] = field(default_factory=dict)
    version: int = 0
    guidelines: str = ""  # Contributing standards, quality expectations
    goals: str = ""  # What entries of this type should achieve
    evaluation_rubric: list[str | dict[str, Any]] = field(
        default_factory=list
    )  # Assertions for QA validation
    edge_type: bool = False  # Whether this type represents an edge/relationship
    endpoints: dict[str, EndpointSpec] = field(default_factory=dict)  # Edge endpoint specs
    state_machine: dict[str, Any] | None = None  # Per-type workflow override (Tier A r1175)
    #: ``{alias: target}`` as the operator wrote it (#697). Kept raw so a malformed
    #: declaration is reported by ``pyrite schema validate`` and ignored by
    #: ``field_aliases()``, never a crash at load.
    field_aliases: Any = field(default_factory=dict)

    def resolve_subdirectory(self, entry: Entry) -> str:
        """Return the resolved subdirectory, expanding template placeholders."""
        if not self.subdirectory:  # None or ""
            return ""
        return expand_subdirectory_template(self.subdirectory, entry)

    #: A bare field name only: no attribute/index access (`{title.upper}`,
    #: `{items[0]}`), no positional/auto placeholders (`{0}`, `{}`).
    _PLACEHOLDER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    #: A resolved filename component this long is refused (#391 cold read
    #: item 4) rather than handed to the filesystem, where the failure mode
    #: varies by OS.
    _MAX_COMPONENT_BYTES = 200

    def resolve_filename(self, entry: Entry) -> str | None:
        """Return a custom filename for the entry, or None to use the default.

        Supported placeholders:
          - ``{id}`` — entry ID
          - ``{slug}`` — entry ID (alias)
          - ``{date}`` — entry date field (YYYY-MM-DD)
          - ``{title}`` — slugified title
          - ``{type}`` — entry type
          - any other ``{field}`` — a plain field name only (no
            ``{title.upper}`` attribute access, no ``{items[0]}`` index
            access, no positional ``{0}``/auto ``{}}``): one of the entry's
            own dataclass fields, falling back to ``entry.metadata[field]``,
            with an optional Python format spec, e.g. ``{adr_number:04d}``
            (#391 — the software-kb ``adr`` type keeps its ``NNNN-slug.md``
            convention through ``KBService.create`` this way).

        A field this entry does not have -- or one that IS missing in the
        sense that matters for a filename (``None``, ``""``, whitespace-only,
        or a value starting with ``.``) -- falls back to the default filename
        (returns ``None``), the recorded #391 acceptance: a placeholder is a
        convenience, not a requirement the caller must satisfy field by
        field. A placeholder using anything but a plain field name (`{0}`,
        `{title.upper}`, a non-data attribute like `{save}`), a format spec
        the value cannot satisfy (e.g. ``:04d`` on a string), a NUL byte, or
        a resolved filename longer than ~200 bytes all raise
        :class:`~pyrite.exceptions.ValidationError` instead -- these are
        pattern/data BUGS, not an ordinarily-absent field, and must not
        reach the filesystem as a raw ``ValueError``/``TypeError``/``OSError``.
        The resolved filename is also refused if it would escape the type's
        folder: any path separator, or a ``..`` PATH COMPONENT (not merely a
        ``..`` substring -- a real name like ``v1..2`` must not be refused).

        Example: ``file_pattern: "{date}--{slug}.md"``
        """
        if not self.file_pattern:
            return None
        from ..exceptions import ValidationError
        from ..schema import generate_entry_id

        fixed = {
            "id": entry.id,
            "slug": entry.id,
            "date": getattr(entry, "date", "") or "",
            "title": generate_entry_id(entry.title),
            "type": entry.entry_type,
        }

        import dataclasses
        from string import Formatter

        data_fields = (
            {f.name for f in dataclasses.fields(entry)}
            if dataclasses.is_dataclass(entry)
            else set()
        )
        meta = getattr(entry, "metadata", None) or {}

        def _is_missing(value: Any) -> bool:
            if value is None:
                return True
            if isinstance(value, str):
                stripped = value.strip()
                return stripped == "" or stripped.startswith(".")
            return False

        values: dict[str, Any] = {}
        for _literal, field_name, _format_spec, conversion in Formatter().parse(self.file_pattern):
            if field_name is None:
                continue
            if field_name in fixed:
                values[field_name] = fixed[field_name]
                continue
            if not self._PLACEHOLDER_NAME_RE.match(field_name):
                raise ValidationError(
                    f"file_pattern {self.file_pattern!r} has placeholder "
                    f"{{{field_name}}}, which is not a plain field name (no "
                    "attribute/index access, no positional placeholders); "
                    "refusing to resolve it"
                )
            if field_name in data_fields:
                value = getattr(entry, field_name, None)
            elif field_name in meta:
                value = meta[field_name]
            elif hasattr(entry, field_name):
                # A plain identifier that resolves to something on the
                # entry that is NOT a data field -- a method (`{save}`), a
                # property, whatever -- puts that object's repr in the
                # filename if we let `.format()` touch it. That is a
                # pattern bug, refused, not an ordinarily-missing field.
                raise ValidationError(
                    f"file_pattern {self.file_pattern!r} names {{{field_name}}}, "
                    f"which is not a data field of entry {entry.id!r} (type "
                    f"{entry.entry_type!r}); refusing to resolve it"
                )
            else:
                # Genuinely absent: fall back to the default filename rather
                # than refuse (#391 recorded acceptance).
                return None
            if _is_missing(value):
                return None
            values[field_name] = value
            if conversion:
                # `!r`/`!s`/`!a` are format-string features we do not need
                # and do not want to reason about (they can call arbitrary
                # __repr__/__str__); a plain field value only.
                raise ValidationError(
                    f"file_pattern {self.file_pattern!r} uses a conversion "
                    f"(!{conversion}) on {{{field_name}}}, which is not "
                    "supported; refusing to resolve it"
                )

        try:
            result = self.file_pattern.format(**values)
        except (ValueError, TypeError, IndexError, KeyError) as e:
            raise ValidationError(
                f"file_pattern {self.file_pattern!r} could not be resolved for entry "
                f"{entry.id!r}: {e}"
            ) from e

        if "\x00" in result:
            raise ValidationError(
                f"file_pattern {self.file_pattern!r} resolved to a filename "
                f"containing a NUL byte for entry {entry.id!r}; refusing to write it"
            )

        # `resolve_filename` produces a single filename component, never a
        # path (the caller joins it under the type's own subdirectory), so
        # ANY path separator is refused outright -- and a resolved name that
        # is exactly the `..` PATH COMPONENT is refused as traversal. This is
        # a whole-component check, not a substring one: `v1..2` is a
        # legitimate filename fragment and must not be refused.
        if "/" in result or "\\" in result:
            raise ValidationError(
                f"file_pattern {self.file_pattern!r} resolved to {result!r} for entry "
                f"{entry.id!r}, which contains a path separator; refusing to write it"
            )
        if result in (".", ".."):
            raise ValidationError(
                f"file_pattern {self.file_pattern!r} resolved to {result!r} for entry "
                f"{entry.id!r}, which is a '..'/'.' path component; refusing to write it"
            )

        if len(result.encode("utf-8", errors="surrogateescape")) > self._MAX_COMPONENT_BYTES:
            raise ValidationError(
                f"file_pattern {self.file_pattern!r} resolved to a filename over "
                f"{self._MAX_COMPONENT_BYTES} bytes for entry {entry.id!r}; "
                "refusing to write it"
            )

        return result

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"description": self.description}
        if self.required != ["title"]:
            result["required"] = self.required
        if self.optional:
            result["optional"] = self.optional
        if self.subdirectory is not None:
            result["subdirectory"] = self.subdirectory
        if self.file_pattern:
            result["file_pattern"] = self.file_pattern
        if self.fields:
            result["fields"] = {name: fs.to_dict() for name, fs in self.fields.items()}
        if self.protocols:
            result["protocols"] = self.protocols
        if self.layout:
            result["layout"] = self.layout
        if self.ai_instructions:
            result["ai_instructions"] = self.ai_instructions
        if self.field_descriptions:
            result["field_descriptions"] = self.field_descriptions
        if self.display:
            result["display"] = self.display
        if self.version > 0:
            result["version"] = self.version
        if self.guidelines:
            result["guidelines"] = self.guidelines
        if self.goals:
            result["goals"] = self.goals
        if self.evaluation_rubric:
            result["evaluation_rubric"] = self.evaluation_rubric
        if self.edge_type:
            result["edge_type"] = True
        if self.endpoints:
            result["endpoints"] = {
                role: {"field": ep.field, "accepts": ep.accepts}
                for role, ep in self.endpoints.items()
            }
        return result


def _validate_field_value(
    field_name: str, value: Any, field_schema: FieldSchema
) -> list[dict[str, Any]]:
    """Validate a field value against its FieldSchema. Returns list of error dicts."""
    errors: list[dict[str, Any]] = []
    ft = field_schema.field_type

    if ft == "number":
        try:
            num = int(value) if isinstance(value, int) else float(value)
            min_val = field_schema.constraints.get("min")
            max_val = field_schema.constraints.get("max")
            if min_val is not None and num < min_val:
                errors.append(
                    {
                        "field": field_name,
                        "rule": "field_range",
                        "expected": f">= {min_val}",
                        "got": value,
                    }
                )
            if max_val is not None and num > max_val:
                errors.append(
                    {
                        "field": field_name,
                        "rule": "field_range",
                        "expected": f"<= {max_val}",
                        "got": value,
                    }
                )
        except (ValueError, TypeError):
            errors.append(
                {
                    "field": field_name,
                    "rule": "field_number",
                    "expected": "numeric value",
                    "got": value,
                }
            )

    elif ft == "date":
        if isinstance(value, str) and not validate_date(value):
            errors.append(
                {
                    "field": field_name,
                    "rule": "field_date",
                    "expected": "YYYY-MM-DD",
                    "got": value,
                }
            )

    elif ft == "datetime":
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError:
                errors.append(
                    {
                        "field": field_name,
                        "rule": "field_datetime",
                        "expected": "ISO 8601 datetime",
                        "got": value,
                    }
                )

    elif ft == "checkbox":
        if not isinstance(value, bool):
            errors.append(
                {
                    "field": field_name,
                    "rule": "field_checkbox",
                    "expected": "boolean",
                    "got": type(value).__name__,
                }
            )

    elif ft == "multi-select":
        # Whether each element is on the declared list is an enum finding,
        # made by `enum_check.enum_findings` under `validation.enforce_enums`
        # (#555). A value that is not a list at all is a type error, governed
        # by `validation.enforce` like every other one.
        if not isinstance(value, list):
            errors.append(
                {
                    "field": field_name,
                    "rule": "field_multi_select",
                    "expected": "list",
                    "got": type(value).__name__,
                }
            )

    # select / list / multi-select membership: see enum_check.enum_findings.
    # text, object-ref, tags -- no validation beyond presence (for now)

    return errors
