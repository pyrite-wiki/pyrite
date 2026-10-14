"""The demo's seeded KBs must stay on its public /site after the upgrade.

/site now renders only KBs with `default_role: read`. deploy/demo/seed.sh
wrote config.yaml without `default_role`, so a fresh demo's /site would be
empty. The test runs the config-writing snippet from seed.sh against a
temporary data dir.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from pyrite.config import PyriteConfig
from pyrite.services.public_kbs import public_kb_names
from pyrite.utils.yaml import load_yaml_file

SEED = Path(__file__).resolve().parent.parent / "deploy/demo/seed.sh"


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
    assert sorted(public_kb_names(config)) == ["demo-a", "pyrite-kb"]


def test_a_kb_yaml_default_role_wins(tmp_path):
    (tmp_path / "private-demo").mkdir()
    (tmp_path / "private-demo" / "kb.yaml").write_text(
        "name: private-demo\nkb_type: generic\ndefault_role: none\n"
    )
    data = _run_seed_config_snippet(tmp_path)
    assert public_kb_names(PyriteConfig.from_dict(data)) == []
