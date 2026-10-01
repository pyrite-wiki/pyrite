"""Declared enums in kb.yaml: the one place their findings are made (#555).

A KB declares an enum three ways -- ``options:`` (or its alias ``values:``) on
a ``select``, ``multi-select`` or ``list`` field, ``items: {options: [...]}``
on a ``list`` field, and ``validation.rules[]`` entries with ``enum:``.
:func:`enum_findings` checks all three, and every reader uses it:
``KBSchema.validate_entry`` (the write path, ``qa validate``, ``ci``),
``schema validate`` (the pre-commit hook) and ``index health``. Before, four
readers disagreed and three of the four forms were never checked.

Severity is governed by ``validation.enforce_enums`` (default ``true``), not
by ``validation.enforce``: on, an off-list value is an error and the write is
refused; off, it is a warning. ``allow_other: true`` on a field makes that
field's finding a warning in both modes. Plugin enum vocabularies are
code-owned and are not governed by this switch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .kb_schema import KBSchema

#: The field types whose declared options constrain the value.
ENUM_FIELD_TYPES = frozenset({"select", "multi-select", "list"})

#: Every rule identifier that means "a value is not on its declared list":
#: the kb.yaml ones made here, and a plugin validator's ``enum``. The write
#: path's exception for values already on disk applies to these only.
ENUM_RULES = frozenset({"field_select", "field_multi_select", "field_list", "rule_enum", "enum"})


def enforce_enums(kb_schema: KBSchema) -> bool:
    """Whether this KB refuses off-list kb.yaml values (``validation.enforce_enums``, default on)."""
    value = (kb_schema.validation or {}).get("enforce_enums", True)
    return value is not False


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _off_list(value: Any, allowed: list[Any], *, per_element: bool) -> Any:
    """The offending part of ``value``: a list of bad elements, the bad scalar, or None."""
    if per_element:
        elements = value if isinstance(value, list) else [value]
        bad = [v for v in elements if v not in allowed]
        return bad or None
    return None if value in allowed else value


def enum_findings(
    kb_schema: KBSchema, entry_type: str, fields: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every off-list value in ``fields`` against the KB's declared enums.

    Each finding is ``{field, rule, expected, got, origin, severity}``:
    ``expected`` is the allowed list; ``got`` is the offending scalar, or the
    offending elements of a list value; ``origin`` is ``field`` (a field's
    options) or ``rule`` (a ``validation.rules`` enum); ``severity`` is
    ``error`` or ``warning`` per the switch and ``allow_other`` (which also
    sets ``allow_other: True`` on the finding). An absent or empty value is
    not an off-list value -- ``required`` covers absence.
    """
    on = enforce_enums(kb_schema)
    switch_severity = "error" if on else "warning"
    findings: list[dict[str, Any]] = []

    type_schema = kb_schema.types.get(entry_type)
    for name, fs in (type_schema.fields if type_schema else {}).items():
        if fs.field_type not in ENUM_FIELD_TYPES or name not in fields:
            continue
        allowed = fs.allowed_values()
        value = fields[name]
        if not allowed or _is_empty(value):
            continue
        if fs.field_type == "multi-select" and not isinstance(value, list):
            continue  # a type error, reported by _validate_field_value under `enforce`
        bad = _off_list(value, allowed, per_element=fs.field_type != "select")
        if bad is None:
            continue
        rule = {
            "select": "field_select",
            "multi-select": "field_multi_select",
            "list": "field_list",
        }[fs.field_type]
        finding = {
            "field": name,
            "rule": rule,
            "expected": allowed,
            "got": bad,
            "origin": "field",
            "severity": "warning" if fs.allow_other else switch_severity,
        }
        if fs.allow_other:
            finding["allow_other"] = True
        findings.append(finding)

    for rule_def in (kb_schema.validation or {}).get("rules", []) or []:
        if not isinstance(rule_def, dict) or "enum" not in rule_def:
            continue
        name = rule_def.get("field")
        if name not in fields or _is_empty(fields[name]):
            continue
        allowed = list(rule_def.get("enum") or [])
        value = fields[name]
        bad = _off_list(value, allowed, per_element=isinstance(value, list))
        if bad is None:
            continue
        findings.append(
            {
                "field": name,
                "rule": "rule_enum",
                "expected": allowed,
                "got": bad,
                "origin": "rule",
                "severity": switch_severity,
            }
        )

    return findings


def schema_enum_warnings(kb_schema: KBSchema) -> list[dict[str, str]]:
    """Schema-level problems with the declared enums themselves.

    Today: a field that gives both ``options:`` and ``values:`` with different
    lists. ``options`` wins; this says so, naming the type and field.
    """
    out = []
    for type_name, ts in kb_schema.types.items():
        for name, fs in ts.fields.items():
            if fs.options_conflict:
                out.append(
                    {
                        "type": type_name,
                        "field": name,
                        "message": (
                            f"Type '{type_name}' field '{name}' declares both `options:` and "
                            f"`values:` with different lists; `options` is used: {fs.options}"
                        ),
                    }
                )
    return out
