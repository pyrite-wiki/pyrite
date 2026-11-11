"""A response carries only text Pyrite wrote for a caller: a ratchet (ADR-0052 N3).

Text from outside Pyrite (an OS error, a library, a provider, git's stderr,
file content, an arbitrary exception's ``str``) belongs to the operator. It is
logged, never shown to a network call and never placed inside a Pyrite
message. The one function that decides what a caller may see of an exception
is ``pyrite.exceptions.public_text``; the fault bodies are built by
``pyrite/server/errors.error_response`` (REST) and ``mcp_server._error_from``
/ ``_protocol_refusal`` (MCP), both through it.

This test walks every module under ``pyrite/`` and ``extensions/*/src`` but
``pyrite/cli`` and ``pyrite/admin_cli.py`` (the CLI is the local process,
whose operator owns the text; see ``ROOTS`` for why the rest is scanned)
and, inside each ``except ... as NAME`` body, finds text derived from NAME:

- ``str(NAME)``, ``repr(NAME)``, ``format(NAME)``, ``"...".format(NAME)``;
- ``NAME.args`` (or any attribute of NAME: ``.stderr``, ``.filename``...);
- ``"..." % NAME`` (or a tuple holding it);
- an f-string with NAME in a replacement field;
- a local name assigned from any of these.

``public_text(NAME)`` and ``outside_text(NAME)`` are the sanctioned wrappers
and are not derived text;
``type(NAME)`` / ``type(NAME).__name__`` (a class name, written by a
programmer) is not either.

Derived text is a violation where it reaches a **sink**:

- a ``raise`` of an exception built from it, unless the raised class fixes
  ``public_message`` (then ``public_text`` never shows the instance text);
- an ``HTTPException`` / ``JSONResponse`` / ``Response`` built from it;
- a dict literal holding it, or a ``d[k] = ...`` storing it (a result or
  error envelope);
- a ``return`` of it.

Each violating site is keyed ``(module, function)`` with its count. A site
that is safe is listed in ``ALLOWED`` with its count and the reason; the
list's size is pinned and only shrinks.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
#: Every module but the CLI's. ``pyrite/server``, ``services``, ``storage``
#: and ``plugins`` are where responses are built; the rest of ``pyrite/``
#: (``utils``, ``schema``, ``models``...) is scanned too, because a handler
#: that catches a Pyrite-text class passes its text on (``_Classes``), and
#: that is sound only if every raise of such a class is under this ratchet.
ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]
#: The local process: its operator owns the text (``acting.is_local_process``).
EXCLUDED = ("pyrite/cli/", "pyrite/admin_cli.py")

_TEXT_CALLS = {"str", "repr", "format"}
_RESPONSE_CALLS = {"HTTPException", "JSONResponse", "Response", "PlainTextResponse"}
#: The sanctioned ways to put an exception into a response or a message:
#: ``public_text`` for a whole response, ``outside_text`` inside a message
#: Pyrite writes (both in ``pyrite.exceptions``).
_WRAPPERS = {"public_text", "outside_text"}
#: Attributes of an exception that are not its text: codes, flags, numbers.
_SAFE_ATTRIBUTES = {
    "error_code",
    "code",
    "status_code",
    "retryable",
    "returncode",
    "errno",
    # The C library's fixed text for errno ("Permission denied"): never a
    # path, never content, the same for every file.
    "strerror",
    # ``HardLinked.links``, ``MigrationError.version`` and
    # ``OwnerNotKeptError.uid``/``.gid``: integers.
    "links",
    "version",
    "uid",
    "gid",
    "__class__",
    "__traceback__",
}


CLASS_ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]


class _Classes:
    """What the code says about each exception class, read from the source.

    Read statically (every ``class`` in ``pyrite/`` and the extensions), so
    the answer does not depend on which modules a test run has imported.

    - ``fixed``: a ``PyriteError`` class that sets ``public_message`` (itself
      or its nearest ancestor that says): ``public_text`` shows that fixed
      text, never the instance's, so a raise of it may carry any detail.
    - ``pyrite_text``: a ``PyriteError`` class whose instances' text is shown
      as it is, by it and by every class under it. Text caught as one of
      these was written by Pyrite (the raise that built it is itself under
      this ratchet), so a handler may pass it on.
    """

    def __init__(self, roots: list[Path]) -> None:
        bases: dict[str, list[str]] = {}
        own: dict[str, bool | None] = {}  # True fixed, False explicitly None, None unset
        for root in roots:
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts or "tests" in path.parts:
                    continue
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if not isinstance(node, ast.ClassDef):
                        continue
                    names = [
                        b.id if isinstance(b, ast.Name) else b.attr
                        for b in node.bases
                        if isinstance(b, (ast.Name, ast.Attribute))
                    ]
                    bases.setdefault(node.name, names)
                    for stmt in node.body:
                        target = value = None
                        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                            target, value = stmt.targets[0], stmt.value
                        elif isinstance(stmt, ast.AnnAssign):
                            target, value = stmt.target, stmt.value
                        if (
                            isinstance(target, ast.Name)
                            and target.id == "public_message"
                            and value is not None
                        ):
                            own[node.name] = not (
                                isinstance(value, ast.Constant) and value.value is None
                            )
        self._bases = bases
        self._own = own
        children: dict[str, set[str]] = {}
        for name, bs in bases.items():
            for b in bs:
                children.setdefault(b, set()).add(name)
        self._children = children

    def is_pyrite(self, name: str, _seen: frozenset = frozenset()) -> bool:
        if name == "PyriteError":
            return True
        if name in _seen:
            return False
        return any(self.is_pyrite(b, _seen | {name}) for b in self._bases.get(name, []))

    def fixed(self, name: str, _seen: frozenset = frozenset()) -> bool:
        if not self.is_pyrite(name) or name in _seen:
            return False
        if self._own.get(name) is not None:
            return bool(self._own[name])
        return any(self.fixed(b, _seen | {name}) for b in self._bases.get(name, []))

    def pyrite_text(self, name: str) -> bool:
        if not self.is_pyrite(name):
            return False
        pending, seen = [name], set()
        while pending:
            cls = pending.pop()
            if cls in seen:
                continue
            seen.add(cls)
            if self.fixed(cls):
                return False
            pending.extend(self._children.get(cls, ()))
        return True


_CLASSES: _Classes | None = None


def _classes() -> _Classes:
    global _CLASSES
    if _CLASSES is None:
        _CLASSES = _Classes(CLASS_ROOTS)
    return _CLASSES


#: (module relative to the repo, enclosing function) -> (count, why it is safe).
#: A site lands here only when the derived text is provably Pyrite's own or
#: never reaches a network caller. Shrinks only.
ALLOWED: dict[tuple[str, str], tuple[int, str]] = {
    ("pyrite/server/mcp_routes.py", "mount_mcp_routes.handle_sse"): (
        1,
        "re-answers an HTTPException's own detail: every HTTPException is built by "
        "Pyrite, and its arguments are under this ratchet (the 'response' sink)",
    ),
    ("pyrite/services/branding_service.py", "BrandingService._load"): (
        1,
        "caches the text only to re-raise it as BrandingInvalidError, whose class "
        "fixes public_message: no transport shows the cached text",
    ),
}
ALLOWED_SIZE = 2


def _qualname(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


class _Scan(ast.NodeVisitor):
    """Collect (function, sink kind) for every sink that carries derived text."""

    def __init__(self, classes: _Classes) -> None:
        self.classes = classes
        self.stack: list[str] = []
        # (exception names, text names) in scope, innermost last
        self.names: list[tuple[set[str], set[str]]] = []
        self.found: list[tuple[str, str]] = []
        self.lines: list[int] = []  # the line of each finding, for the message

    # -- scope ---------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        # A nested function body is its own scope: an except name of the
        # enclosing frame is still visible to a closure, so keep the stack.
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.type is not None:
            self.visit(node.type)
        outer = self.names[-1] if self.names else (frozenset(), frozenset())
        if node.name and self._caught_pyrite_text(node.type):
            # Every class this handler can catch shows its own text, which a
            # Pyrite raise wrote (and that raise is under this ratchet).
            self.names.append((set(outer[0]), set(outer[1])))
        elif node.name:
            # (exception names, text names): an exception object may be
            # passed to a builder; text derived from it may not go anywhere.
            self.names.append((set(outer[0]) | {node.name}, set(outer[1])))
        elif self.names:
            self.names.append((set(outer[0]), set(outer[1])))
        else:
            for stmt in node.body:
                self.visit(stmt)
            return
        for stmt in node.body:
            self.visit(stmt)
        self.names.pop()

    def _caught_pyrite_text(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        elts = node.elts if isinstance(node, ast.Tuple) else [node]
        names = [
            e.id if isinstance(e, ast.Name) else e.attr if isinstance(e, ast.Attribute) else ""
            for e in elts
        ]
        return all(n and self.classes.pyrite_text(n) for n in names)

    # -- derived text --------------------------------------------------------
    def _derived(self, node: ast.AST | None, top: bool = True) -> bool:
        """Whether ``node`` carries text derived from an except name in scope.

        ``top``: ``node`` is itself the value at a sink (or inside a
        stringifier), so a bare exception there is its text. Passed as an
        argument to some other call, a bare exception is an object, not text.
        """
        if node is None or not self.names:
            return False
        return self._carries(node, top)

    def _carries(self, node: ast.AST, top: bool) -> bool:
        excs, texts = self.names[-1]
        if isinstance(node, ast.Name):
            return node.id in texts or (top and node.id in excs)
        if isinstance(node, ast.Call):
            func = node.func
            fname = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if fname in _WRAPPERS or fname in {"type", "isinstance", "getattr", "hasattr"}:
                return False
            if isinstance(func, ast.Name) and fname in _TEXT_CALLS or fname == "format":
                return any(self._carries(a, True) for a in node.args) or any(
                    self._carries(k.value, True) for k in node.keywords
                )
            if isinstance(func, ast.Attribute) and self._carries(func.value, True):
                return True  # e.stderr.decode(), msg.strip()
            return any(self._carries(a, False) for a in node.args) or any(
                self._carries(k.value, False) for k in node.keywords
            )
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in excs:
                return node.attr not in _SAFE_ATTRIBUTES
            if node.attr in {"__name__", "__qualname__"}:
                return False
            return self._carries(node.value, top)
        if isinstance(node, ast.JoinedStr):
            return any(
                self._carries(v.value, True)
                for v in node.values
                if isinstance(v, ast.FormattedValue)
            )
        if isinstance(node, ast.BinOp):
            return self._carries(node.left, True) or self._carries(node.right, True)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(self._carries(e, top) for e in node.elts)
        if isinstance(node, ast.Dict):
            return any(self._carries(v, top) for v in node.values if v is not None)
        if isinstance(node, ast.Subscript):
            return self._carries(node.value, top)
        if isinstance(node, ast.IfExp):
            return self._carries(node.body, top) or self._carries(node.orelse, top)
        if isinstance(node, ast.BoolOp):
            return any(self._carries(v, top) for v in node.values)
        if isinstance(node, ast.Starred):
            return self._carries(node.value, top)
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            return self._carries(node.elt, top) or any(
                self._carries(g.iter, top) for g in node.generators
            )
        return False

    def _hit(self, kind: str, node: ast.AST) -> None:
        self.found.append((_qualname(self.stack), kind))
        self.lines.append(getattr(node, "lineno", 0))

    # -- propagation ---------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if self.names:
            excs, texts = self.names[-1]
            alias = isinstance(node.value, ast.Name) and node.value.id in excs
            derived = not alias and self._derived(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if alias:
                        excs.add(target.id)
                    elif derived:
                        texts.add(target.id)
                    else:
                        texts.discard(target.id)
                elif isinstance(target, ast.Subscript) and (alias or derived):
                    self._hit("stored into a dict", node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if self.names and node.value is not None and isinstance(node.target, ast.Name):
            if self._derived(node.value):
                self.names[-1][1].add(node.target.id)
        self.generic_visit(node)

    # -- sinks ---------------------------------------------------------------
    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
        exc = node.exc
        if self.names and isinstance(exc, ast.Call):
            func = exc.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            # A class (CamelCase) built from the exception carries its text
            # as its own str(); a helper (snake_case, ``refusal_http(e)``,
            # ``_storage_error(id, e)``) is handed the object, and only text
            # derived from it counts. A class that fixes public_message never
            # shows the instance's text.
            top = name[:1].isupper()
            if name in _RESPONSE_CALLS:
                pass  # counted once, by visit_Call
            elif not self.classes.fixed(name) and (
                any(self._derived(a, top) for a in exc.args)
                or any(self._derived(k.value, top) for k in exc.keywords)
            ):
                self._hit("raised", node)
                return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if (
            self.names
            and name in _RESPONSE_CALLS
            and (
                any(self._derived(a) for a in node.args)
                or any(self._derived(k.value) for k in node.keywords)
            )
        ):
            self._hit("response", node)
            return  # the response is one site, not also its dict
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:  # noqa: N802
        if self.names and any(self._derived(v) for v in node.values if v is not None):
            self._hit("dict", node)
            return
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        if (
            self.names
            and node.value is not None
            and not isinstance(node.value, ast.Dict)
            and self._derived(node.value, top=False)
        ):
            self._hit("returned", node)
            return
        self.generic_visit(node)


def scan_source(source: str) -> list[tuple[str, str]]:
    """(function, sink kind) for each violation in ``source``."""
    scan = _Scan(_classes())
    scan.visit(ast.parse(source))
    return scan.found


def _violations() -> dict[tuple[str, str], int]:
    sites: dict[tuple[str, str], int] = {}
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            rel = str(path.relative_to(REPO))
            if rel.startswith(EXCLUDED):
                continue
            for function, _kind in scan_source(path.read_text(encoding="utf-8")):
                sites[(rel, function)] = sites.get((rel, function), 0) + 1
    return sites


def test_no_response_carries_text_pyrite_did_not_write() -> None:
    sites = _violations()
    new = sorted((site, n) for site, n in sites.items() if n > ALLOWED.get(site, (0, ""))[0])
    assert not new, (
        "text derived from a caught exception reaches a response, a raise or a result "
        "dict. Let a PyriteError propagate, raise one whose text Pyrite wrote, or pass "
        "the exception through public_text(...) (ADR-0052 N3):\n"
        + "\n".join(
            f"  {m}: {f} ({n} site{'s' if n > 1 else ''}"
            f"{', ' + str(ALLOWED[(m, f)][0]) + ' allowed' if (m, f) in ALLOWED else ''})"
            for (m, f), n in new
        )
    )


@pytest.mark.control(reason="the ratchet's bookkeeping: it guards the next site, not this change")
def test_the_allow_list_only_shrinks() -> None:
    sites = _violations()
    stale = sorted(
        (site, n, sites.get(site, 0))
        for site, (n, _why) in ALLOWED.items()
        if sites.get(site, 0) < n
    )
    assert not stale, f"fixed, so lower or delete the entry and ALLOWED_SIZE: {stale}"
    assert all(why.strip() for _n, why in ALLOWED.values()), "every entry carries a reason"
    assert len(ALLOWED) == ALLOWED_SIZE, "the list only shrinks: lower ALLOWED_SIZE with it"


@pytest.mark.control(reason="a sanity check of the scan itself")
def test_the_scan_reaches_the_server() -> None:
    """A scan that finds no except handler at all is broken, not clean."""
    handlers = 0
    for path in (REPO / "pyrite" / "server").rglob("*.py"):
        handlers += sum(
            isinstance(n, ast.ExceptHandler) and n.name is not None
            for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    assert handlers > 50, handlers


# -- planted violations: one per shape, each must be caught -------------------

_PLANTED = {
    "str() into HTTPException": """
