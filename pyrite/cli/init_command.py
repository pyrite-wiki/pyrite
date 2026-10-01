"""
Headless KB init command.

Creates a new knowledge base from a template with zero prompts.
"""

import logging
from pathlib import Path

import typer
from rich.console import Console

logger = logging.getLogger(__name__)

console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


# Built-in templates (no extension install required)
BUILTIN_TEMPLATES = {
    "software": {
        "name": "my-project",
        "description": "Software team knowledge base with ADRs, design docs, standards, components, backlog, and runbooks",
        "types": {
            "adr": {
                "description": "Architecture Decision Record",
                "required": ["title"],
                "optional": ["adr_number", "status", "deciders", "date", "superseded_by"],
                "subdirectory": "adrs/",
            },
            "design_doc": {
                "description": "Design document or specification",
                "required": ["title"],
                "optional": ["status", "reviewers", "date", "author", "url"],
                "subdirectory": "designs/",
            },
            "standard": {
                "description": "Coding standard or convention",
                "required": ["title"],
                "optional": ["category", "enforced"],
                "subdirectory": "standards/",
            },
            "component": {
                "description": "Module or service documentation",
                "required": ["title"],
                "optional": ["kind", "path", "owner", "dependencies"],
                "subdirectory": "components/",
            },
            "backlog_item": {
                "description": "Feature, bug, or tech debt item",
                "required": ["title"],
                "optional": ["kind", "status", "priority", "assignee", "effort"],
                "subdirectory": "backlog/",
            },
            "runbook": {
                "description": "How-to guide or operational procedure",
                "required": ["title"],
                "optional": ["runbook_kind", "audience"],
                "subdirectory": "runbooks/",
            },
        },
        "policies": {"team_owned": True, "require_adr_number": True},
        # No rule enum on `status`: a rule applies to every type with the
        # field, and each type here has its own statuses, which the
        # software-kb validator checks per type (#555 round 1).
        "validation": {
            "enforce": True,
            "rules": [],
        },
        "directories": ["adrs", "designs", "standards", "components", "backlog", "runbooks"],
    },
    "zettelkasten": {
        "name": "my-zettelkasten",
        "description": "Personal knowledge garden",
        "types": {
            "zettel": {
                "description": "Atomic knowledge note",
                "required": ["title"],
                "optional": ["zettel_type", "maturity", "source_ref", "processing_stage"],
                "subdirectory": "zettels/",
            },
            "literature_note": {
                "description": "Note capturing ideas from a source work",
                "required": ["title", "source_work"],
                "optional": ["author", "page_refs"],
                "subdirectory": "literature/",
            },
        },
        "policies": {"private": True, "single_author": True},
        "validation": {
            "enforce": True,
            "rules": [
                {"field": "zettel_type", "enum": ["fleeting", "literature", "permanent", "hub"]},
                {"field": "maturity", "enum": ["seed", "sapling", "evergreen"]},
                {
                    "field": "processing_stage",
                    "enum": ["capture", "elaborate", "question", "review", "connect"],
                },
            ],
        },
        "directories": ["zettels", "literature"],
    },
    "research": {
        "name": "my-research",
        "description": "Research knowledge base",
        "types": {
            "note": {
                "description": "Research note",
                "required": ["title"],
                "optional": ["source_ref"],
                "subdirectory": "notes/",
            },
            "event": {
                "description": "Timeline event",
                "required": ["title", "date"],
                "optional": ["importance"],
                "subdirectory": "events/",
            },
            "person": {
                "description": "Person profile",
                "required": ["title"],
                "optional": ["role", "affiliation"],
                "subdirectory": "people/",
            },
            "organization": {
                "description": "Organization profile",
                "required": ["title"],
                "optional": ["org_type", "jurisdiction"],
                "subdirectory": "organizations/",
            },
        },
        "policies": {},
        "validation": {"enforce": False},
        "directories": ["notes", "events", "people", "organizations"],
    },
    "intellectual-biography": {
        "name": "my-biography",
        "description": "Intellectual biography knowledge base for mapping a thinker's ideas, writings, and influence",
        "types": {
            "note": {
                "description": "Core concept or idea",
                "required": ["title"],
                "optional": ["source_ref"],
                "subdirectory": "notes/",
            },
            "writing": {
                "description": "Published work by the subject",
                "required": ["title"],
                "optional": ["writing_type", "date", "url"],
                "subdirectory": "writings/",
            },
            "era": {
                "description": "Biographical period",
                "required": ["title"],
                "optional": ["date_range"],
                "subdirectory": "eras/",
            },
            "event": {
                "description": "Key moment or milestone",
                "required": ["title"],
                "optional": ["date", "importance"],
                "subdirectory": "events/",
            },
            "person": {
                "description": "People in the subject's network",
                "required": ["title"],
                "optional": ["role", "affiliation"],
                "subdirectory": "people/",
            },
            "source": {
                "description": "Reference material",
                "required": ["title"],
                "optional": ["source_type", "author", "date", "url"],
                "subdirectory": "sources/",
            },
            "organization": {
                "description": "Institutions involved",
                "required": ["title"],
                "optional": ["org_type", "jurisdiction"],
                "subdirectory": "organizations/",
            },
        },
        "policies": {},
        "validation": {
            "enforce": True,
            "rules": [
                {
                    "field": "writing_type",
                    "enum": ["book", "paper", "talk", "blog-post", "report"],
                },
                {
                    "field": "source_type",
                    "enum": ["book", "paper", "article", "documentary", "interview"],
                },
            ],
        },
        "directories": [
            "notes",
            "writings",
            "eras",
            "events",
            "people",
            "sources",
            "organizations",
        ],
    },
    "movement": {
        "name": "my-movement",
        "description": "Movement knowledge base for mapping a school of thought, its practices, and participants",
        "types": {
            "note": {
                "description": "Core concept or idea",
                "required": ["title"],
                "optional": ["source_ref"],
                "subdirectory": "notes/",
            },
            "writing": {
                "description": "Published work by movement participants",
                "required": ["title"],
                "optional": ["writing_type", "date", "url"],
                "subdirectory": "writings/",
            },
            "era": {
                "description": "Movement period or phase",
                "required": ["title"],
                "optional": ["date_range"],
                "subdirectory": "eras/",
            },
            "event": {
                "description": "Key moment or milestone",
                "required": ["title"],
                "optional": ["date", "importance"],
                "subdirectory": "events/",
            },
            "person": {
                "description": "People in the movement",
                "required": ["title"],
                "optional": ["role", "affiliation"],
                "subdirectory": "people/",
            },
            "source": {
                "description": "Reference material",
                "required": ["title"],
                "optional": ["source_type", "author", "date", "url"],
                "subdirectory": "sources/",
            },
            "organization": {
                "description": "Institutions involved",
                "required": ["title"],
                "optional": ["org_type", "jurisdiction"],
                "subdirectory": "organizations/",
            },
            "practice": {
                "description": "Method, framework, or technique",
                "required": ["title"],
                "optional": ["origin", "status"],
                "subdirectory": "practices/",
                # A field enum, not a rule: a rule applies to every type with
                # the field, and an event's `status` is `confirmed` (#555).
                "fields": {
                    "status": {"type": "select", "options": ["active", "deprecated", "evolved"]},
                },
            },
        },
        "policies": {},
        "validation": {
            "enforce": True,
            "rules": [
                {
                    "field": "writing_type",
                    "enum": ["book", "paper", "talk", "blog-post", "report"],
                },
                {
                    "field": "source_type",
                    "enum": ["book", "paper", "article", "documentary", "interview"],
                },
            ],
        },
        "directories": [
            "notes",
            "writings",
            "eras",
            "events",
            "people",
            "sources",
            "organizations",
            "practices",
        ],
    },
    "empty": {
        "name": "my-kb",
        "description": "Empty knowledge base",
        "types": {},
        "policies": {},
        "validation": {"enforce": False},
        "directories": [],
    },
}


