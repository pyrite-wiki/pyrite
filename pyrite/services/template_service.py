"""
Template Service

Manages user-defined markdown templates that scaffold new KB entries.
Templates are stored as markdown files with YAML frontmatter in
``_templates/`` within each KB directory.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..config import PyriteConfig
from ..exceptions import FrontmatterError, KBNotFoundError
from ..utils.frontmatter import Frontmatter, NoFrontmatter, describe, split_frontmatter

# Pattern for {{variable}} placeholders
_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")

logger = logging.getLogger(__name__)

# Built-in variables that are auto-populated at render time
BUILTIN_VARIABLES = {"date", "datetime", "title", "kb", "author"}


class TemplateService:
    """Service for listing, reading, and rendering KB templates."""

    def __init__(self, config: PyriteConfig):
        self.config = config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _kb_path(self, kb_name: str) -> Path:
        """Resolve the root path for a KB, raising if not found."""
        kb = self.config.get_kb(kb_name)
        if kb is None:
            raise KBNotFoundError(f"KB '{kb_name}' not found")
        return kb.path

    def _templates_dir(self, kb_name: str) -> Path:
        return self._kb_path(kb_name) / "_templates"

    @staticmethod
    def _parse_template_file(path: Path) -> dict[str, Any]:
        """Parse a template markdown file into its components.

        Returns a dict with keys:
            name, description, entry_type, frontmatter (dict), body (str)
        """
        text = path.read_text(encoding="utf-8")

        frontmatter: dict[str, Any] = {}
        body = text

        split = split_frontmatter(text)
        if isinstance(split, Frontmatter):
            try:
                frontmatter = yaml.safe_load(split.text) or {}
            except yaml.YAMLError as e:
                raise FrontmatterError(
                    f"Template {path.name}: invalid YAML frontmatter: {e}"
                ) from e
            if not isinstance(frontmatter, dict):
                raise FrontmatterError(f"Template {path.name}: frontmatter is not a mapping")
            body = text[split.close_end :].lstrip("\n")
        elif not isinstance(split, NoFrontmatter):
            raise FrontmatterError(f"Template {path.name}: {describe(split)}")

        name = frontmatter.pop("template_name", path.stem)
        description = frontmatter.pop("template_description", "")

        return {
            "name": name,
            "description": description,
            "entry_type": frontmatter.get("entry_type", "note"),
            "frontmatter": frontmatter,
            "body": body,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_templates(self, kb_name: str) -> list[dict[str, Any]]:
        """List available templates for a KB.

        Returns a list of dicts with ``name``, ``description``, and ``entry_type`` keys.
        """
        tpl_dir = self._templates_dir(kb_name)
        if not tpl_dir.is_dir():
            return []

        templates: list[dict[str, Any]] = []
        for path in sorted(tpl_dir.glob("*.md")):
            try:
                parsed = self._parse_template_file(path)
            except (FrontmatterError, yaml.YAMLError, UnicodeDecodeError) as e:
                # One unreadable template must not hide the others.
                logger.warning("Skipping template %s: %s", path.name, e)
                continue
            templates.append(
                {
                    "name": parsed["name"],
                    "description": parsed["description"],
                    "entry_type": parsed["entry_type"],
                }
            )
        return templates

    def get_template(self, kb_name: str, template_name: str) -> dict[str, Any]:
        """Get a single template by name.

        ``template_name`` is matched against the file stem (e.g. ``meeting-note``
        for ``meeting-note.md``).

        Raises ``FileNotFoundError`` if the template does not exist.
        """
        tpl_dir = self._templates_dir(kb_name)
        path = tpl_dir / f"{template_name}.md"
        if not path.is_file():
            raise FileNotFoundError(f"Template '{template_name}' not found in KB '{kb_name}'")
        return self._parse_template_file(path)

    def render_template(
        self,
        kb_name: str,
        template_name: str,
        variables: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Render a template with the given variables.

        Built-in variables (``date``, ``datetime``, ``title``, ``kb``, ``author``)
        are automatically populated.  User-supplied ``variables`` override built-ins
        and fill custom placeholders.  Unresolved custom placeholders are left as-is.

        Returns a dict with ``entry_type``, ``frontmatter`` (dict), and ``body`` (str).
        """
        tpl = self.get_template(kb_name, template_name)
        variables = variables or {}

        now = datetime.now(UTC)
        builtin: dict[str, str] = {
            "date": now.strftime("%Y-%m-%d"),
            "datetime": now.isoformat(),
            "kb": kb_name,
            "title": variables.get("title", ""),
            "author": variables.get("author", ""),
        }
        # Merge: user values override built-ins
        merged = {**builtin, **variables}

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in merged and merged[key]:
                return merged[key]
            # Leave unresolved custom placeholders intact
            return match.group(0)

        rendered_body = _VAR_PATTERN.sub(_replace, tpl["body"])

        # Also render placeholders inside frontmatter string values
        rendered_fm: dict[str, Any] = {}
        for k, v in tpl["frontmatter"].items():
            if isinstance(v, str):
                rendered_fm[k] = _VAR_PATTERN.sub(_replace, v)
            else:
                rendered_fm[k] = v

        return {
            "entry_type": rendered_fm.get("entry_type", tpl["entry_type"]),
            "frontmatter": rendered_fm,
            "body": rendered_body,
        }