def f():
    try:
        g()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
""",
    "repr() into a result dict": """
def f():
    try:
        g()
    except OSError as exc:
        return {"error": repr(exc)}
""",
    ".args into JSONResponse": """
def f():
    try:
        g()
    except OSError as exc:
        return JSONResponse({"error": exc.args[0]}, status_code=500)
""",
    "format() into a raise": """
def f():
    try:
        g()
    except OSError as exc:
        raise PluginToolError(format(exc))
""",
    "%-formatting into a raise": """
def f():
    try:
        g()
    except OSError as exc:
        raise PluginToolError("could not read: %s" % exc)
""",
    "f-string into a raise": """
def f():
    try:
        g()
    except OSError as exc:
        raise PluginToolError(f"the model call failed: {type(exc).__name__}: {exc}")
""",
    "str.format into a stored dict value": """
def f(result):
    try:
        g()
    except OSError as exc:
        result["error"] = "failed: {}".format(exc)
        return result
""",
    "a local derived from the exception, then returned": """
def f():
    try:
        g()
    except OSError as exc:
        message = f"failed: {exc}"
        return message
""",
    "a git stderr attribute into HTTPException": """
def f():
    try:
        g()
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail={"message": exc.stderr})
""",
    "except PyriteError, str() bypassing public_message": """
