"""Rules about how the test suite itself is wired (test-architecture review, rule 11)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cheap_bcrypt_patch_lives_only_in_the_root_conftest():
    """The test-only bcrypt cost is set in one place, the root conftest.py.

    A second patch (in another conftest or a test file) would drift from it, and
    a production hook would let a real install lower its own cost.
    """
    offenders = []
    for base in (ROOT / "tests", ROOT / "extensions"):
        for path in base.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(errors="ignore")
            if (
                "auth_service._bcrypt" in text
                or '"_bcrypt"' in text
                or "_DUMMY_PASSWORD_HASH" in text
            ):
                if not text.lstrip().startswith("#") and "test_auth" not in path.name:
                    offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"bcrypt cost patched outside the root conftest: {offenders}"
    root = (ROOT / "conftest.py").read_text()
    assert "_cheap_password_hashing" in root


def test_production_never_reads_a_test_cost_switch():
    src = (ROOT / "pyrite" / "services" / "auth_service.py").read_text()
    assert "gensalt()" in src and "rounds" not in src and "_TEST_BCRYPT" not in src


# --- tests-t1: no test can hang the suite --------------------------------------
#
# pytest-timeout (pyproject) is the backstop: a hung test dies at the ceiling and
# names itself. These rules keep each wait short of that ceiling and local to
# the call that waits, so the failure names the call, not the 600 s.

import ast  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402

import pytest  # noqa: E402

_SUBPROCESS_CALLS = {"run", "check_output", "check_call", "call"}
_WAITS = {"communicate", "wait", "join"}
_WS_RECEIVES = {"receive_json", "receive_text", "receive_bytes"}
_NETWORK = {"urlopen", "create_connection"}


def _is_deadline(node: ast.expr | None) -> bool:
    """A real deadline: present and not the literal ``None`` (which waits forever)."""
    return node is not None and not (isinstance(node, ast.Constant) and node.value is None)


def _module_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """({local module name: real module}, {local name: "module.attr"}) for imports."""
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                modules[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                names[a.asname or a.name] = f"{n.module}.{a.name}"
    return modules, names


def _dicts_with_a_deadline(tree: ast.AST) -> set[str]:
    """Names assigned a dict literal / ``dict(...)`` whose ``timeout`` is a real deadline."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Assign) and len(n.targets) == 1):
            continue
        target, value = n.targets[0], n.value
        if not isinstance(target, ast.Name):
            continue
        if isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values, strict=True):
                if isinstance(k, ast.Constant) and k.value == "timeout" and _is_deadline(v):
                    out.add(target.id)
        elif isinstance(value, ast.Call) and ast.unparse(value.func) == "dict":
            if any(k.arg == "timeout" and _is_deadline(k.value) for k in value.keywords):
                out.add(target.id)
    return out


