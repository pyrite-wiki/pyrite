"""Board configuration for kanban-style views."""

import copy
from pathlib import Path

from pyrite.config import KBConfig
from pyrite.exceptions import ValidationError

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


BOARD_FILE = "board.yaml"

POLICIES = ("warn", "enforce")
CRITERION_TYPES = ("checker", "judgment", "agent_responsibility")
#: Checkers ``_evaluate_gate`` runs itself rather than through NAMED_CHECKERS.
PLUGIN_CHECKERS = frozenset({"no_open_blockers"})


class BoardConfigError(ValidationError):
    """A ``board.yaml`` that cannot be turned into a board config (#1538).

    A gate or limit the file declares cannot be evaluated, so every tool that
    decides or reports one refuses rather than answer as if it had run. The
    text is Pyrite's own: the file name and the kind of problem, with a line
    or a position; never a value from the file or the YAML parser's message,
    so it is safe on every surface."""

    def __init__(self, problem: str):
        super().__init__(
            f"{BOARD_FILE} {problem}",
            suggestion=f"Fix {BOARD_FILE} in the KB's root, or remove it to use the defaults.",
        )


def default_board_config() -> dict:
    """The board config of a KB with no ``board.yaml``: a copy to change freely."""
    return copy.deepcopy(DEFAULT_BOARD_CONFIG)


def load_board_config(kb: KBConfig) -> dict:
    """The board config of a resolved KB: its ``board.yaml``, validated, or the
    defaults when it has none. The one read of the file (#1538).

    ``kb`` is the ``KBConfig`` the one KB resolver returned; the file is read
    from that KB's root and from nowhere else, so a bare path is a
    ``TypeError``. A file that is there and cannot be loaded raises
    ``BoardConfigError``. The KB file reader's own refusals propagate with
    their own codes: over the YAML limits (``YamlLimitError``), unreadable
    (``KB_UNREADABLE``), a link out of the KB or a file with more than one
    hard link (``OUTSIDE_KB_ROOT``).

    A file replaces the defaults whole: absent ``lanes`` is no lanes, absent
    ``gates`` no gates, absent ``wip_policy`` is ``warn``."""
    if not isinstance(kb, KBConfig):
        raise TypeError("load_board_config takes the KBConfig the KB resolver returned")
    from ruamel.yaml.error import YAMLError

    from pyrite.utils.kb_files import KBFiles
    from pyrite.utils.yaml import load_yaml_file

    kb_root = Path(kb.path)
    board_file = kb_root / BOARD_FILE
    # The KB's own probe: a link out is there whatever it points at, and the
    # read below refuses it, so the answer never says what lies outside.
    if not KBFiles(kb_root).present(board_file):
        return default_board_config()
    try:
        data = load_yaml_file(board_file, kb_root=kb_root)
    except UnicodeDecodeError:
        raise BoardConfigError("is not UTF-8 text") from None
    except YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise BoardConfigError(f"does not parse as YAML{where}") from None
    return _validated(data)


def _validated(data: object) -> dict:
    """``data`` as a config the gate code can use, or ``BoardConfigError``."""
    if not isinstance(data, dict):
        raise BoardConfigError("must be a mapping of lanes, wip_policy and gates")
    lanes = data.get("lanes", [])
    if not isinstance(lanes, list):
        raise BoardConfigError("'lanes' must be a list")
    for n, lane in enumerate(lanes, 1):
        _check_lane(n, lane)
    wip_policy = data.get("wip_policy", "warn")
    if wip_policy not in POLICIES:
        raise BoardConfigError("'wip_policy' must be 'warn' or 'enforce'")
    gates = data.get("gates", {})
    if not isinstance(gates, dict):
        raise BoardConfigError("'gates' must be a mapping of status to gate")
    for n, gate in enumerate(gates.values(), 1):
        _check_gate(n, gate)
    return {**data, "lanes": lanes, "wip_policy": wip_policy, "gates": gates}


def _check_lane(n: int, lane: object) -> None:
    if not isinstance(lane, dict):
        raise BoardConfigError(f"lane {n} must be a mapping with 'name' and 'statuses'")
    if not isinstance(lane.get("name"), str):
        raise BoardConfigError(f"lane {n} has no 'name'")
    statuses = lane.get("statuses")
    if not isinstance(statuses, list) or not all(isinstance(s, str) for s in statuses):
        raise BoardConfigError(f"lane {n} has no 'statuses' list")
    limit = lane.get("wip_limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise BoardConfigError(f"lane {n}: 'wip_limit' must be a whole number, 0 or more")


def _check_gate(n: int, gate: object) -> None:
    if not isinstance(gate, dict):
        raise BoardConfigError(f"gate {n} must be a mapping")
    if gate.get("policy", "warn") not in POLICIES:
        raise BoardConfigError(f"gate {n}: 'policy' must be 'warn' or 'enforce'")
    criteria = gate.get("criteria", [])
    if not isinstance(criteria, list):
        raise BoardConfigError(f"gate {n}: 'criteria' must be a list")
    for m, criterion in enumerate(criteria, 1):
        check_criterion(f"gate {n}, criterion {m}", criterion)


def check_criterion(where: str, criterion: object) -> None:
    """Refuse a gate criterion that cannot be evaluated. ``_evaluate_gate``
    asks this too, so a criterion it cannot run is never recorded as passed."""
    from pyrite.services.rubric_checkers import NAMED_CHECKERS

    if not isinstance(criterion, dict):
        raise BoardConfigError(f"{where} must be a mapping")
    kind = criterion.get("type", "checker")
    if kind not in CRITERION_TYPES:
        raise BoardConfigError(
            f"{where}: 'type' must be one of {', '.join(repr(t) for t in CRITERION_TYPES)}"
        )
    if kind != "checker":
        return
    checker = criterion.get("checker")
    if not checker:
        raise BoardConfigError(f"{where} names no checker")
    if not isinstance(checker, str) or (
        checker not in NAMED_CHECKERS and checker not in PLUGIN_CHECKERS
    ):
        known = ", ".join(sorted({*NAMED_CHECKERS, *PLUGIN_CHECKERS}))
        raise BoardConfigError(f"{where} names a checker Pyrite does not have (known: {known})")
    params = criterion.get("params")
    if params is not None and not isinstance(params, dict):
        raise BoardConfigError(f"{where}: 'params' must be a mapping")