def f():
    try:
        g()
    except PyriteError as e:
        raise HTTPException(status_code=500, detail=str(e))
""",
    "a fixed-message class's text laundered into one that shows it": """
def f():
    try:
        g()
    except StorageError as e:
        raise ValidationError(f"cannot save: {e}")
""",
    "the bare exception as a new exception's message": """
def f():
    try:
        g()
    except OSError as exc:
        raise ValidationError(exc)
""",
    "an outside exception in a tuple with a Pyrite one": """
def f():
    try:
        g()
    except (ValidationError, OSError) as e:
        return {"error": str(e)}
""",
    "inner except inside an outer one": """
def f():
    try:
        g()
    except OSError as outer:
        try:
            h()
        except ValueError:
            return {"error": str(outer)}
""",
}


@pytest.mark.control(reason="tests the scanner on planted source, not the change")
@pytest.mark.parametrize("shape", sorted(_PLANTED))
def test_a_planted_violation_is_caught(shape: str) -> None:
    assert scan_source(_PLANTED[shape]), shape


_SANCTIONED = {
    "public_text wraps it": """
def f():
    try:
        g()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=public_text(exc))
""",
    "a class that fixes public_message": """
def f():
    try:
        g()
    except OSError as exc:
        raise StorageError(f"disk: {exc}") from exc