def unbounded_calls(source: str) -> list[tuple[int, str]]:
    """Calls in ``source`` that can wait forever: (line, what).

    Forms judged (each has an offender and a clean case in the tests below):
    ``subprocess.run|check_output|check_call|call`` and ``Popen.communicate`` /
    ``Popen.wait`` / ``Thread.join`` / ``Event.wait`` / ``Barrier.wait`` /
    ``Barrier(...)`` need a deadline, as a keyword or in its positional slot;
    ``timeout=None`` is not one; ``**opts`` counts only when ``opts`` is a dict
    built in the module with a real ``timeout``; ``with Popen(...)`` waits
    forever on exit; ``import subprocess as sp`` / ``from subprocess import run
    as r`` are resolved; a ``.wait()`` is a Barrier's only if the owner was bound
    to ``Barrier(...)`` or annotated as one; websocket ``receive_*`` must use
    ``tests.ws_helpers``; ``urlopen`` / ``create_connection`` need a deadline.
    """
    tree = ast.parse(source)
    modules, names = _module_aliases(tree)
    ok_dicts = _dicts_with_a_deadline(tree)
    awaited = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Await)}

    def qualified(fn: ast.expr) -> str:
        """``subprocess.run`` for ``sp.run`` / ``run`` / ``subprocess.run``."""
        if isinstance(fn, ast.Name):
            return names.get(fn.id, fn.id)
        if isinstance(fn, ast.Attribute):
            base = qualified(fn.value)
            head, _, rest = base.partition(".")
            head = modules.get(head, head)
            return f"{head}{'.' + rest if rest else ''}.{fn.attr}"
        return ast.unparse(fn)

    def deadline(call: ast.Call, position: int | None = None) -> bool:
        for k in call.keywords:
            if k.arg == "timeout":
                return _is_deadline(k.value)
            if k.arg is None and isinstance(k.value, ast.Name) and k.value.id in ok_dicts:
                return True
        return (
            position is not None and len(call.args) > position and _is_deadline(call.args[position])
        )

    # Barriers: names/attributes bound to Barrier(...), or annotated as one.
    barriers: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            if qualified(n.value.func) == "threading.Barrier":
                barriers.update(ast.unparse(t) for t in n.targets)
        elif isinstance(n, ast.arg) and n.annotation is not None:
            if qualified(n.annotation) in {"threading.Barrier", "Barrier"}:
                barriers.add(n.arg)

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and qualified(ctx.func) == "subprocess.Popen":
                    found.append((ctx.lineno, "`with Popen(...)` waits forever on exit"))
        if not isinstance(node, ast.Call):
            continue
        q = qualified(node.func)
        if q.startswith("subprocess.") and q.split(".")[1] in _SUBPROCESS_CALLS:
            if not deadline(node):
                found.append((node.lineno, f"{q}() without a real timeout="))
        elif q == "threading.Barrier":
            if not deadline(node, 2):
                found.append((node.lineno, "threading.Barrier() without a real timeout="))
        elif q in {"urllib.request.urlopen", "socket.create_connection"}:
            if not deadline(node, 2 if q.endswith("urlopen") else 1):
                found.append((node.lineno, f"{q}() without a real timeout="))
        elif isinstance(node.func, ast.Attribute):
            attr, owner = node.func.attr, ast.unparse(node.func.value)
            if attr in _WS_RECEIVES and not node.args:
                found.append(
                    (node.lineno, f"{owner}.{attr}() blocks forever: use tests.ws_helpers")
                )
            elif attr in _WAITS and id(node) not in awaited:
                if owner in {"os", "os.path", "posixpath"}:
                    continue  # os.path.join is not a wait
                if owner in barriers:
                    continue  # its construction was judged above
                if not deadline(node, 1 if attr == "communicate" else 0):
                    found.append((node.lineno, f"{owner}.{attr}() without a real timeout"))
    return sorted(found)


def _scanned_files() -> list[Path]:
    files = [*(ROOT / "tests").rglob("*.py"), *ROOT.glob("extensions/*/tests/**/*.py")]
    return sorted(files)


