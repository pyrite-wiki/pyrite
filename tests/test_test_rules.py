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
