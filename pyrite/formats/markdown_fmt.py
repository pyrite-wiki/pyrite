"""Markdown format serializer."""

from typing import Any

from .tabular import projected_markdown, projected_rows


def markdown_serialize(data: Any, **kwargs) -> str:
    """Serialize data to Markdown.

    For single entries: renders frontmatter + body.
    For lists: renders each item as a section.
    For search results: renders as a list with snippets.
    """
    if isinstance(data, dict):
        fields = kwargs.get("fields")
        rows = projected_rows(data, single=kwargs.get("single_entry", False)) if fields else None
        if rows is not None:
            return projected_markdown(rows, fields)
        if "id" in data and "title" in data:
            return _entry_to_markdown(data)
        if "entries" in data:
            return _entry_list_to_markdown(data)
        if "results" in data:
            return _search_results_to_markdown(data)
        if "events" in data:
            return _timeline_to_markdown(data)
        if "kbs" in data:
            return _kbs_to_markdown(data)
        if "tags" in data:
            return _tags_to_markdown(data)
    return str(data)


def _entry_to_markdown(entry: dict) -> str:
    """Single entry as markdown with frontmatter."""
    from copy import deepcopy

    from ruamel.yaml.comments import CommentedSeq

    from ..utils.yaml import dump_yaml

    header = {}
    for key in ("id", "title", "type", "kb_name", "date", "importance", "tags"):
        value = entry.get(key)
        if key == "type" and value is None:
            value = entry.get("entry_type")
        if value is not None and value != "" and value != []:
            # dump_yaml quotes YAML1.1-ambiguous strings recursively; copy so
            # rendering cannot change the caller's nested tag list.
            value = deepcopy(value)
            if isinstance(value, list):
                value = CommentedSeq(value)
                value.fa.set_flow_style()
            header[key] = value
    body = entry.get("body") or ""
    return "---\n" + dump_yaml(header) + "\n---\n\n" + body


def _entry_list_to_markdown(data: dict) -> str:
    entries = data.get("entries", [])
    lines = [f"# Entries ({data.get('total', len(entries))} total)", ""]
    for e in entries:
        if isinstance(e, dict):
            lines.append(f"## {e.get('title', e.get('id', ''))}")
            if e.get("date"):
                lines.append(f"Date: {e['date']}")
            if e.get("tags"):
                tags = e["tags"] if isinstance(e["tags"], list) else [e["tags"]]
                lines.append(f"Tags: {', '.join(tags)}")
            lines.append("")
    return "\n".join(lines)


def _search_results_to_markdown(data: dict) -> str:
    results = data.get("results", [])
    lines = [
        f"# Search: {data.get('query', '')} ({data.get('count', len(results))} results)",
        "",
    ]
    for r in results:
        if isinstance(r, dict):
            lines.append(f"- **{r.get('title', r.get('id', ''))}** ({r.get('kb_name', '')})")
            if r.get("snippet"):
                lines.append(f"  {r['snippet']}")
    return "\n".join(lines)


def _timeline_to_markdown(data: dict) -> str:
    events = data.get("events", [])
    lines = [f"# Timeline ({data.get('count', len(events))} events)", ""]
    for e in events:
        if isinstance(e, dict):
            lines.append(f"- **{e.get('date', '')}** -- {e.get('title', '')}")
    return "\n".join(lines)


def _kbs_to_markdown(data: dict) -> str:
    kbs = data.get("kbs", [])
    lines = ["# Knowledge Bases", ""]
    for kb in kbs:
        if isinstance(kb, dict):
            lines.append(
                f"- **{kb.get('name', '')}** ({kb.get('type', '')}) -- "
                f"{kb.get('entries', 0)} entries"
            )
    return "\n".join(lines)


def _tags_to_markdown(data: dict) -> str:
    tags = data.get("tags", [])
    lines = [f"# Tags ({len(tags)} tags)", ""]
    for t in tags:
        if isinstance(t, dict):
            lines.append(f"- {t.get('tag', t.get('name', ''))} ({t.get('count', 0)})")
    return "\n".join(lines)
