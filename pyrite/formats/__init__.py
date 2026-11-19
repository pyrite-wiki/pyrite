"""Format registry and content negotiation for Pyrite API responses."""

from dataclasses import dataclass
from typing import Any


@dataclass
class FormatSpec:
    """Specification for an output format."""

    name: str
    media_type: str
    file_extension: str
    serializer: Any  # callable: (data: Any, **kwargs) -> str


class FormatRegistry:
    """Registry of available output formats."""

    def __init__(self):
        self._formats: dict[str, FormatSpec] = {}

    def register(self, spec: FormatSpec):
        self._formats[spec.name] = spec

    def get(self, name: str) -> FormatSpec | None:
        return self._formats.get(name)

    def get_by_media_type(self, media_type: str) -> FormatSpec | None:
        for spec in self._formats.values():
            if spec.media_type == media_type:
                return spec
        return None

    def available_formats(self) -> list[str]:
        return list(self._formats.keys())


# Global registry singleton
_registry: FormatRegistry | None = None


def get_format_registry() -> FormatRegistry:
    global _registry
    if _registry is None:
        _registry = FormatRegistry()
        _register_defaults(_registry)
    return _registry


def _register_defaults(registry: FormatRegistry):
    from .csv_fmt import csv_serialize
    from .json_fmt import json_serialize
    from .markdown_fmt import markdown_serialize
    from .yaml_fmt import yaml_serialize

    registry.register(FormatSpec("json", "application/json", "json", json_serialize))
    registry.register(FormatSpec("markdown", "text/markdown", "md", markdown_serialize))
    registry.register(FormatSpec("csv", "text/csv", "csv", csv_serialize))
    registry.register(FormatSpec("yaml", "text/yaml", "yaml", yaml_serialize))


def negotiate_format(accept_header: str) -> str | None:
    """Parse Accept header and return best matching format name.

    Returns 'json' as default. Returns None if Accept specifies something
    we don't support and doesn't include */*.
    """
    if not accept_header:
        return "json"

    import re

    ranges = []
    for position, part in enumerate(accept_header.split(",")):
        media, *parameters = (piece.strip() for piece in part.split(";"))
        quality = 1.0
        for parameter in parameters:
            key, separator, value = parameter.partition("=")
            if separator and key.strip().lower() == "q":
                value = value.strip()
                quality = (
                    float(value)
                    if re.fullmatch(r"(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)", value)
                    else 0.0
                )
        ranges.append((media.lower(), quality, position))

    choices = []
    registry = get_format_registry()
    for name in registry.available_formats():
        spec = registry.get(name)
        assert spec is not None
        media = spec.media_type.lower()
        matches = []
        for pattern, quality, position in ranges:
            specificity = (
                2
                if pattern == media
                else 1
                if pattern == media.split("/")[0] + "/*"
                else 0
                if pattern == "*/*"
                else -1
            )
            if specificity >= 0:
                matches.append((specificity, quality, -position))
        if matches:
            # A specific exclusion overrides a broader wildcard allowance
            # (RFC9110 section12.5.1), before comparing representations.
            _, quality, order = max(matches)
            if quality > 0:
                choices.append((quality, order, name))
    if not choices:
        return None
    # Keep registry order as the server preference when the client ties.
    return max(choices, key=lambda choice: choice[:2])[2]


def format_response(data: Any, format_name: str, **kwargs) -> tuple[str, str]:
    """Serialize data to the specified format.

    Returns (content_string, media_type).
    """
    registry = get_format_registry()
    spec = registry.get(format_name)
    if not spec:
        raise ValueError(f"Unknown format: {format_name}")
    return spec.serializer(data, **kwargs), spec.media_type
