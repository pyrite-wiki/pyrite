"""Board configuration for kanban-style views."""

import copy
from contextlib import contextmanager
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
#: The keys Pyrite reads at each level. Any other key is refused: a misspelt
#: ``gates`` or ``wip_limit`` would otherwise be a gate or limit that is
#: declared and never evaluated.
TOP_KEYS = ("lanes", "wip_policy", "gates")
LANE_KEYS = ("name", "statuses", "wip_limit")
GATE_KEYS = ("name", "policy", "criteria")
CRITERION_KEYS = ("text", "type", "checker", "params", "hint")


class BoardConfigError(ValidationError):
    """A ``board.yaml`` that cannot be loaded or evaluated (#1538): the one
    refusal every tool that reads the board answers.

    A gate or limit the file declares cannot be evaluated, so every tool that
    decides or reports one refuses rather than answer as if it had run. The
    text is Pyrite's own: the file name and the kind of problem, with a
    position in the file's structure (lane 2, gate 1 criterion 3); never a
    value from the file, nor the YAML parser's or a checker's message, so it
    is safe on every surface. ``VALIDATION_FAILED`` unless the KB file reader
    refused the read, whose code it keeps (``OUTSIDE_KB_ROOT``,
    ``KB_UNREADABLE``, ``INVALID_FRONTMATTER`` for the YAML limits)."""

    def __init__(self, problem: str, *, error_code: str | None = None):
        super().__init__(
            f"{BOARD_FILE} {problem}",
            suggestion=f"Fix {BOARD_FILE} in the KB's root, or remove it to use the defaults.",
        )
        if error_code is not None:
            self.error_code = error_code


@contextmanager
def _board_refusal(where: str = ""):
    """The one place where "could not load or evaluate the board" becomes the
    one refusal, ``BoardConfigError`` (kb/design.md principle 8). Used around
    the file's read and validation, a criterion's dry run, and a checker's run
    in ``_evaluate_gate``. Nothing it raises carries the cause's text."""
    from ruamel.yaml.error import YAMLError

    from pyrite.exceptions import KBUnreadableError, YamlLimitError
    from pyrite.services.rubric_checkers import CheckerConfigError
    from pyrite.utils.kb_files import KBFileOutsideRootError

    at = f"{where}: " if where else ""
    try:
        yield
    except BoardConfigError:
        raise
    except KBFileOutsideRootError as e:
        raise BoardConfigError(
            "is not read: a link on its path leaves the KB, or it has more than one hard link",
            error_code=e.error_code,
        ) from None
    except KBUnreadableError as e:
        raise BoardConfigError(
            "cannot be read: the operating system refused", error_code=e.error_code
        ) from None
    except YamlLimitError as e:
        raise BoardConfigError(
            "is over Pyrite's YAML limits (too many aliases, or too large once expanded)",
            error_code=e.error_code,
        ) from None
    except CheckerConfigError:
        # Its text can quote the file (a pattern): the position only.
        raise BoardConfigError(f"{at}'params' cannot be used by its checker") from None
    except UnicodeDecodeError:
        raise BoardConfigError("is not UTF-8 text") from None
    except YAMLError:
        # Not the parser's message, nor its position: both come from the
        # exception, and its message quotes the file (ADR-0052 N3).
        raise BoardConfigError("does not parse as YAML") from None
    except RecursionError:
        raise BoardConfigError("is nested too deeply to load") from None
    except OSError:
        # A directory named board.yaml, or the file gone between the KB's
        # presence probe and the read.
        raise BoardConfigError("is not a file Pyrite can read") from None


def default_board_config() -> dict:
    """The board config of a KB with no ``board.yaml``: a copy to change freely."""
    return copy.deepcopy(DEFAULT_BOARD_CONFIG)


def load_board_config(kb: KBConfig) -> dict:
    """The board config of a resolved KB: its ``board.yaml``, validated, or the
    defaults when it has none. The one read of the file (#1538).

    ``kb`` is the ``KBConfig`` the one KB resolver returned; the file is read
    from that KB's root and from nowhere else, so a bare path is a
    ``TypeError``. A file that is there and cannot be loaded raises
    ``BoardConfigError``, whatever failed underneath (``_board_refusal``).

    A file replaces the defaults whole: absent ``lanes`` is no lanes, absent
    ``gates`` no gates, absent ``wip_policy`` is ``warn``."""
    if not isinstance(kb, KBConfig):
        raise TypeError("load_board_config takes the KBConfig the KB resolver returned")
    from pyrite.utils.kb_files import KBFiles
    from pyrite.utils.yaml import load_yaml_file

    kb_path = Path(kb.path)
    board_file = kb_path / BOARD_FILE
    with _board_refusal():
        # The KB's own probe: a link out is there whatever it points at, and
        # the read below refuses it, so the answer never says what lies outside.
        if not KBFiles(kb_path).present(board_file):
            return default_board_config()
        data = load_yaml_file(board_file, kb_root=kb_path)
        return _validated(data)


