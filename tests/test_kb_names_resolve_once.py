"""A KB name resolves once, at the boundary: the ratchet and the walk (T3-1, #1530).

``kb/design.md`` principle 8 (each rule lives in one place). Epic #1530:
``services/kb_resolver.resolve_kb`` is the only function that turns a caller's
KB string into a KB or one refusal; code behind the boundary takes the handle
(``ResolvedKB``). ``PyriteConfig.get_kb`` is a plain cache read: it knows only
the config and the registry cache, never the live ``kb`` table (#925), and each
caller decides for itself what ``None`` means (a bare ``return``, "no schema",
a fallback, a raise).

Two properties are pinned here.

1. **The ratchet.** A ``.get_kb(`` call outside the registry module fails this
   test. The registry module is ``pyrite/config.py``,
   ``pyrite/services/kb_resolver.py`` and
   ``pyrite/services/kb_registry_service.py``. Three call shapes are seen: the
   attribute call ``x.get_kb(n)``, a bound-method alias (``f = x.get_kb``, or
   the method passed as a value), and ``getattr(x, "get_kb")``. The baseline is
   per file (``BASELINE``): a count above it is a new call, a count below it is
   a conversion that must lower its line (the test says which), so two honest
   pull requests never edit one size line (#1608). T3-3 and later slices lower
   their group's lines, to zero.

   How the baseline was counted, on ``dev`` at 96bb4936:

       git grep -c "\\.get_kb(" -- 'pyrite/*.py' 'extensions/*/src/*.py'

   is 104 calls in 44 files. The registry module holds 12 of them
   (``config.py`` 2, ``kb_resolver.py`` 1, ``kb_registry_service.py`` 9), so
   **92 sites in 41 files** stand outside it, which is the sum of
   ``BASELINE``. (Epic #1530 counted 110 and 98 on ``d371260b``; the tree has
   moved since: dev's count is the one pinned.) The scan is the AST, not the
   text, so a comment, a docstring or ``def get_kb`` is never a site; the
   ``.get_kb(`` text count and the AST count agree today, and the
   ``test_the_ast_count_is_the_text_count`` check keeps them honest.

2. **The walk.** For one representative public entry point per site group, a
   name the resolver finds is found by the code behind it, and a name the
   resolver refuses is refused as ``KBNotFoundError`` (``KB_NOT_FOUND``), never
   a clean empty result. Shapes: a configured KB; a KB registered after the
   process loaded its config (the live-table case of #925); a KB that exists
   only as an ephemeral database row; an unknown name; a shortname (link syntax
   only, refused as unknown). Every probe is written from the property, not from
   the code under test. The failures that exist today are strict expected
   failures in ``TODAY_FAILS``, one entry per (site group, shape), each naming
   its group and slice: when a slice converts its group, its rows turn XPASS and
   fail the run until the entry is deleted. CLI commands load the config afresh
   per process and resolve with the database open, so the probes for the CLI and
   the extension group find no failing shape today; they pass, and keep it so.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.exceptions import KBNotFoundError
from pyrite.services import kb_resolver
from pyrite.storage.database import PyriteDB

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]

#: The registry module: where "is there a KB called X" is asked by design.
REGISTRY_MODULE = frozenset(
    {
        "pyrite/config.py",
        "pyrite/services/kb_resolver.py",
        "pyrite/services/kb_registry_service.py",
    }
)

# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------


def get_kb_sites(source: str) -> list[tuple[str, int]]:
    """Every `get_kb` use in ``source``: ``(shape, line)``.

    Shapes: ``call`` (``x.get_kb(...)``), ``alias`` (``x.get_kb`` used as a
    value: assigned, passed, returned) and ``getattr`` (``getattr(x,
    "get_kb")``). Definitions, other names (``require_kb``, ``get_kb_config``,
    ``_get_kb``), strings and comments are not sites.
    """
    tree = ast.parse(source)
    called: set[int] = set()
    sites: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get_kb":
                called.add(id(func))
                sites.append(("call", node.lineno))
            elif (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "get_kb"
            ):
                sites.append(("getattr", node.lineno))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "get_kb" and id(node) not in called:
            sites.append(("alias", node.lineno))
    return sorted(sites, key=lambda s: s[1])


def violations(rel_path: str, source: str) -> list[tuple[str, int]]:
    """The sites in ``source`` that count against the baseline: none inside the
    registry module."""
    if rel_path in REGISTRY_MODULE:
        return []
    return get_kb_sites(source)


def _scan_tree() -> dict[str, int]:
    found: Counter[str] = Counter()
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            n = len(violations(rel, path.read_text(encoding="utf-8")))
            if n:
                found[rel] = n
    return dict(found)


#: ``.get_kb(`` sites outside the registry module, per file, on dev at
#: 96bb4936 (see the module docstring for the command). A slice that converts a
#: site lowers its file's number here, or deletes the line at zero: a one-line
#: change. The group comments are the epic's table.
BASELINE: dict[str, int] = {
    # QA services (T3-2)
    "pyrite/services/qa_service.py": 15,
    "pyrite/services/qa_fix_service.py": 3,
    "pyrite/services/qa_analytics_service.py": 1,
    # KBService and the small services (T3-3)
    "pyrite/services/kb_service.py": 8,
    "pyrite/services/worktree_service.py": 2,
    "pyrite/services/version_service.py": 2,
    "pyrite/services/write_target.py": 1,
    "pyrite/services/template_service.py": 1,
    "pyrite/services/task_service.py": 1,
    "pyrite/services/schema_service.py": 1,
    "pyrite/services/review_service.py": 1,
    "pyrite/services/repo_service.py": 1,
    "pyrite/services/kb_names.py": 1,
    "pyrite/services/id_pin_service.py": 1,
    "pyrite/services/ephemeral_service.py": 1,
    "pyrite/services/access_policy.py": 1,
    # The index (T3-4)
    "pyrite/storage/index.py": 6,
    # CLI (T3-5a)
    "pyrite/cli/kb_commands.py": 6,
    "pyrite/admin_cli.py": 6,
    "pyrite/cli/schema_commands.py": 3,
    "pyrite/cli/index_commands.py": 3,
    "pyrite/cli/export_commands.py": 3,
    "pyrite/cli/qa_commands.py": 2,
    "pyrite/cli/context.py": 2,
    "pyrite/cli/__init__.py": 2,
    "pyrite/cli/entry_commands.py": 1,
    "pyrite/cli/ids_commands.py": 1,
    "pyrite/cli/init_command.py": 1,
    "pyrite/cli/protocol_commands.py": 1,
    "pyrite/cli/search_commands.py": 1,
    # Server (T3-5b)
    "pyrite/server/mcp_server.py": 3,
    "pyrite/server/endpoints/entries.py": 2,
    "pyrite/server/endpoints/repos.py": 1,
    "pyrite/server/endpoints/kbs.py": 1,
    "pyrite/server/endpoints/clipper.py": 1,
    # Extensions (#1529, per extension)
    "extensions/software-kb/src/pyrite_software_kb/cli.py": 1,
    "extensions/software-kb/src/pyrite_software_kb/adr_listing.py": 1,
    "extensions/journalism-investigation/src/pyrite_journalism_investigation/plugin.py": 1,
    "extensions/journalism-investigation/src/pyrite_journalism_investigation/promote.py": 1,
    "extensions/zettelkasten/src/pyrite_zettelkasten/cli.py": 1,
}


def test_no_new_get_kb_outside_the_registry_module():
    """A `.get_kb(` outside the registry module, in a file or a count the
    baseline does not allow, fails: resolve once and take the handle."""
    found = _scan_tree()
    grew = {f: (BASELINE.get(f, 0), n) for f, n in sorted(found.items()) if n > BASELINE.get(f, 0)}
    assert not grew, (
        "a new `.get_kb(` outside the registry module (file: allowed -> found): "
        f"{grew}. Resolve the name once at the entry point with "
        "`services.kb_resolver.resolve_kb` (or `PyriteConfig.require_kb`) and pass the "
        "ResolvedKB down; see kb/design.md principle 8 and epic #1530."
    )


def test_a_converted_site_lowers_its_baseline_line():
    """The ratchet only shrinks: a file whose count fell, or that has none left,
    must lower or delete its line in ``BASELINE``."""
    found = _scan_tree()
    shrunk = {
        f: (allowed, found.get(f, 0))
        for f, allowed in sorted(BASELINE.items())
        if found.get(f, 0) < allowed
    }
    assert not shrunk, (
        f"sites were converted; lower these BASELINE lines (file: baseline -> found): {shrunk}"
    )


def test_the_baseline_names_no_registry_module_and_no_missing_file():
    assert not REGISTRY_MODULE & BASELINE.keys()
    missing = [f for f in BASELINE if not (REPO / f).is_file()]
    assert not missing, f"BASELINE names a file that does not exist: {missing}"


def test_the_ast_count_is_the_text_count():
    """The AST scan agrees with the text count named in the docstring."""
    out = subprocess.run(
        ["git", "grep", "-c", r"\.get_kb(", "--", "pyrite/*.py", "extensions/*/src/*.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if out.returncode not in (0, 1):  # not a git checkout (an sdist): nothing to compare
        pytest.skip("git grep unavailable")
    text = {
        line.rsplit(":", 1)[0]: int(line.rsplit(":", 1)[1])
        for line in out.stdout.splitlines()
        if line.rsplit(":", 1)[0] not in REGISTRY_MODULE
    }
    assert _scan_tree() == text


# -- planted violations: the scanner sees each shape ------------------------

PLANTED_CALL = "def f(config, n):\n    return config.get_kb(n)\n"
PLANTED_NESTED_CALL = "class S:\n    def f(self, n):\n        return self.config.get_kb(n) or 1\n"
PLANTED_ALIAS = "def f(config):\n    lookup = config.get_kb\n    return lookup('x')\n"
PLANTED_ALIAS_PASSED = "def f(config, names):\n    return list(map(config.get_kb, names))\n"
PLANTED_GETATTR = "def f(config, n):\n    return getattr(config, 'get_kb')(n)\n"


@pytest.mark.parametrize(
    ("source", "shape"),
    [
        (PLANTED_CALL, "call"),
        (PLANTED_NESTED_CALL, "call"),
        (PLANTED_ALIAS, "alias"),
        (PLANTED_ALIAS_PASSED, "alias"),
        (PLANTED_GETATTR, "getattr"),
    ],
)
def test_the_scanner_sees_each_planted_shape(source, shape):
    assert [s for s, _ in violations("pyrite/services/some_service.py", source)] == [shape]


def test_a_planted_call_in_an_unlisted_file_is_a_growth():
    """A file the baseline does not list, holding one call, is above its allowance."""
    rel = "pyrite/services/never_listed.py"
    assert rel not in BASELINE
    assert len(violations(rel, PLANTED_CALL)) > BASELINE.get(rel, 0)


# -- controls: the allowed shapes do not fire --------------------------------

CONTROL_NOT_SITES = '''
class Registry:
    def get_kb(self, name):
        """A definition: `config.get_kb(name)` in a docstring is text."""
        # config.get_kb(name) in a comment is text too
        return "config.get_kb(name)"

def g(config, db, name):
    config.require_kb(name, db=db)
    config.get_kb_config(name)
    config._get_kb(name)
    config.get_kb_names()
    get_kb(name)  # a bare function is not the registry's method
    return resolve_kb(config, name, readable_kbs=None, db=db)
'''


def test_the_scanner_does_not_fire_on_the_allowed_shapes():
    assert violations("pyrite/services/some_service.py", CONTROL_NOT_SITES) == []


@pytest.mark.parametrize("rel", sorted(REGISTRY_MODULE))
def test_the_registry_module_may_call_get_kb(rel):
    assert violations(rel, PLANTED_CALL + PLANTED_ALIAS + PLANTED_GETATTR) == []


def test_the_registry_module_is_where_the_lookups_are():
    """The allowlist is real: each registry file holds the calls it is exempt for."""
    for rel in sorted(REGISTRY_MODULE):
        assert get_kb_sites((REPO / rel).read_text(encoding="utf-8")), rel


# ---------------------------------------------------------------------------
# The handle's name
# ---------------------------------------------------------------------------


def test_the_resolved_handle_has_one_name():
    """`ResolvedKB` is the name of what `resolve_kb` returns, defined in the
    resolver module and nowhere else, and it is the configured KB itself (no
    new class, no behaviour change)."""
    assert kb_resolver.ResolvedKB is KBConfig
    assert kb_resolver.ResolvedKB.__doc__ is not None
    definers = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                targets = []
                if isinstance(node, ast.Assign):
                    targets = [t for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    targets = [node.target]
                if any(t.id == "ResolvedKB" for t in targets):
                    definers.append(path.relative_to(REPO).as_posix())
    assert definers == ["pyrite/services/kb_resolver.py"]


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

KB_YAML = "name: {name}\ntypes:\n  widget:\n    description: a widget\n    fields: {{}}\n"
ENTRY = "---\nid: n1\ntype: note\ntitle: N1\n---\nbody\n"

SHAPES = ["configured", "added_after_load", "ephemeral", "unknown", "shortname"]
KNOWN_SHAPES = {"configured", "added_after_load", "ephemeral"}


@dataclass
class Subject:
    """What a probe is asked about: the world, the name, and the shape."""

    world: object
    name: str
    shape: str

    @property
    def known(self) -> bool:
        return self.shape in KNOWN_SHAPES

    @property
    def folder(self) -> Path | None:
        return self.world.folders.get(self.name)


def _make_kb_folder(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "kb.yaml").write_text(KB_YAML.format(name=name), encoding="utf-8")
    # Never indexed: a KB the code behind the boundary really sees has an
    # unindexed file for the index health check and for QA to report.
    (path / "n1.md").write_text(ENTRY, encoding="utf-8")


@pytest.fixture
def make_subject(tmp_path):
    """A world whose config was loaded before two KBs were registered by
    another process's connection (``added_after_load``) and as a bare database
    row (``ephemeral``), plus one KB in config.yaml (``configured``, with the
    shortname ``sa``). Closed at teardown."""
    from tests._world import build_world

    worlds = []

    def _make(shape: str, *, rest: bool = False) -> Subject:
        root = tmp_path / f"walk-{len(worlds)}"
        world = build_world(
            root,
            kbs=[
                KBConfig(
                    name="configured-kb",
                    path=root / "configured-kb",
                    kb_type="generic",
                    shortname="sa",
                )
            ],
            rest=rest,
        )
        worlds.append(world)
        folders = {"configured-kb": root / "configured-kb"}
        _make_kb_folder(folders["configured-kb"], "configured-kb")
        # Another process registers KBs in the live table: a separate connection,
        # so this world's loaded config never hears of it (#925).
        other = PyriteDB(world.config.settings.index_path)
        try:
            late = root / "late-kb"
            _make_kb_folder(late, "late-kb")
            other.register_kb(
                name="late-kb", kb_type="generic", path=str(late), description="d", source="user"
            )
            folders["late-kb"] = late
            eph = root / "eph-kb"
            _make_kb_folder(eph, "eph-kb")
            assert other.insert_new_kb(
                name="eph-kb",
                kb_type="generic",
                path=str(eph),
                description="Ephemeral KB (TTL: 60s)",
                default_role="none",
            )
            folders["eph-kb"] = eph
        finally:
            other.close()
        world.folders = folders
        name = {
            "configured": "configured-kb",
            "added_after_load": "late-kb",
            "ephemeral": "eph-kb",
            "unknown": "nosuchkb",
            "shortname": "sa",
        }[shape]
        return Subject(world, name, shape)

    yield _make
    errors: list[BaseException] = []
    for world in worlds:
        try:
            world.close()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
    if errors:
        raise errors[0]


def _refused(call) -> bool:
    """True when ``call()`` raises the resolver's refusal, False when it returns."""
    try:
        call()
    except KBNotFoundError:
        return True
    return False


