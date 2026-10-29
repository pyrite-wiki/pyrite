"""Installs come from one locked set of versions (#1357).

`uv.lock` is the one lock (Dependabot's `uv` ecosystem maintains it, and
`scripts/lock-deps.sh --check` verifies it). Installs keep `uv pip install -e`
and apply it as constraints exported on the spot by
`scripts/lock-constraints.sh`; nothing derived is committed. The one
scheduled job that deliberately does not is `.github/workflows/unlocked.yml`,
which tells us when the newest allowed versions break Pyrite (what PyPI users
get). `pyproject.toml` keeps its ranges.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "uv.lock"
CONSTRAINTS = '-c "$(scripts/lock-constraints.sh)"'
CI = REPO / ".github" / "workflows" / "ci.yml"
UNLOCKED = REPO / ".github" / "workflows" / "unlocked.yml"
SETUP = REPO / "scripts" / "setup-checkout.sh"

# An install of this checkout or an extension: `uv pip install ... -e ...` or
# `pip install ... -e ...`, however the line is wrapped or indented.
_INSTALL = re.compile(r"(?:uv pip|pip) install\b[^\n]*?\s-e\s[^\n]*")


def _install_lines(path: Path) -> list[str]:
    return [m.group(0) for m in _INSTALL.finditer(path.read_text())]


def _pins() -> dict[str, list[Version]]:
    pins: dict[str, list[Version]] = {}
    for pkg in tomllib.loads(LOCK.read_text())["package"]:
        if "version" in pkg:
            pins.setdefault(canonicalize_name(pkg["name"]), []).append(Version(pkg["version"]))
    return pins


class TestLockCoversInstalls:
    def test_ci_has_install_sites(self):
        # Guards the guard: a regex that matches nothing would pass vacuously.
        assert len(_install_lines(CI)) >= 15

    @pytest.mark.parametrize("path", [CI, SETUP], ids=lambda p: p.name)
    def test_every_install_uses_the_lock(self, path):
        offenders = [
            ln.strip()
            for ln in _install_lines(path)
            if CONSTRAINTS not in ln and '-c "$constraints"' not in ln
        ]
        assert not offenders, f"{path.name}: installs not constrained by uv.lock: {offenders}"

    def test_setup_checkout_locks_with_and_without_uv_installed(self):
        text = SETUP.read_text()
        # One install path serves both: without uv on PATH the script first
        # puts a private uv in the venv, and the install is the same line.
        assert re.search(r"^\s*\.venv/bin/pip install -q uv\b", text, re.M)
        assert re.search(r'"\$UV" pip install .*-c "\$constraints" -e', text)
        assert "scripts/lock-constraints.sh" in text

    def test_setup_checkout_stamp_includes_the_lock(self):
        # Without it a pulled lock bump would be skipped by `--if-changed`.
        assert "uv.lock" in re.search(r"stamp=.*", SETUP.read_text()).group(0)

    def test_ci_cache_keys_include_the_lock(self):
        # uv's wheel cache is keyed on these files; a lock bump must miss it.
        blocks = re.findall(r"cache-dependency-glob:([^\n]*(?:\n {12}\S[^\n]*)*)", CI.read_text())
        assert blocks
        assert all("uv.lock" in b for b in blocks), blocks

    def test_ci_checks_the_lock_is_not_stale(self):
        assert "scripts/lock-deps.sh --check" in CI.read_text()

    def test_lock_change_runs_the_full_matrix(self):
        text = CI.read_text()
        infra = text[text.index("            infra:") :]
        assert "uv.lock" in infra
        assert "'uv.lock'" in text[: text.index("            web:")]  # backend filter


class TestLockMatchesPyproject:
    """Offline staleness check; CI's `lock-deps.sh --check` is the full one."""

    def _requirements(self) -> list[Requirement]:
        data = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
        raw = list(data["dependencies"])
        for extra in data["optional-dependencies"].values():
            raw += extra
        reqs = [Requirement(r) for r in raw]
        return [r for r in reqs if canonicalize_name(r.name) != "pyrite"]

    def test_every_declared_dependency_is_pinned_inside_its_range(self):
        pins = _pins()
        problems = []
        for req in self._requirements():
            versions = pins.get(canonicalize_name(req.name))
            if not versions:
                problems.append(f"{req.name}: not in uv.lock")
            elif not any(req.specifier.contains(v, prereleases=True) for v in versions):
                problems.append(f"{req.name}: pinned {versions}, pyproject wants {req.specifier}")
        assert not problems, "uv.lock is stale; run scripts/lock-deps.sh: " + "; ".join(problems)

    def test_pyproject_ranges_are_still_ranges(self):
        # The lock is for us, not for users: the wheel's metadata keeps ranges.
        data = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
        loose = [d for d in data["dependencies"] if ">=" in d]
        assert len(loose) >= 5


class TestUnlockedCanary:
    def test_weekly_job_installs_without_the_lock_and_runs_the_suite(self):
        import yaml

        wf = yaml.safe_load(UNLOCKED.read_text())
        triggers = wf.get("on") or wf.get(True)
        assert "schedule" in triggers and "workflow_dispatch" in triggers
        text = UNLOCKED.read_text()
        assert '".[all]"' in text
        assert "pytest" in text
        # Comments may mention the lock; no command may apply it.
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        assert "lock-constraints" not in code and "-c " not in code


class TestDependabotKeepsTheLockCurrent:
    def test_python_entry_is_the_uv_ecosystem_on_dev(self):
        import yaml

        updates = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text())["updates"]
        entry = [u for u in updates if u["package-ecosystem"] == "uv"]
        assert len(entry) == 1 and entry[0]["target-branch"] == "dev"
        assert entry[0]["directory"] == "/" and entry[0]["groups"]
        # `pip` cannot read uv.lock; keeping both would open two PRs for one set.
        assert not [u for u in updates if u["package-ecosystem"] == "pip"]

    def test_security_floors_are_declared(self):
        data = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
        core = {
            canonicalize_name(Requirement(r).name): Requirement(r) for r in data["dependencies"]
        }
        server = {
            canonicalize_name(Requirement(r).name): Requirement(r)
            for r in data["optional-dependencies"]["server"]
        }
        floors = [
            ("certifi", core, "2026.10.19"),
            ("anyio", core, "4.14.2"),
            ("python-multipart", server, "0.0.31"),
            ("cryptography", server, "50.0.0"),
        ]
        for name, where, floor in floors:
            assert where[name].specifier.contains(Version(floor)), name
            assert not where[name].specifier.contains(Version("0.0.1")), name


def test_uv_lock_is_tracked_not_ignored():
    import subprocess

    ignored = (
        subprocess.run(["git", "check-ignore", "-q", "uv.lock"], cwd=REPO, timeout=60).returncode
        == 0
    )
    assert not ignored, ".gitignore hides uv.lock, so the lock could never be committed"
    assert LOCK.exists()