""",
    "only logged": """
def f():
    try:
        g()
    except OSError as exc:
        logger.warning("failed: %s", exc)
        raise HTTPException(status_code=500, detail="Could not read the file.")
""",
    "a Pyrite-text class passed on": """
def f():
    try:
        g()
    except ValidationError as e:
        raise ValidationError(f"links: {e}") from None
""",
    "the exception object handed to a builder": """
def f():
    try:
        g()
    except OSError as e:
        raise _storage_error(entry_id, e) from e
""",
    "outside_text inside a Pyrite message": """
def f():
    try:
        g()
    except OSError as exc:
        raise ValidationError(f"cannot read: {outside_text(exc)}") from exc
""",
    "the class name only": """
def f():
    try:
        g()
    except OSError as exc:
        return {"error_type": type(exc).__name__}
""",
}


@pytest.mark.control(reason="tests the scanner on sanctioned source, not the change")
@pytest.mark.parametrize("shape", sorted(_SANCTIONED))
def test_a_sanctioned_shape_is_not_reported(shape: str) -> None:
    assert scan_source(_SANCTIONED[shape]) == [], shape


# -- the helpers and a site, by behaviour ------------------------------------


def test_outside_text_withholds_an_outside_exceptions_text_from_a_network_caller(caplog):
    import logging

    from pyrite.exceptions import OUTSIDE_TEXT_WITHHELD, outside_text

    with caplog.at_level(logging.WARNING, logger="pyrite.exceptions"):
        shown = outside_text(OSError(13, "Permission denied", "/srv/secret"), local=False)
    assert shown == OUTSIDE_TEXT_WITHHELD
    assert "/srv/secret" in caplog.text  # the operator still finds it


@pytest.mark.control(reason="the local operator keeps the text, as before")
def test_outside_text_keeps_the_text_for_the_local_process():
    from pyrite.exceptions import outside_text

    assert "/srv/secret" in outside_text(OSError("/srv/secret"), local=True)


def test_outside_text_shows_a_pyrite_errors_public_text():
    from pyrite.exceptions import RefusedValueError, StorageError, outside_text

    assert outside_text(RefusedValueError("limit must be positive"), local=False) == (
        "limit must be positive"
    )
    assert "/srv/secret" not in outside_text(StorageError("/srv/secret"), local=False)


def test_a_yaml_parse_error_names_no_file_content_to_a_network_caller():
    """The parser quotes the file's own lines; a network caller reads Pyrite's
    message with the detail withheld, the CLI's operator the whole of it."""
    from pyrite.exceptions import FrontmatterError
    from pyrite.services.acting import local_process
    from pyrite.utils.yaml import load_yaml_document

    text = "title: ok\nsecret-line: [unclosed\n"
    with pytest.raises(FrontmatterError) as network:
        load_yaml_document(text)
    assert str(network.value).startswith("Invalid YAML frontmatter: ")
    assert "secret-line" not in str(network.value)
    with local_process(True), pytest.raises(FrontmatterError) as local:
        load_yaml_document(text)
    assert "secret-line" in str(local.value)  # the parser quotes the line


