"""Board configuration for kanban-style views."""

import copy
import logging
from contextlib import contextmanager
from pathlib import Path

from pyrite.config import KBConfig
from pyrite.exceptions import ValidationError

logger = logging.getLogger(__name__)

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
#: ``no_open_blockers`` reads the KB's index, not the file, so ``_evaluate_gate``
#: runs it outside ``evaluating``: a failed lookup keeps its own error, never
#: the board's refusal, which is for a cause in board.yaml's content (#1585).
PLUGIN_CHECKERS = frozenset({"no_open_blockers"})
#: The keys Pyrite reads at each level. Any other key is refused: a misspelt
#: ``gates`` or ``wip_limit`` would otherwise be a gate or limit that is
#: declared and never evaluated.
TOP_KEYS = ("lanes", "wip_policy", "gates")
LANE_KEYS = ("name", "statuses", "wip_limit")
GATE_KEYS = ("name", "policy", "criteria")
CRITERION_KEYS = ("text", "type", "checker", "params", "hint")
#: The keys a criterion of each type reads. ``judgment`` and
#: ``agent_responsibility`` are never run, so ``checker`` and ``params`` on one
#: would be a key Pyrite does not read: refused when the key is present,
#: whatever its value (#1586).
CRITERION_KEYS_BY_TYPE = {
    "checker": CRITERION_KEYS,
    "judgment": ("text", "type", "hint"),
    "agent_responsibility": ("text", "type", "hint"),
}


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
def _board_refusal():
    """The one place where "could not load or evaluate the board" becomes the
    one refusal, ``BoardConfigError`` (kb/design.md principle 8), for the
    file: its read and validation. A criterion that cannot be evaluated is
    ``_criterion_refusal``'s. Nothing it raises carries the cause's text."""
    from ruamel.yaml.error import YAMLError

    from pyrite.exceptions import KBUnreadableError, YamlLimitError
    from pyrite.utils.kb_files import KBFileOutsideRootError

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


#: What each named checker reads from ``params``: the parameter, and its kind
#: ("text": a non-empty string; "names": a non-empty list of non-empty
#: strings). Each is required, as the checker raises without it. Every checker
#: may also take ``rubric_text`` (text), which a gate does not show. A checker
#: not listed reads no parameter; ``test_every_checker_that_takes_params_is_declared``
#: fails when a checker that needs one is added without an entry here.
#: It does not catch a new optional parameter or a changed kind: a checker that
#: answered "holds" for a value of the wrong kind would load unchecked.
CHECKER_PARAMS: dict[str, dict[str, str]] = {
    "has_field": {"field": "text"},
    "has_any_field": {"fields": "names"},
    "body_has_section": {"heading": "text"},
    "body_has_pattern": {"pattern": "text"},
}
OPTIONAL_PARAMS = {"rubric_text": "text"}

_KIND_TEXT = {"text": "must be text", "names": "must be a list of names"}


def _check_params(where: str, checker: str, params: dict | None) -> None:
    """``params`` against what ``checker`` declares it reads (CHECKER_PARAMS)."""
    params = params or {}
    declared = {**CHECKER_PARAMS.get(checker, {}), **OPTIONAL_PARAMS}
    for key in CHECKER_PARAMS.get(checker, {}):
        if key not in params:
            raise BoardConfigError(f"{where}: checker '{checker}' needs '{key}' in 'params'")
    for key, kind in declared.items():
        if key not in params:
            continue
        value = params[key]
        ok = (
            isinstance(value, str) and value.strip() != ""
            if kind == "text"
            else isinstance(value, list)
            and value != []
            and all(isinstance(v, str) and v.strip() != "" for v in value)
        )
        if not ok:
            raise BoardConfigError(f"{where}: '{key}' {_KIND_TEXT[kind]}")


@contextmanager
def _criterion_refusal(where: str, *, on_an_item: bool):
    """A criterion that could not be evaluated, at load (its dry run) or on one
    item: the board's one refusal, never recorded as passed (#1538).

    A checker's own refusal (``CheckerConfigError``: a pattern that does not
    compile, or that takes too long on this item's body) is named for what it
    is. Anything else is unexpected, so it may be Pyrite's fault rather than
    the file's: its traceback goes to the server log and the refusal says the
    criterion could not be evaluated, not that ``params`` is wrong. Neither
    shows the cause's text, which can quote the file."""
    from pyrite.services.rubric_checkers import CheckerConfigError

    try:
        yield
    except BoardConfigError:
        raise
    except MemoryError:
        raise
    except CheckerConfigError:
        if on_an_item:
            raise BoardConfigError(f"{where} could not be evaluated on this item") from None
        raise BoardConfigError(f"{where}: 'params' cannot be used by its checker") from None
    except Exception:
        logger.exception("%s %s could not be evaluated", BOARD_FILE, where)
        place = " on this item" if on_an_item else ""
        raise BoardConfigError(f"{where} could not be evaluated{place}") from None


def gate_checker(name: str):
    """The function a gate runs for checker ``name``. ``body_has_pattern``
    without QA's skip: after a timeout QA skips that pattern for a while and
    answers "no finding", which a gate would read as a pass; the gate matches
    every time, within the same time limit."""
    from pyrite.services.rubric_checkers import (
        NAMED_CHECKERS,
        check_body_has_pattern_every_time,
    )

    if name == "body_has_pattern":
        return check_body_has_pattern_every_time
    return NAMED_CHECKERS[name]


def evaluating(where: str):
    """Around a criterion's run on one item in ``_evaluate_gate``."""
    return _criterion_refusal(where, on_an_item=True)


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
    keys = CRITERION_KEYS_BY_TYPE[kind]
    if _unknown_key(criterion, keys):
        raise BoardConfigError(f"{where} has a key Pyrite does not read ({', '.join(keys)})")
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
    _check_params(where, checker, params)
    if checker in NAMED_CHECKERS:
        with _criterion_refusal(where, on_an_item=False):
            # Dry-run against the builder's entry with no row (#1587): a
            # parameterised checker checks its params before it reads the
            # entry (a pattern compiles), so a file whose params a checker
            # cannot use is refused by every tool, not only by those that run
            # the gate.
            from pyrite.services.checker_entry import empty_checker_entry

            gate_checker(checker)(empty_checker_entry(), None, params)
