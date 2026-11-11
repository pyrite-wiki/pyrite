"""Board configuration for kanban-style views."""

from pathlib import Path

DEFAULT_BOARD_CONFIG = {
    "lanes": [
        {"name": "Backlog", "statuses": ["proposed", "planned"]},
        {"name": "Ready", "statuses": ["accepted"]},
        {"name": "In Progress", "statuses": ["in_progress"], "wip_limit": 5},
        {"name": "Review", "statuses": ["review"], "wip_limit": 3},
        {"name": "Done", "statuses": ["done"]},
    ],
    "wip_policy": "warn",
    "gates": {
        "in_progress": {
            "name": "Definition of Ready",
            "policy": "warn",
            "criteria": [
                {"text": "Problem statement is clear and specific", "type": "judgment"},
                {
                    "text": "Acceptance criteria or expected outcome defined",
                    "type": "judgment",
                    "hint": "Add an ## Acceptance Criteria section to the item body",
                },
                {
                    "text": "Impacted code areas identified",
                    "type": "judgment",
                    "hint": "Add an ## Impacted Files section or link to components",
                },
                {
                    "text": "Architectural approach decided (if applicable)",
                    "type": "judgment",
                    "hint": "Link to an ADR or add an ## Approach section",
                },
                {
                    "text": "Effort estimated",
                    "checker": "has_field",
                    "params": {"field": "effort"},
                },
                {"text": "Not oversized (XL+) without subtasks", "checker": "not_oversized"},
                {"text": "No unresolved blockers", "checker": "no_open_blockers"},
            ],
        },
        "done": {
            "name": "Definition of Done",
            "policy": "warn",
            "criteria": [
                {"text": "Tests passing", "type": "agent_responsibility"},
                {"text": "KB docs updated to reflect changes", "type": "judgment"},
                {
                    "text": "Backlog item updated with implementation notes",
                    "type": "judgment",
                    "hint": "Add an ## Implementation Notes section",
                },
                {
                    "text": "Code ready to commit (clean working tree)",
                    "type": "agent_responsibility",
                },
            ],
        },
    },
}


def load_board_config(kb_path: Path) -> dict:
    """Load board config from board.yaml in KB root, falling back to defaults.

    Read only inside the KB: a board.yaml that is a link out of it, or a file
    with more than one hard link, raises ``KBFileReadOutsideRootError``
    (``OUTSIDE_KB_ROOT``); its lanes are not the KB's to show."""
    board_file = kb_path / "board.yaml"
    if board_file.exists():
        from pyrite.utils.yaml import load_yaml_file

        return load_yaml_file(board_file, kb_root=kb_path)
    return DEFAULT_BOARD_CONFIG.copy()
