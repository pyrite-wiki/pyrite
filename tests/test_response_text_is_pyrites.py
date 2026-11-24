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
and finds text derived from an exception. The sources are the NAME of an
``except ... as NAME`` body, and (0.26.3) a parameter that receives an
exception: annotated as one, or handed one by any call (a helper hop,
followed by name to a fixpoint across modules); a parameter handed text
derived from one, inside its own module. Derived text is:

- ``str(NAME)``, ``repr(NAME)``, ``format(NAME)``, ``"...".format(NAME)``;
- ``NAME.args`` (or any attribute of NAME: ``.stderr``, ``.filename``, and
  ``.strerror`` too, D5), and ``getattr(NAME, ...)`` of the same;
- ``"..." % NAME`` (or a tuple holding it);
- an f-string with NAME in a replacement field;
- ``traceback.format_*(...)``;
- a local name assigned from any of these.

``NAME.orig`` / ``__cause__`` / ``__context__``, ``sys.exc_info()[1]``,
``sys.exception()`` and ``future.exception()`` are exception objects, not text.
What a handler binds (a text name, an alias of the exception) outlives the
handler: ``msg = str(e)`` in the handler and ``return msg`` after the ``try``
is a violation.

Stated limits (held by review, not by this test): a ``lambda`` or a
``functools.partial`` that closes over an exception and is called later; a
function that returns the exception object for its caller to stringify (the
caller is a source only when the return value is annotated as an exception);
an exception captured by a logging handler and read back; a name bound by
``for``/``with``/a comprehension; dynamic attribute access other than
``getattr``. Text derived from an exception and handed to a helper is
followed only within its own module (an exception object is followed across
modules, by name). A value written to the database (an ``execute``
parameter) is not a sink: what reads it back is. The one such write found,
``embedding_worker``'s ``embed_queue.error``, is read back by nothing
(``test_nothing_reads_the_embed_queue_error_back``).

``public_text(NAME)`` and ``outside_text(NAME)`` are the sanctioned wrappers
and are not derived text;
``type(NAME)`` / ``type(NAME).__name__`` (a class name, written by a
programmer) is not either.

Derived text is a violation where it reaches a **sink**:

- a ``raise`` of an exception built from it, unless the raised class fixes
  ``public_message`` (then ``public_text`` never shows the instance text);
- an ``HTTPException`` / ``JSONResponse`` / ``Response`` built from it;
- a dict literal holding it, or a ``d[k] = ...``, ``d.setdefault(k, ...)`` or
  ``d.update(...)`` storing it (a result or error envelope);
- a ``return`` or a ``yield`` of it;
- (0.26.3) an attribute or a global set from it, or from the exception
  itself, and a list ``append``/``extend``/``insert``/``add`` of it: a later
  reader (a warning, a status) is out of this scan's sight. An exception kept
  for later is a ``HeldFault`` (``exceptions.held_fault``), whose text is
  decided when it is shown.