def _expect_property(subject: Subject, refused: bool, found: bool, what: str) -> None:
    """The one property: a name the resolver finds is found by the code behind
    it (``found``); one it refuses is refused as ``KBNotFoundError``
    (``refused``), never answered cleanly."""
    if subject.known:
        assert not refused, f"{what}: the resolver finds {subject.name!r}; this refused it"
        assert found, f"{what}: the resolver finds {subject.name!r}; this did not see its KB"
    else:
        assert refused, f"{what}: {subject.name!r} is no KB; this answered instead of refusing"


def _probe_qa(subject: Subject) -> None:
    """QA (T3-2): `validate_kb` on a KB with an unindexed file reports the
    index out of date; for a name that is no KB it raises."""
    from pyrite.services.qa_service import QAService

    world = subject.world
    svc = QAService(world.config, world.db)
    result: dict = {}

    def call():
        result.update(svc.validate_kb(subject.name, readable_kbs=None))

    refused = _refused(call)
    rules = {i.get("rule") for i in result.get("issues", [])}
    _expect_property(subject, refused, "index_out_of_date" in rules, "QAService.validate_kb")


def _probe_small_services(subject: Subject) -> None:
    """KBService and the small services (T3-3): `SchemaService.show_schema`
    returns the KB's own declared type; for a name that is no KB it raises."""
    from pyrite.services.schema_service import SchemaService

    svc = SchemaService(subject.world.config)
    result: dict = {}

    def call():
        result.update(svc.show_schema(subject.name))

    refused = _refused(call)
    _expect_property(
        subject, refused, "widget" in result.get("types", {}), "SchemaService.show_schema"
    )


