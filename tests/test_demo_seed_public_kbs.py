"""The demo's seeded KBs are open to anonymous readers.

deploy/demo/seed.sh wrote config.yaml without `default_role`; the demo is
public, so each seeded KB gets `default_role: read` unless its kb.yaml says
otherwise. The test runs the config-writing snippet from seed.sh against a
temporary data dir and asks the access policy what an anonymous visitor
gets.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from pyrite.config import PyriteConfig
from pyrite.services.access_policy import kb_role
from pyrite.utils.yaml import load_yaml_file

SEED = Path(__file__).resolve().parent.parent / "deploy/demo/seed.sh"


def _anonymous_readable(config: PyriteConfig) -> list[str]:
    """The KBs the access policy lets an anonymous visitor (tier read) read."""
    return sorted(
        kb.name
        for kb in config.all_kbs()
        if kb_role(None, None, lambda: None, kb.default_role, "read") is not None
    )


def _run_seed_config_snippet(data_dir: Path) -> dict:
    script = SEED.read_text()
    snippet = re.search(r'python3 -c "\n(.*?)\n"\n', script, re.S).group(1)
    snippet = snippet.replace("$DATA_DIR", str(data_dir))
    subprocess.run([sys.executable, "-c", snippet], check=True, capture_output=True, timeout=120)
    return load_yaml_file(data_dir / "config.yaml")


def test_seeded_kbs_are_public(tmp_path):
    for name in ("pyrite-kb", "demo-a"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "kb.yaml").write_text(f"name: {name}\nkb_type: generic\n")
    data = _run_seed_config_snippet(tmp_path)
    config = PyriteConfig.from_dict(data)
    assert {kb.name: kb.default_role for kb in config.all_kbs()} == {
        "demo-a": "read",
        "pyrite-kb": "read",
    }
    assert _anonymous_readable(config) == ["demo-a", "pyrite-kb"]


def test_a_kb_yaml_default_role_wins(tmp_path):
    (tmp_path / "private-demo").mkdir()
    (tmp_path / "private-demo" / "kb.yaml").write_text(
        "name: private-demo\nkb_type: generic\ndefault_role: none\n"
    )
    data = _run_seed_config_snippet(tmp_path)
    assert _anonymous_readable(PyriteConfig.from_dict(data)) == []