def init_kb(
    template: str = typer.Option(
        ...,
        "--template",
        "-t",
        help="Template: software, zettelkasten, research, intellectual-biography, movement, empty",
    ),
    path: Path = typer.Option(..., "--path", "-p", help="Directory for the new KB"),
    name: str | None = typer.Option(
        None, "--name", "-n", help="KB name (defaults to directory name)"
    ),
    no_examples: bool = typer.Option(False, "--no-examples", help="Skip creating example entries"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Custom schema YAML file to use as preset override"
    ),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Initialize a new knowledge base from a template.

    Creates the directory structure, kb.yaml, registers the KB in config,
    and runs initial indexing. Zero interactive prompts.
    """
    from ..config import KBConfig, load_config, save_config
    from ..storage.index import IndexManager
    from ..utils.yaml import dump_yaml_file, load_yaml_file
    from .context import open_index_db

    path = path.expanduser().resolve()
    kb_name = name or path.name

    # Idempotency: if kb.yaml exists, warn and return
    kb_yaml_path = path / "kb.yaml"
    if kb_yaml_path.exists():
        result = {
            "status": "exists",
            "name": kb_name,
            "path": str(path),
            "message": "kb.yaml already exists",
        }
        formatted = _format_output(result, output_format)
        if formatted is not None:
            typer.echo(formatted)
        else:
            console.print(f"[yellow]Warning:[/yellow] {path}/kb.yaml already exists. Skipping.")
        return

    # Resolve preset: try plugin presets first, fall back to built-in
    preset = None
    if schema_file:
        if not schema_file.exists():
            from ..utils.errors import cli_error

            cli_error(
                f"Schema file not found: {schema_file}",
                error_code="NOT_FOUND",
                suggestion="check the --schema path",
            )
        preset = load_yaml_file(schema_file)
    else:
        try:
            from ..plugins import get_registry

            plugin_presets = get_registry().get_all_kb_presets()
            if template in plugin_presets:
                preset = plugin_presets[template]
        except Exception:
            logger.warning(
                "Failed to load plugin presets for template '%s'", template, exc_info=True
            )

        if preset is None:
            if template not in BUILTIN_TEMPLATES:
                available = ", ".join(sorted(BUILTIN_TEMPLATES.keys()))
                console.print(
                    f"[red]Error:[/red] Unknown template '{template}'. Available: {available}"
                )
                raise typer.Exit(1)
            preset = BUILTIN_TEMPLATES[template]

    # Create directory structure
    path.mkdir(parents=True, exist_ok=True)
    for subdir in preset.get("directories", []):
        (path / subdir).mkdir(parents=True, exist_ok=True)

    # Write kb.yaml
    kb_yaml_data = {
        "name": kb_name,
        "description": preset.get("description", ""),
        "types": preset.get("types", {}),
        "policies": preset.get("policies", {}),
        "validation": preset.get("validation", {}),
    }
    dump_yaml_file(kb_yaml_data, kb_yaml_path)

    # Register KB in config
    config = load_config()
    if config.get_kb(kb_name):
        # Already registered, skip
        pass
    else:
        kb_config = KBConfig(name=kb_name, path=path, kb_type="generic")
        kb_config.load_kb_yaml()
        config.add_kb(kb_config)
        save_config(config)

    # Index the KB
    entries_indexed = 0
    try:
        config = load_config()  # reload to get the newly added KB
        db = open_index_db(config)
        index_mgr = IndexManager(db, config)
        entries_indexed = index_mgr.index_kb(kb_name)
        db.close()
    except Exception:
        logger.warning("Initial indexing failed for %s", kb_name, exc_info=True)

    # Result
    type_names = list(preset.get("types", {}).keys())
    result = {
        "status": "created",
        "name": kb_name,
        "path": str(path),
        "template": template,
        "types": type_names,
        "entries_indexed": entries_indexed,
    }

    formatted = _format_output(result, output_format)
    if formatted is not None:
        typer.echo(formatted)
    else:
        console.print(f"[green]Created KB:[/green] {kb_name}")
        console.print(f"  Path: {path}")
        console.print(f"  Template: {template}")
        if type_names:
            console.print(f"  Types: {', '.join(type_names)}")
        console.print(f"  Entries indexed: {entries_indexed}")