def _probe_index(subject: Subject) -> None:
    """The index (T3-4): `IndexManager.check_health` lists the KB's unindexed
    file; for a name that is no KB it raises."""
    from pyrite.storage.index import IndexManager

    world = subject.world
    result: dict = {}

    def call():
        result.update(IndexManager(world.db, world.config).check_health(subject.name))

    refused = _refused(call)
    _expect_property(
        subject, refused, bool(result.get("unindexed_files")), "IndexManager.check_health"
    )


def _probe_cli(subject: Subject) -> None:
    """CLI (T3-5a): `pyrite schema diff -k <kb>` prints the KB's declared type;
    for a name that is no KB it exits 1 with KB_NOT_FOUND."""
    out = subject.world.cli("schema", "diff", "-k", subject.name)
    refused = "KB_NOT_FOUND" in (out.output or "")
    _expect_property(
        subject,
        refused,
        out.exit_code == 0 and "widget" in (out.output or ""),
        "pyrite schema diff",
    )


def _probe_server_rest(subject: Subject) -> None:
    """Server (T3-5b): REST `GET /api/entries/type-schemas?kb=` includes the
    KB's own declared type; for a name that is no KB it answers 404 KB_NOT_FOUND."""
    resp = subject.world.client.get("/api/entries/type-schemas", params={"kb": subject.name})
    refused = resp.status_code == 404 and "KB_NOT_FOUND" in resp.text
    found = resp.status_code == 200 and "widget" in resp.json().get("types", {})
    _expect_property(subject, refused, found, "GET /api/entries/type-schemas")