def _unknown_key(mapping: dict, known: tuple[str, ...]) -> bool:
    return any(key not in known for key in mapping)


def _validated(data: object) -> dict:
    """``data`` as a config the gate code can use, or ``BoardConfigError``."""
    from .workflows import BACKLOG_WORKFLOW

    if not isinstance(data, dict):
        raise BoardConfigError("must be a mapping of lanes, wip_policy and gates")
    if _unknown_key(data, TOP_KEYS):
        raise BoardConfigError(f"has a key Pyrite does not read (it reads: {', '.join(TOP_KEYS)})")
    lanes = data.get("lanes", [])
    if not isinstance(lanes, list):
        raise BoardConfigError("'lanes' must be a list")
    names: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for n, lane in enumerate(lanes, 1):
        _check_lane(n, lane)
        if lane["name"] in names:
            raise BoardConfigError(f"lanes {names[lane['name']]} and {n} have the same name")
        names[lane["name"]] = n
        for status in lane["statuses"]:
            if status in statuses and statuses[status] != n:
                raise BoardConfigError(
                    f"lanes {statuses[status]} and {n} list the same status: "
                    "a status is in one lane, so its WIP limit is one"
                )
            statuses[status] = n
    wip_policy = data.get("wip_policy", "warn")
    if wip_policy not in POLICIES:
        raise BoardConfigError("'wip_policy' must be 'warn' or 'enforce'")
    gates = data.get("gates", {})
    if not isinstance(gates, dict):
        raise BoardConfigError("'gates' must be a mapping of status to gate")
    known_statuses = BACKLOG_WORKFLOW["states"]
    for n, (status, gate) in enumerate(gates.items(), 1):
        if status not in known_statuses:
            raise BoardConfigError(
                f"gate {n} is keyed by something that is not a backlog status, so it would "
                f"never run (statuses: {', '.join(known_statuses)})"
            )
        _check_gate(n, gate)
    return {**data, "lanes": lanes, "wip_policy": wip_policy, "gates": gates}


def _check_lane(n: int, lane: object) -> None:
    if not isinstance(lane, dict):
        raise BoardConfigError(f"lane {n} must be a mapping with 'name' and 'statuses'")
    if _unknown_key(lane, LANE_KEYS):
        raise BoardConfigError(f"lane {n} has a key Pyrite does not read ({', '.join(LANE_KEYS)})")
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
    if _unknown_key(gate, GATE_KEYS):
        raise BoardConfigError(f"gate {n} has a key Pyrite does not read ({', '.join(GATE_KEYS)})")
    if not isinstance(gate.get("name", ""), str):
        raise BoardConfigError(f"gate {n}: 'name' must be text")
    if gate.get("policy", "warn") not in POLICIES:
        raise BoardConfigError(f"gate {n}: 'policy' must be 'warn' or 'enforce'")
    criteria = gate.get("criteria", [])
    if not isinstance(criteria, list):
        raise BoardConfigError(f"gate {n}: 'criteria' must be a list")
    for m, criterion in enumerate(criteria, 1):
        check_criterion(f"gate {n}, criterion {m}", criterion)


#: The entry a criterion is dry-run against at load: a parameterised checker
#: checks its params before it reads the entry, so a file whose params a
#: checker cannot use is refused by every tool, not only by those that run it.
_DRY_RUN_ENTRY = {"id": "", "kb_name": "", "title": "", "body": "", "metadata": {}, "_links": []}


def check_criterion(where: str, criterion: object) -> None:
    """Refuse a gate criterion that cannot be evaluated. ``_evaluate_gate``
    asks this too, so a criterion it cannot run is never recorded as passed."""
    from pyrite.services.rubric_checkers import NAMED_CHECKERS

    if not isinstance(criterion, dict):
        raise BoardConfigError(f"{where} must be a mapping")
    if _unknown_key(criterion, CRITERION_KEYS):
        raise BoardConfigError(
            f"{where} has a key Pyrite does not read ({', '.join(CRITERION_KEYS)})"
        )
    for field in ("text", "hint"):
        if not isinstance(criterion.get(field, ""), str):
            raise BoardConfigError(f"{where}: '{field}' must be text")
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
    if checker in NAMED_CHECKERS:
        with _board_refusal(where):
            NAMED_CHECKERS[checker](dict(_DRY_RUN_ENTRY), None, params)


def run_checker(where: str, checker, entry: dict, params) -> dict | None:
    """Run a gate's checker on one entry; a checker that cannot run on it (a
    pattern that takes too long on this body) is the board's refusal."""
    with _board_refusal(where):
        return checker(entry, None, params)