@pytest.mark.parametrize("path", _scanned_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_test_call_can_wait_forever(path):
    offenders = unbounded_calls(path.read_text())
    assert not offenders, (
        f"{path.relative_to(ROOT)} has calls with no deadline (tests-t1). Give each a "
        f"timeout= (or use tests.ws_helpers.receive_json for sockets): {offenders}"
    )


_OFFENDERS = {
    "run": "import subprocess\nsubprocess.run(['x'])",
    "check_output": "import subprocess\nsubprocess.check_output(['x'])",
    "timeout=None is no deadline": "import subprocess\nsubprocess.run(['x'], timeout=None)",
    "alias of the module": "import subprocess as sp\nsp.run(['x'])",
    "from-import": "from subprocess import run\nrun(['x'])",
    "from-import alias": "from subprocess import check_call as cc\ncc(['x'])",
    "**opts with no timeout": "import subprocess\nopts = {'text': True}\nsubprocess.run(['x'], **opts)",
    "**opts with timeout None": (
        "import subprocess\nopts = {'timeout': None}\nsubprocess.run(['x'], **opts)"
    ),
    "**opts from elsewhere": "import subprocess\nsubprocess.run(['x'], **make_opts())",
    "communicate()": "p.communicate()",
    "communicate(data) has no deadline": "p.communicate(b'data')",
    "communicate(data, None)": "p.communicate(b'data', None)",
    "wait()": "p.wait()",
    "wait(None)": "p.wait(None)",
    "wait(timeout=None)": "p.wait(timeout=None)",
    "join()": "t.join()",
    "join(None)": "t.join(None)",
    "with Popen waits on exit": "import subprocess\nwith subprocess.Popen(['x']) as p:\n    pass",
    "with Popen alias": "import subprocess as sp\nwith sp.Popen(['x']) as p:\n    pass",
    "Barrier()": "import threading\nthreading.Barrier(2)",
    "Barrier alias": "import threading as th\nth.Barrier(2)",
    "Barrier from-import": "from threading import Barrier\nBarrier(2)",
    "Barrier(timeout=None)": "import threading\nthreading.Barrier(2, timeout=None)",
    "wait on an unknown barrier-named owner": "barrier.wait()",
    "wait on a barrier built without a deadline": (
        "import threading\nb = threading.Barrier(2)\nb.wait(timeout=None)"
    ),
    "ws receive": "ws.receive_json()",
    "urlopen": "from urllib.request import urlopen\nurlopen('http://x')",
    "create_connection": "import socket\nsocket.create_connection(('h', 1))",
}

_CLEAN = {
    "run": "import subprocess\nsubprocess.run(['x'], timeout=5)",
    "alias": "import subprocess as sp\nsp.run(['x'], timeout=5)",
    "from-import": "from subprocess import run\nrun(['x'], timeout=5)",
    "**opts carrying a deadline": (
        "import subprocess\nopts = {'timeout': 5}\nsubprocess.run(['x'], **opts)"
    ),
    "**dict() carrying a deadline": (
        "import subprocess\nopts = dict(timeout=5)\nsubprocess.run(['x'], **opts)"
    ),
    "communicate(timeout=)": "p.communicate(timeout=5)",
    "communicate(data, 5)": "p.communicate(b'data', 5)",
    "wait(5)": "p.wait(5)",
    "wait(timeout=)": "p.wait(timeout=5)",
    "join(timeout=)": "t.join(timeout=5)",
    "os.path.join": "import os\nos.path.join('a', 'b')",
    "str.join": "','.join(items)",
    "Popen without with": "import subprocess\np = subprocess.Popen(['x'])\np.wait(timeout=5)",
    "Barrier with a deadline and its waits": (
        "import threading\nb = threading.Barrier(2, timeout=5)\nb.wait()\n"
        "class C:\n    def go(self):\n        self.s = threading.Barrier(2, None, 5)\n"
        "        self.s.wait()"
    ),
    "barrier passed in, annotated": (
        "import threading\ndef w(b: threading.Barrier):\n    b.wait()"
    ),
    "async wait": "async def f():\n    await ev.wait()",
    "ws helper": "receive_json(ws)",
    "urlopen(timeout=)": "from urllib.request import urlopen\nurlopen('http://x', timeout=5)",
}


@pytest.mark.parametrize("source", _OFFENDERS.values(), ids=_OFFENDERS.keys())
def test_the_rule_flags_every_form_of_unbounded_call(source):
    assert unbounded_calls(source), source


@pytest.mark.parametrize("source", _CLEAN.values(), ids=_CLEAN.keys())
def test_the_rule_accepts_every_bounded_form(source):
    assert unbounded_calls(source) == [], source


_STUCK = {
    "test body": "def test_stuck():\n    time.sleep(60)\n",
    "fixture setup": (
        "@pytest.fixture\ndef stuck():\n    time.sleep(60)\n\ndef test_stuck(stuck):\n    pass\n"
    ),
    "fixture teardown": (
        "@pytest.fixture\ndef stuck():\n    yield\n    time.sleep(60)\n\n"
        "def test_stuck(stuck):\n    pass\n"
    ),
}


@pytest.mark.parametrize("body", _STUCK.values(), ids=_STUCK.keys())
def test_a_stuck_test_or_fixture_is_killed_and_its_stack_dumped(tmp_path, body):
    """The backstop works end to end, with the repo's own pytest config values:
    pytest-timeout kills it, faulthandler dumps the stuck thread's stack."""
    import tomllib

    opts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    # Same option names as the repo config, scaled down; timeout_func_only is
    # deliberately not passed, so whatever the default is applies.
    (tmp_path / "test_stuck.py").write_text("import time\nimport pytest\n\n" + body)
    assert "timeout_func_only" not in opts
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-o",
            "timeout=3",
            "-o",
            "faulthandler_timeout=1",
            str(tmp_path / "test_stuck.py"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=120,
    )
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert "Timeout (>3.0s) from pytest-timeout" in out, out  # pytest-timeout fired
    assert "Timeout (0:00:01)!" in out, out  # faulthandler dumped the stacks
    assert "most recent call first" in out and "test_stuck.py" in out, out


def test_pytest_config_bounds_every_test_and_dumps_stacks():
    import tomllib

    opts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert 0 < opts["timeout"] <= 600
    # fixture setup/teardown stay under the ceiling
    assert opts.get("timeout_func_only", False) is False
    assert 0 < opts["faulthandler_timeout"] < opts["timeout"]


def test_ci_pytest_jobs_have_a_job_timeout():
    import yaml

    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    for name, job in jobs.items():
        runs_pytest = any("pytest" in str(step.get("run", "")) for step in job.get("steps", []))
        if runs_pytest:
            assert "timeout-minutes" in job, (
                f"ci.yml job {name!r} runs pytest with no timeout-minutes"
            )