def _probe_extension(subject: Subject) -> None:
    """Extensions (#1529): `pyrite zettel new -k <kb>` writes the note inside the
    KB's folder; for a name that is no KB it exits 1 with KB_NOT_FOUND."""
    out = subject.world.cli("zettel", "new", "A thought", "-k", subject.name)
    refused = "KB_NOT_FOUND" in (out.output or "")
    folder = subject.folder
    found = (
        out.exit_code == 0
        and folder is not None
        and any((folder / "zettels").glob("*.md") if (folder / "zettels").is_dir() else [])
    )
    _expect_property(subject, refused, found, "pyrite zettel new")


#: group -> (probe, needs the REST app)
GROUPS = {
    "qa services (T3-2)": (_probe_qa, False),
    "kb_service and the small services (T3-3)": (_probe_small_services, False),
    "storage index (T3-4)": (_probe_index, False),
    "cli (T3-5a)": (_probe_cli, False),
    "server (T3-5b)": (_probe_server_rest, True),
    "extensions (#1529)": (_probe_extension, False),
}

#: Today's failures, found by running each probe on dev at 96bb4936: one entry
#: per (site group, shape), each naming its group. Strict: a slice that fixes
#: its group turns these XPASS, and deletes the entries it fixed.
_QA = "qa services (T3-2)"
_SMALL = "kb_service and the small services (T3-3)"
_INDEX = "storage index (T3-4)"
_SERVER = "server (T3-5b)"
TODAY_FAILS: dict[tuple[str, str], str] = {
    # QA asks `config.get_kb` and a None is a skipped check or a clean result.
    (
        _QA,
        "added_after_load",
    ): "qa services (T3-2): a KB registered after load is skipped, not checked (qa_service.py `get_kb` is None -> no finding)",
    (_QA, "ephemeral"): "qa services (T3-2): a database-row KB is skipped, not checked",
    (
        _QA,
        "unknown",
    ): "qa services (T3-2): validate_kb of no KB returns a clean zero-count result, not KBNotFoundError",
    (
        _QA,
        "shortname",
    ): "qa services (T3-2): validate_kb of a shortname returns a clean zero-count result, not KBNotFoundError",
    # The small services take a config only: a KB the resolver finds is unknown inside.
    (
        _SMALL,
        "added_after_load",
    ): "kb_service and the small services (T3-3): SchemaService._get_kb refuses a KB the resolver finds",
    (
        _SMALL,
        "ephemeral",
    ): "kb_service and the small services (T3-3): SchemaService._get_kb refuses a database-row KB the resolver finds",
    # The index answers for any name.
    (
        _INDEX,
        "added_after_load",
    ): "storage index (T3-4): check_health does not see a KB registered after load",
    (_INDEX, "ephemeral"): "storage index (T3-4): check_health does not see a database-row KB",
    (
        _INDEX,
        "unknown",
    ): "storage index (T3-4): check_health of no KB returns a report, not KBNotFoundError",
    (
        _INDEX,
        "shortname",
    ): "storage index (T3-4): check_health of a shortname returns a report, not KBNotFoundError",
    # The route's boundary finds the KB; the handler's own get_kb drops its layer silently.
    (
        _SERVER,
        "added_after_load",
    ): "server (T3-5b): /api/entries/type-schemas answers 200 without a KB registered after load's own types",
    (
        _SERVER,
        "ephemeral",
    ): "server (T3-5b): /api/entries/type-schemas answers 200 without a database-row KB's own types",
}


def _cases():
    for group in GROUPS:
        for shape in SHAPES:
            yield pytest.param(group, shape, id=f"{group.split(' ')[0]}-{shape}")


@pytest.mark.parametrize(("group", "shape"), list(_cases()))
def test_a_name_resolves_once_for_every_site_group(group, shape, make_subject, request):
    reason = TODAY_FAILS.get((group, shape))
    if reason:
        request.applymarker(pytest.mark.xfail(reason=reason, strict=True, raises=AssertionError))
    probe, rest = GROUPS[group]
    probe(make_subject(shape, rest=rest))


def test_the_walk_covers_every_site_group_of_the_baseline():
    """Each BASELINE group has a probe, and every expected failure names a probe."""
    assert {g for g, _ in TODAY_FAILS} <= set(GROUPS)
    assert {s for _, s in TODAY_FAILS} <= set(SHAPES)