def test_pyrites_own_value_errors_still_reach_a_network_caller():
    """A refusal Pyrite wrote is a RefusedValueError, so ``public_text``
    shows it where a plain ValueError's text is withheld."""
    from pyrite.exceptions import public_text
    from pyrite.utils.field_path import parse_field_path

    with pytest.raises(ValueError) as raised:
        parse_field_path("a..b")
    assert "empty segment" in public_text(raised.value, local=False)


def test_an_unreadable_fields_refusal_names_no_library_text_to_a_network_caller(tmp_path):
    """``_refuse_unreadable_fields`` words its refusal around the ``TypeError``
    ``read_fields`` raised (a by-id get over REST and MCP reaches it). That
    text is a library's: withheld from a network caller, kept for the CLI."""
    from pyrite.exceptions import ValidationError
    from pyrite.services.acting import local_process
    from pyrite.services.kb_service import _refuse_unreadable_fields

    canary = "CANARY-encoder-text-7f3a"
    path = tmp_path / "e.md"  # absent: no #760 rule names it, so the generic refusal answers
    exc = TypeError(canary)

    with pytest.raises(ValidationError) as network:
        _refuse_unreadable_fields(tmp_path, path, "e", exc)
    assert canary not in str(network.value)
    assert str(network.value).startswith("Cannot read 'e': the frontmatter holds a value")

    with local_process(True), pytest.raises(ValidationError) as local:
        _refuse_unreadable_fields(tmp_path, path, "e", exc)
    assert canary in str(local.value)
