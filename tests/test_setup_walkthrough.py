"""The canonical contributor setup is executable, not four copied recipes."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

from pyrite.config import resolve_config_dir

REPO = Path(__file__).resolve().parents[1]


def test_canonical_setup_registers_and_queries_checkout_kb():
    text = (REPO / "CONTRIBUTING.md").read_text()
    assert "<!-- contributor-setup:start -->" in text
    block = text.split("<!-- contributor-setup:start -->", 1)[1].split(
        "<!-- contributor-setup:end -->", 1
    )[0]
    assert "scripts/setup-checkout.sh" in block
    assert ".venv/bin/pyrite kb list" in block
    assert ".venv/bin/pyrite search design -k pyrite" in block
    assert "scripts/test-affected --list" in block


def test_setup_is_linked_not_repeated():
    for name in ["CLAUDE.md", "AGENTS.md", "kb/runbooks/setting-up-dev-environment.md"]:
        text = (REPO / name).read_text()
        assert "CONTRIBUTING.md#initial-setup" in text
        assert 'pip install -e ".[dev]"' not in text
    assert "python3 -m venv" not in (REPO / "kb/runbooks/setting-up-dev-environment.md").read_text()


def test_walkthrough_copies_exact_documented_block(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "setup_walkthrough", REPO / "scripts/run_setup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    doc = tmp_path / "setup.md"
    doc.write_text(
        "outside\n<!-- contributor-setup:start -->\n```bash\necho setup\n```\n<!-- contributor-setup:end -->\n"
    )
    assert module.setup_block(doc) == "echo setup\n"


def test_walkthrough_does_not_inherit_operator_data_directory(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "setup_walkthrough", REPO / "scripts/run_setup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operator = tmp_path / "operator"
    operator.mkdir()
    registry = operator / "config.yaml"
    registry.write_text("knowledge_bases: []\n")
    monkeypatch.setenv("PYRITE_DATA_DIR", str(operator))
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(operator))
    monkeypatch.setattr(module, "resolve_base", lambda root: "dev")
    monkeypatch.setattr(module.subprocess, "check_output", lambda *args, **kwargs: "a" * 40)

    def run(args, *, cwd, env, **kwargs):
        # At the actual subprocess boundary: the installed CLI must choose
        # the temporary checkout, not either inherited operator override.
        assert args[0] == "bash"
        assert "PYRITE_DATA_DIR" not in env
        assert "PYRITE_CONFIG_DIR" not in env
        clone_kb = Path(cwd) / "pyrite" / "kb"
        clone_config = clone_kb.parent / ".pyrite"
        clone_config.mkdir(parents=True)
        (clone_config / "config.yaml").write_text("knowledge_bases: []\n")
        with patch.dict("os.environ", env, clear=True):
            assert resolve_config_dir(clone_kb.parent) == clone_config
        return subprocess.CompletedProcess(args, 0, f"{clone_kb}\ndesign\n", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main() == 0
    assert registry.read_text() == "knowledge_bases: []\n"
    assert list(operator.iterdir()) == [registry]