Text a parser wrote about bytes the caller sent, or a KB they read, is marked
where it is made (``caller_owned``, ``kb_owned``, a parser's ``origin=``);
``MARKS`` lists every such mark with why it is the caller's.

Each violating site is keyed ``(module, function)`` with its count. A site
that is safe is listed in ``ALLOWED`` with its count and the reason; the
list's size is pinned and only shrinks.
"""

from __future__ import annotations

import ast
import functools
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
#: ``read_cli`` is the ``pyrite-read`` entry point and ``utils/errors`` renders
#: a CLI's errors; ``test_the_cli_only_modules_are_imported_by_clis_only``
#: fails when anything else imports them.
EXCLUDED = ("pyrite/cli/", "pyrite/admin_cli.py", "pyrite/read_cli.py", "pyrite/utils/errors.py")

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


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_exception_annotation(node: ast.AST | None) -> bool:
    """``exc: OSError``, ``error: Exception | None``, ``e: "BaseException"``."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(node, ast.BinOp):
        return _is_exception_annotation(node.left) or _is_exception_annotation(node.right)
    if isinstance(node, ast.Subscript):  # Optional[OSError]
        return _is_exception_annotation(node.slice)
    if isinstance(node, ast.Tuple):
        return any(_is_exception_annotation(e) for e in node.elts)
    name = _call_name(node)
    return name.endswith(("Error", "Exception")) or name in {"BaseException", "Exception"}


#: Annotations that say a parameter holds no exception and no outside text: a
#: hop by name to a function whose parameter is one of these is another
#: function of the same name (``overlay_backend._refusal(name: str, ...)``).
_PLAIN_TYPES = {"int", "float", "bool", "bytes", "Path", "dict", "list", "tuple", "set"}


def _is_plain_annotation(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Subscript):
        return _is_plain_annotation(node.value)
    return isinstance(node, ast.Name) and node.id in _PLAIN_TYPES


def _is_text_annotation(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Name) and node.id == "str"


def _produces_exception(node: ast.AST) -> bool:
    """``sys.exc_info()[1]``, ``sys.exception()``, ``future.exception()``: a
    call that hands back the exception being handled or one a task raised."""
    if isinstance(node, ast.Subscript):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    if name == "exc_info":
        return True
    # ``logger.exception("...")`` takes a message; ``sys.exception()`` and
    # ``future.exception(timeout=...)`` take none.
    return name == "exception" and isinstance(node.func, ast.Attribute) and not node.args


#: Attributes of an exception that hold another exception, not text.
_EXCEPTION_ATTRIBUTES = {"orig", "__cause__", "__context__"}


#: Callees an exception may be handed to without becoming a hop: the wrappers
#: (the constructor), a logger, the builtins that do not stringify, and the
#: list methods (counted as the "appended" sink instead).
_NOT_HOPS = _WRAPPERS | {
    "type",
    "isinstance",
    "issubclass",
    "getattr",
    "hasattr",
    "id",
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "critical",
    "log",
    "append",
    "appendleft",
    "extend",
    "insert",
    "add",
    "print",
    "write",
}
#: Methods that store a value under a key.
_DICT_STORE_CALLS = {"setdefault", "update"}
#: Methods that put a value into a container a later reader returns.
_APPEND_CALLS = {"append", "appendleft", "extend", "insert", "add"}
#: ``traceback.format_*``: the text of the exception being handled, its frames
#: and their source lines.
_TRACEBACK_TEXT = {
    "format_exc",
    "format_exception",
    "format_exception_only",
    "format_tb",
    "format_stack",
    "format_list",
}


class _Scan(ast.NodeVisitor):
    """Collect (function, sink kind) for every sink that carries derived text.

    ``hops`` maps a function name to ``(kind, slot)`` pairs: a position or a
    keyword at which some call passes it an exception object (kind ``"exc"``)
    or text derived from one (kind ``"text"``). A parameter there is a source
    inside that function (a helper the exception or its text is handed to).
    ``_scan_modules`` rescans until the map stops growing, so a helper that
    hands it on to another helper is followed too.
    """

    def __init__(self, classes: _Classes, hops: dict[str, set] | None = None) -> None:
        self.classes = classes
        self.hops_in: dict[str, set] = hops or {}
        self.hops: dict[str, set] = {}
        self.stack: list[str] = []
        # (exception names, text names) in scope, innermost last
        self.names: list[tuple[set[str], set[str]]] = []
        self.globals: list[set[str]] = [set()]
        self.found: list[tuple[str, str]] = []
        self.lines: list[int] = []  # the line of each finding, for the message

    # -- scope ---------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _parameter_sources(self, node: ast.FunctionDef) -> tuple[set[str], set[str]]:
        """(exception parameters, text parameters) of ``node``."""
        args = [*node.args.posonlyargs, *node.args.args]
        every = [*args, *node.args.kwonlyargs]
        excs = {a.arg for a in every if _is_exception_annotation(a.annotation)}
        texts: set[str] = set()
        offset = 1 if args and args[0].arg in {"self", "cls"} else 0
        for kind, slot in self.hops_in.get(node.name, ()):
            if isinstance(slot, int):
                param = args[slot + offset] if slot + offset < len(args) else None
            else:
                param = next((a for a in every if a.arg == slot), None)
            if param is None or _is_plain_annotation(param.annotation):
                continue
            if kind == "exc" and _is_text_annotation(param.annotation):
                continue  # a str parameter of another function by the same name
            (excs if kind == "exc" else texts).add(param.arg)
        return excs, texts - excs

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        # A nested function body is its own scope: an except name of the
        # enclosing frame is still visible to a closure, so keep the stack.
        # A parameter that receives an exception (annotated as one, or handed
        # one by some call) is a source inside the function.
        self.stack.append(node.name)
        self.globals.append(set())
        # Every function has a frame, so text a handler assigns is still known
        # after the handler ends (``msg = str(e)`` then ``return msg``).
        excs, texts = self._parameter_sources(node)
        outer = self.names[-1] if self.names else (set(), set())
        self.names.append(((set(outer[0]) - texts) | excs, (set(outer[1]) - excs) | texts))
        self.generic_visit(node)
        self.names.pop()
        self.globals.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.globals[-1].update(node.names)

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
        excs, texts = self.names.pop()
        if self.names:
            # What the handler bound outlives it: a text name, and an alias of
            # the exception (the except name itself is unbound at the end).
            self.names[-1][1].update(texts)
            self.names[-1][0].update(excs - {node.name})

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
        if node is None:
            return False
        if not self.names:
            # At module level only a traceback formatter is text from outside
            # Pyrite.
            return any(
                isinstance(n, ast.Call) and _call_name(n.func) in _TRACEBACK_TEXT
                for n in ast.walk(node)
            )
        return self._carries(node, top)

    def _carries(self, node: ast.AST, top: bool) -> bool:
        excs, texts = self.names[-1]
        if _produces_exception(node):
            return top  # an exception object: text only where it is shown
        if isinstance(node, ast.NamedExpr):
            return self._carries(node.value, top)
        if isinstance(node, ast.Name):
            return node.id in texts or (top and node.id in excs)
        if isinstance(node, ast.Call):
            func = node.func
            fname = _call_name(func)
            if fname in _TRACEBACK_TEXT:
                return True  # traceback.format_exc(): the handled exception's text
            if fname == "getattr" and node.args:
                # getattr(e, "stderr") is e.stderr; a computed name is any attribute.
                held = node.args[0]
                if not (isinstance(held, ast.Name) and held.id in excs):
                    return self._carries(held, top)
                name = node.args[1] if len(node.args) > 1 else None
                literal = name.value if isinstance(name, ast.Constant) else None
                if literal in _EXCEPTION_ATTRIBUTES:
                    return top
                return literal not in _SAFE_ATTRIBUTES
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
                if node.attr in _EXCEPTION_ATTRIBUTES:
                    return top  # another exception: an object, text only where shown
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
            # An instance of a class that fixes public_message is an
            # exception, not text: public_text never shows what it carries,
            # but its str() is still the text (``err = StorageError(f"{e}")``).
            alias = self._is_exception(node.value) or (
                isinstance(node.value, ast.Call) and self.classes.fixed(_call_name(node.value.func))
            )
            derived = not alias and self._derived(node.value)
            for target in node.targets:
                self._bind(target, alias, derived, node)
        elif self._derived(node.value):  # a traceback formatter outside a handler
            for target in node.targets:
                self._bind(target, False, True, node)
        self.generic_visit(node)

    def _is_exception(self, node: ast.AST) -> bool:
        """``e``, ``e.orig``, ``getattr(e, "orig", e)``, ``sys.exc_info()[1]``,
        ``fut.exception()``: an exception object."""
        excs = self.names[-1][0] if self.names else set()
        if _produces_exception(node):
            return True
        if isinstance(node, ast.Name):
            return node.id in excs
        if isinstance(node, ast.Attribute) and node.attr in _EXCEPTION_ATTRIBUTES:
            return self._is_exception(node.value)
        if isinstance(node, ast.Call) and _call_name(node.func) == "getattr" and node.args:
            name = node.args[1] if len(node.args) > 1 else None
            return (
                isinstance(name, ast.Constant)
                and name.value in _EXCEPTION_ATTRIBUTES
                and self._is_exception(node.args[0])
            )
        return False

    def _bind(self, target: ast.AST, alias: bool, derived: bool, node: ast.AST) -> None:
        """A name takes the value on; an attribute, a global or a dict slot is a sink:
        whoever reads it later (a warning, a status, a body) is out of this scan's sight."""
        if isinstance(target, ast.Name) and target.id in self.globals[-1] and (alias or derived):
            self._hit("set on a global", node)
        elif isinstance(target, ast.Name) and self.names:
            excs, texts = self.names[-1]
            if alias:
                excs.add(target.id)
            elif derived:
                texts.add(target.id)
            else:
                texts.discard(target.id)
        elif isinstance(target, ast.Subscript) and (alias or derived):
            self._hit("stored into a dict", node)
        elif isinstance(target, ast.Attribute) and (alias or derived):
            self._hit("set on an attribute", node)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind(elt, False, derived, node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            excs = self.names[-1][0] if self.names else set()
            alias = isinstance(node.value, ast.Name) and node.value.id in excs
            derived = not alias and self._derived(node.value)
            if alias or derived or (self.names and isinstance(node.target, ast.Name)):
                self._bind(node.target, alias, derived, node)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        alias = self._is_exception(node.value)
        self._bind(node.target, alias, not alias and self._derived(node.value), node)
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:  # noqa: N802
        # A generator's caller reads what it yields, as a return's reads it.
        if node.value is not None and self._derived(node.value, top=False):
            self._hit("yielded", node)
            return
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:  # noqa: N802
        if self._derived(node.value, top=False):
            self._hit("yielded", node)
            return
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        if self._derived(node.value):
            self._bind(node.target, False, True, node)
        self.generic_visit(node)

    # -- sinks ---------------------------------------------------------------
    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
        exc = node.exc
        if isinstance(exc, ast.Call):
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
        if name in _RESPONSE_CALLS and (
            any(self._derived(a) for a in node.args)
            or any(self._derived(k.value) for k in node.keywords)
        ):
            self._hit("response", node)
            return  # the response is one site, not also its dict
        if (
            name in _DICT_STORE_CALLS
            and isinstance(func, ast.Attribute)
            and (
                any(self._derived(a) for a in node.args[1:] if name == "setdefault")
                or any(self._derived(k.value) for k in node.keywords)
                or (name == "update" and any(self._derived(a) for a in node.args))
            )
        ):
            # d.setdefault(k, e), d.update(error=e): a stored value, as d[k] = e.
            self._hit("stored into a dict", node)
            return
        if (
            name in _APPEND_CALLS
            and isinstance(func, ast.Attribute)
            and any(self._derived(a) for a in node.args)
        ):
            # warnings.append(f"... {e}"): the list is read later, by a body.
            self._hit("appended to a list", node)
            return
        if self.names and name and name not in _NOT_HOPS and not name[:1].isupper():
            # Text is followed into this module's own functions: a bare name or
            # a method of self (``re.search(p, text)`` is not ``self.search``).
            own = isinstance(func, ast.Name) or (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in {"self", "cls"}
            )
            slots = [(i, a) for i, a in enumerate(node.args)]
            slots += [(k.arg, k.value) for k in node.keywords if k.arg]
            for slot, arg in slots:
                if self._is_exception(arg):
                    self.hops.setdefault(name, set()).add(("exc", slot))
                elif self._derived(arg) and own:
                    self.hops.setdefault(name, set()).add(("text", slot))
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:  # noqa: N802
        if any(self._derived(v) for v in node.values if v is not None):
            self._hit("dict", node)
            return
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        value = node.value
        if isinstance(value, ast.Call) and _call_name(value.func)[:1].isupper():
            # A returned exception is raised by the caller (``raise
            # _storage_error(...)``): judged as a raise of the same class.
            name = _call_name(value.func)
            if self.classes.fixed(name):
                self.generic_visit(node)
                return
            if any(self._derived(a, True) for a in value.args) or any(
                self._derived(k.value, True) for k in value.keywords
            ):
                self._hit("returned", node)
                return
        if (
            node.value is not None
            and not isinstance(node.value, ast.Dict)
            and self._derived(node.value, top=False)
        ):
            self._hit("returned", node)
            return
        self.generic_visit(node)


def _scan_modules(trees: dict[str, ast.Module]) -> dict[str, list[tuple[str, str]]]:
    """(function, sink kind) per module, with helper hops followed to a fixpoint.

    Each pass learns which function names some call hands an exception (or
    text derived from one) to, at which position or keyword; the next pass
    treats those parameters as sources. Names, not definitions: a helper name
    shared by two functions makes both parameters sources, which can
    over-report; a parameter annotated as a plain type (``str`` excepted, since
    text is a ``str``) is not taken. Text hops stay inside their module.
    """
    hops: dict[str, set] = {}  # exception hops: by name, across modules
    local: dict[str, dict[str, set]] = {rel: {} for rel in trees}  # text hops: per module
    while True:
        found: dict[str, list[tuple[str, str]]] = {}
        grew = False
        for rel, tree in trees.items():
            seen = {n: hops.get(n, set()) | local[rel].get(n, set()) for n in {*hops, *local[rel]}}
            scan = _Scan(_classes(), seen)
            scan.visit(tree)
            found[rel] = scan.found
            for name, slots in scan.hops.items():
                for kind, slot in slots:
                    # Text handed on is followed inside its own module only: a
                    # name shared across modules (``search``, ``get``) is too
                    # common to tell one function from another.
                    into = hops if kind == "exc" else local[rel]
                    if (kind, slot) not in into.get(name, set()):
                        into.setdefault(name, set()).add((kind, slot))
                        grew = True
        if not grew:
            return found


def scan_source(source: str) -> list[tuple[str, str]]:
    """(function, sink kind) for each violation in ``source``."""
    return _scan_modules({"<source>": ast.parse(source)})["<source>"]


#: The constructor itself: the functions that decide what a caller sees of an
#: exception read its text by design. Not allow-listed sites; the rule.
CONSTRUCTOR = {
    ("pyrite/exceptions.py", "public_text"),
    ("pyrite/exceptions.py", "outside_text"),
    # A PyriteError's public attributes, read off a PyriteError only.
    ("pyrite/exceptions.py", "refusal_fields"),
    ("pyrite/exceptions.py", "public_attribute"),
    # Keeps the exception; shows it only through outside_text.
    ("pyrite/exceptions.py", "HeldFault.__init__"),
    ("pyrite/exceptions.py", "held_fault"),
    # Reads the origin mark, answers a bool.
    ("pyrite/exceptions.py", "_the_callers"),
}

_VIOLATIONS: dict[tuple[str, str], int] | None = None


def _violations() -> dict[tuple[str, str], int]:
    global _VIOLATIONS
    if _VIOLATIONS is not None:
        return _VIOLATIONS
    trees: dict[str, ast.Module] = {}
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            rel = str(path.relative_to(REPO))
            if rel.startswith(EXCLUDED):
                continue
            trees[rel] = ast.parse(path.read_text(encoding="utf-8"))
    sites: dict[tuple[str, str], int] = {}
    for rel, found in _scan_modules(trees).items():
        for function, _kind in found:
            if (rel, function) in CONSTRUCTOR:
                continue
            sites[(rel, function)] = sites.get((rel, function), 0) + 1
    _VIOLATIONS = sites
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


def _imports_of(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            names.update({base, *(f"{base}.{a.name}" for a in node.names)})
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_the_cli_only_modules_are_imported_by_clis_only() -> None:
    """The exclusions hold only while every importer is a CLI (a local process)."""
    cli_only = ("utils.errors", "read_cli")
    importers = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            rel = str(path.relative_to(REPO))
            if rel.startswith(EXCLUDED) or path.name == "cli.py":
                continue  # a CLI itself (an extension's command module is cli.py)
            imports = _imports_of(ast.parse(path.read_text(encoding="utf-8")))
            if any(i.endswith(m) or f"{m}." in i for i in imports for m in cli_only):
                importers.append(rel)
    assert importers == [], importers


#: Every place outside ``pyrite/exceptions.py`` that marks outside text with an
#: origin (``caller_owned``, ``kb_owned``, ``with_origin``, a parser's
#: ``origin=``), with its count, its class, and the test that fails without
#: it. The property: text marked caller-request holds only bytes the caller
#: sent; text marked KB(name) reaches only a caller with a read grant on that
#: KB. Each entry's class is one of:
#:
#: - ``caller bytes``: the parser is given only bytes of the caller's request;
#: - ``named KB``: the parser is given only bytes of the named KB's file;
#: - ``plumbing``: passes on the origin its own caller named, adding none.
#:
#: Any other way to set an origin is banned (``_mark_scan``), and a held
#: exception (``HeldFault``) honours no caller-request mark.
MARKS: dict[tuple[str, str], tuple[int, str, str]] = {
    ("pyrite/formats/importers/csv_importer.py", "import_csv"): (
        2,
        "caller bytes: decoding and csv parsing of the upload; neither is given a file name",
        "test_a_malformed_file_answers_parse_error_with_the_parsers_words",
    ),
    ("pyrite/formats/importers/json_importer.py", "import_json"): (
        2,
        "caller bytes: decoding and json.loads of the upload",
        "test_rest_import_names_where_the_callers_json_does_not_parse",
    ),
    ("pyrite/formats/importers/yaml_importer.py", "import_yaml"): (
        2,
        "caller bytes: decoding and the YAML parser over the upload",
        "test_rest_import_names_where_the_callers_upload_is_not_utf8",
    ),
    ("pyrite/formats/importers/markdown_importer.py", "import_markdown"): (
        1,
        "caller bytes: decoding the upload",
        "test_rest_import_names_where_the_callers_upload_is_not_utf8",
    ),
    ("pyrite/formats/importers/markdown_importer.py", "_next_entry"): (
        1,
        "caller bytes: the YAML parser over the next entry's frontmatter in the upload",
        "test_rest_import_stream_names_where_the_next_entrys_yaml_does_not_parse",
    ),
    ("pyrite/formats/importers/markdown_importer.py", "_parse_single_md"): (
        1,
        "caller bytes: the YAML parser over an uploaded entry's frontmatter",
        "test_rest_import_names_where_the_callers_markdown_yaml_does_not_parse",
    ),
    ("pyrite/services/search_service.py", "SearchService._db_search"): (
        1,
        "caller bytes: only when the query is the caller's own words (caller_wrote_query) "
        "and the FTS message is one of the parser's templates whose quoted part is a "
        "substring of it, never of the expanded query; otherwise withheld",
        "test_only_a_parser_template_quoting_the_callers_own_query_is_shown",
    ),
    ("pyrite/services/kb_service.py", "KBService._update"): (
        2,
        "named KB: apply() over this KB's file (kb_config.name, the resolved KB), with "
        "the caller's edit among its bytes",
        "test_an_update_names_its_kb_as_the_origin_of_the_file_text",
    ),
    ("pyrite/storage/repository.py", "KBRepository._frontmatter_of"): (
        1,
        "named KB: the YAML parser over this KB's entry file (self.name), whose bytes N1 "
        "reads only through KBFiles",
        "test_rest_qa_of_a_kb_file_whose_yaml_breaks_names_the_parsers_detail",
    ),
    ("pyrite/storage/file_operations.py", "apply"): (
        2,
        "plumbing: passes origin= on to _parse (the file before the edit) and "
        "_final_check (after it)",
        "test_a_file_on_disk_that_does_not_parse_is_the_kbs_text",
    ),
    ("pyrite/storage/file_operations.py", "_parse"): (
        2,
        "plumbing: with_origin(e, origin) on the composer's error; origin= on to "
        "frontmatter_refusal",
        "test_a_file_on_disk_that_does_not_parse_is_the_kbs_text",
    ),
    ("pyrite/storage/file_operations.py", "frontmatter_refusal"): (
        1,
        "plumbing: with_origin(e, origin) on the constructors' error",
        "test_a_file_on_disk_that_does_not_parse_is_the_kbs_text",
    ),
    ("pyrite/storage/file_operations.py", "_final_check"): (
        1,
        "plumbing: origin= on to load_frontmatter",
        "test_the_reread_after_an_edit_is_the_kbs_text_not_the_callers",
    ),
    ("pyrite/utils/frontmatter.py", "load_frontmatter"): (
        1,
        "plumbing: origin= on to load_yaml",
        "test_the_reread_after_an_edit_is_the_kbs_text_not_the_callers",
    ),
    ("pyrite/utils/yaml.py", "load_yaml_document"): (
        1,
        "plumbing: with_origin(e, origin) on the parser's error",
        "test_rest_import_names_where_the_callers_markdown_yaml_does_not_parse",
    ),
    ("pyrite/utils/yaml.py", "load_yaml"): (
        1,
        "plumbing: origin= on to load_yaml_document",
        "test_rest_qa_of_a_kb_file_whose_yaml_breaks_names_the_parsers_detail",
    ),
}
_MARKERS = {"caller_owned", "kb_owned", "with_origin"}
#: The attribute the constructor keeps an origin in (``exceptions._ORIGIN``),
#: and the value of a caller-request origin (``exceptions.CALLER_REQUEST``).
_ORIGIN_ATTRIBUTE = "_pyrite_text_origin"
_CALLER_REQUEST_VALUE = "caller-request"
#: The functions that take ``origin=``: a ``**`` unpacking into one of these
#: could carry an origin the scan cannot read.
_TAKES_ORIGIN = {
    "load_yaml",
    "load_yaml_document",
    "load_frontmatter",
    "apply",
    "_parse",
    "frontmatter_refusal",
    "_final_check",
}


def _mark_scan(tree: ast.Module) -> tuple[dict[str, int], list[str]]:
    """``(marks per function, bans)`` for one module.

    A mark is a direct call of a marker, or a call passing ``origin=``. A ban
    is any other way to set or carry an origin, where the scan cannot see
    which call it becomes:

    - the origin attribute named anywhere (``setattr(e, "_pyrite_text_origin",
      ...)``, ``e._pyrite_text_origin = ...``), or its name imported;
    - a marker imported under another name, used as a value (``mark =
      caller_owned``, ``map(kb_owned, ...)``), or named in a string
      (``getattr(exceptions, "caller_owned")``);
    - the caller-request value spelled as a string instead of
      ``CALLER_REQUEST``;
    - ``**`` keyword unpacking into a function that takes ``origin=``, or of a
      mapping that names ``origin`` (``f(**{"origin": o})``,
      ``f(**dict(origin=o))``).

    Stated limits (held by review, not by this scan): a marker or an origin
    reached through ``importlib``, ``globals()``, ``vars()``, ``eval`` or
    ``exec``; ``**`` unpacking of an opaque mapping into a function whose name
    is not in ``_TAKES_ORIGIN`` (a wrapper that forwards ``**kwargs`` to one).
    """
    marks: dict[str, int] = {}
    bans: list[str] = []
    called: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            called.add(id(node.func))

    def names_origin(mapping: ast.AST) -> bool:
        if isinstance(mapping, ast.Dict):
            return any(isinstance(k, ast.Constant) and k.value == "origin" for k in mapping.keys)
        if isinstance(mapping, ast.Call) and _call_name(mapping.func) == "dict":
            return any(k.arg == "origin" for k in mapping.keywords)
        return False

    def walk(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if named:
                stack.append(child.name)
            where = ".".join(stack) or "<module>"
            if isinstance(child, ast.Call) and (
                _call_name(child.func) in _MARKERS or any(k.arg == "origin" for k in child.keywords)
            ):
                marks[where] = marks.get(where, 0) + 1
            if isinstance(child, ast.Call):
                for kw in child.keywords:
                    if kw.arg is None and (
                        names_origin(kw.value) or _call_name(child.func) in _TAKES_ORIGIN
                    ):
                        bans.append(f"{where}: ** unpacking can carry an origin")
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value == _ORIGIN_ATTRIBUTE:
                    bans.append(f"{where}: names the origin attribute")
                elif child.value in _MARKERS:
                    bans.append(f"{where}: names {child.value} in a string")
                elif child.value == _CALLER_REQUEST_VALUE:
                    bans.append(f"{where}: spells the caller-request origin as a string")
            if isinstance(child, ast.Attribute) and child.attr == _ORIGIN_ATTRIBUTE:
                bans.append(f"{where}: sets or reads the origin attribute")
            if isinstance(child, ast.ImportFrom):
                for alias in child.names:
                    if alias.name in _MARKERS and alias.asname not in (None, alias.name):
                        bans.append(f"{where}: imports {alias.name} as {alias.asname}")
                    if alias.name == "_ORIGIN":
                        bans.append(f"{where}: imports the origin attribute's name")
            if (
                isinstance(child, (ast.Name, ast.Attribute))
                and _call_name(child) in _MARKERS
                and id(child) not in called
                and not isinstance(getattr(child, "ctx", None), ast.Store)
            ):
                bans.append(f"{where}: uses {_call_name(child)} as a value")
            walk(child, stack)
            if named:
                stack.pop()

    walk(tree, [])
    return marks, bans


def _marks() -> tuple[dict[tuple[str, str], int], list[str]]:
    found: dict[tuple[str, str], int] = {}
    bans: list[str] = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            rel = str(path.relative_to(REPO))
            if rel == "pyrite/exceptions.py":
                continue
            marks, banned = _mark_scan(ast.parse(path.read_text(encoding="utf-8")))
            for where, n in marks.items():
                found[(rel, where)] = found.get((rel, where), 0) + n
            bans += [f"{rel}: {b}" for b in banned]
    return found, bans


@functools.cache
def _test_names() -> frozenset[str]:
    """Every test function defined under ``tests/`` and ``extensions/*/tests``."""
    names: set[str] = set()
    for root in [REPO / "tests", *sorted((REPO / "extensions").glob("*/tests"))]:
        for path in root.rglob("test_*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_"):
                        names.add(node.name)
    return frozenset(names)


def test_every_mark_of_caller_text_is_listed_with_its_reason() -> None:
    found, bans = _marks()
    assert not bans, f"an origin set around the listed calls: {bans}"
    new = sorted((k, n) for k, n in found.items() if n > MARKS.get(k, (0, "", ""))[0])
    assert not new, (
        "outside text is given an origin here, unlisted. Mark only a parser's words "
        "about bytes the caller sent or a KB's file, and list the site in MARKS with "
        f"its class and the test that fails without it: {new}"
    )
    stale = sorted(k for k, (n, _why, _t) in MARKS.items() if found.get(k, 0) < n)
    assert not stale, f"no longer marked there: lower or remove the MARKS entry: {stale}"
    classes = ("caller bytes: ", "named KB: ", "plumbing: ")
    assert all(why.startswith(classes) for _n, why, _t in MARKS.values())


@pytest.mark.control(reason="the list's bookkeeping: it guards the next mark, not this change")
@pytest.mark.parametrize("site", sorted(MARKS), ids=lambda s: f"{s[0]}:{s[1]}")
def test_every_mark_names_a_test_that_exists(site) -> None:
    """A mark is listed with the test that fails without it, and that test is real."""
    guard = MARKS[site][2]
    assert guard in _test_names(), f"{site}: no test named {guard}"


_BANNED_MARKS = {
    "setattr of the origin attribute": """
def f(e):
    setattr(e, "_pyrite_text_origin", "caller-request")
""",
    "assignment to the origin attribute": """
def f(e):
    e._pyrite_text_origin = ("kb", "x")
""",
    "a marker imported under another name": """
from pyrite.exceptions import caller_owned as mine

def f(e):
    return mine(e)
""",
    "a marker bound to a name": """
from pyrite.exceptions import caller_owned

def f(e):
    mark = caller_owned
    return mark(e)
""",
    "a marker passed as a value": """
from pyrite import exceptions

def f(errors):
    return list(map(exceptions.kb_owned, errors))
""",
    "the origin attribute's name imported": """
from pyrite.exceptions import _ORIGIN
""",
    "a marker reached through getattr": """
from pyrite import exceptions

def f(e):
    return getattr(exceptions, "caller_owned")(e)
""",
    "the caller-request origin spelled as a string": """
def f(text):
    return parse(text, where="caller-request")
""",
    "an origin unpacked from a dict literal": """
def f(text, o):
    return parse(text, **{"origin": o})
""",
    "an origin unpacked from dict()": """
def f(text, o):
    return parse(text, **dict(origin=o))
""",
    "any mapping unpacked into a function that takes origin": """
def f(text, opts):
    return load_yaml(text, **opts)
""",
}


@pytest.mark.control(reason="tests the mark scan on planted source, not the change")
@pytest.mark.parametrize("shape", sorted(_BANNED_MARKS))
def test_a_mark_around_the_listed_calls_is_banned(shape: str) -> None:
    assert _mark_scan(ast.parse(_BANNED_MARKS[shape]))[1], shape


@pytest.mark.control(reason="tests the mark scan on planted source, not the change")
def test_a_direct_mark_is_counted_not_banned() -> None:
    marks, bans = _mark_scan(
        ast.parse(
            "from pyrite.exceptions import caller_owned, kb_origin\n"
            "def f(e, text):\n"
            "    load_yaml(text, origin=kb_origin('k'))\n"
            "    raise caller_owned(e) from None\n"
            "    return other(text, **opts)\n"
        )
    )
    assert marks == {"f": 2} and bans == []


@pytest.mark.control(reason="holds the reader side of a database write: true before and after")
def test_nothing_reads_the_embed_queue_error_back() -> None:
    """``embed_queue.error`` holds a fault's own text (the operator's, in the
    operator's database). The ratchet does not see a database write as a sink,
    so this holds the reader side: no SQL in Pyrite selects that column."""
    import re

    readers = []
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                sql = " ".join(node.value.split()).lower()
                if "embed_queue" in sql and re.search(r"select\b[^;]*\berror\b", sql):
                    readers.append(str(path.relative_to(REPO)))
    assert readers == [], readers


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
    "a helper hop that returns str(e)": """
def f():
    try:
        g()
    except OSError as e:
        return {"error": _describe(e)}

def _describe(exc):
    return str(exc)
""",
    "a helper handing the exception on to another helper": """
def f():
    try:
        g()
    except OSError as e:
        return _outer(e)

def _outer(err):
    return _inner(code=err)

def _inner(code):
    return f"failed: {code}"
""",
    "a method hop through self": """
class S:
    def f(self):
        try:
            g()
        except OSError as e:
            raise self._refusal(e)

    def _refusal(self, exc):
        return HTTPException(status_code=500, detail=str(exc))
""",
    "a parameter annotated as an exception": """
def describe(error: OSError | None):
    return {"cause": f"{error}"}
""",
    "the exception set on an attribute": """
class C:
    def load(self):
        try:
            g()
        except ImportError as exc:
            self.load_error = exc
""",
    "derived text set on a global": """
LAST = None

def f():
    global LAST
    try:
        g()
    except OSError as exc:
        LAST = f"failed: {exc}"
""",
    "derived text appended to a list": """
def f(warnings):
    try:
        g()
    except OSError as exc:
        warnings.append(f"skipped: {exc}")
""",
    "getattr of a non-safe attribute": """
def f():
    try:
        g()
    except subprocess.CalledProcessError as exc:
        return {"stderr": getattr(exc, "stderr", "")}
""",
    "traceback.format_exc into a result": """
def f():
    try:
        g()
    except OSError:
        return {"trace": traceback.format_exc()}
""",
    "derived text handed to a helper that returns it": """
def f():
    try:
        g()
    except OSError as exc:
        return _wrap(f"failed: {exc}")

def _wrap(message):
    return {"error": message}
""",
    "a non-fixed exception built from the text, kept and returned": """
def f():
    try:
        g()
    except OSError as e:
        err = ValidationError(f"bad: {e}")
        return {"error": err}
""",
    "text assigned in a handler, returned after it": """
def f():
    msg = ""
    try:
        g()
    except OSError as e:
        msg = str(e)
    return {"error": msg}
""",
    "text built with += in a handler, returned after it": """
def f():
    msg = "failed: "
    try:
        g()
    except OSError as e:
        msg += f"{e}"
    return msg
""",
    "an alias of the exception, stringified after the handler": """
def f():
    err = None
    try:
        g()
    except OSError as e:
        err = e
    return {"error": str(err)}
""",
    "derived text yielded": """
def f():
    try:
        g()
    except OSError as e:
        yield f"failed: {e}"
""",
    "derived text bound with a walrus": """
def f():
    try:
        g()
    except OSError as e:
        if (msg := str(e)):
            return msg
""",
    "the exception stored with setdefault": """
def f(d):
    try:
        g()
    except OSError as e:
        d.setdefault("error", e)
""",
    "derived text stored with update": """
def f(d):
    try:
        g()
    except OSError as e:
        d.update(error=f"{e}")
""",
    "str(sys.exc_info()[1])": """
def f():
    try:
        g()
    except OSError:
        return {"error": str(sys.exc_info()[1])}
""",
    "a task's exception stringified": """
def f(fut):
    if fut.exception() is not None:
        return {"error": f"{fut.exception()}"}
""",
    "strerror is the OS's text, not a code (D5)": """
def f():
    try:
        g()
    except OSError as e:
        raise ValidationError(f"cannot write: {e.strerror or e}")
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
    "a helper that passes the exception through public_text": """
def f():
    try:
        g()
    except OSError as e:
        return {"error": _safe(e)}

def _safe(exc):
    return public_text(exc)
""",
    "getattr of a code": """
def f():
    try:
        g()
    except OSError as exc:
        return {"errno": getattr(exc, "errno", None)}
""",
    "a fixed-message exception built from the text and returned": """
def _storage_error(entry_id, e: OSError):
    err = StorageError(f"Cannot write {entry_id}: {e}")
    return err
""",
    "the wrapped exception handed to a builder": """
def f():
    try:
        g()
    except DBAPIError as exc:
        raise storage_error_from(getattr(exc, "orig", exc), "failed") from exc
""",
    "a str parameter of a function sharing a helper's name": """
def f():
    try:
        g()
    except OSError as e:
        raise _refusal(e)

def _refusal(name: str, why: str = ""):
    return f"{name} is refused: {why}"
""",
    "the C library's text for the errno": """
def f():
    try:
        g()
    except OSError as e:
        raise ValidationError(f"cannot write: {os_reason(e)}")
""",
    "logger.exception takes a message, not an exception": """
def f():
    try:
        g()
    except OSError:
        logger.exception("failed")
        return {"error": "Could not read the file."}
""",
    "a handler's text name rebound to Pyrite's words before use": """
def f():
    msg = ""
    try:
        g()
    except OSError as e:
        msg = str(e)
        logger.warning(msg)
        msg = "Could not read the file."
    return {"error": msg}
""",
    "the exception kept in a local for a later raise": """
def f():
    last = None
    for _ in range(3):
        try:
            return g()
        except OSError as exc:
            last = exc
    raise StorageError("gave up") from last
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
