"""Run CONTRIBUTING's canonical setup in a fresh clone and temporary HOME."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from base_ref import resolve_base

REPO = Path(__file__).resolve().parents[1]


def setup_block(doc: Path) -> str:
    text = doc.read_text()
    section = text.split("<!-- contributor-setup:start -->", 1)[1].split(
        "<!-- contributor-setup:end -->", 1
    )[0]
    blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
    if len(blocks) != 1:
        raise ValueError("the canonical setup must contain exactly one bash block")
    return blocks[0]


def main() -> int:
    # Run this commit, not whichever dev tip happens to be fetched tomorrow.
    # The clone URL alone is replaced; every setup command is the doc's own.
    block = setup_block(REPO / "CONTRIBUTING.md")
    url = "https://github.com/pyrite-wiki/pyrite.git"
    if block.count(url) != 1:
        raise ValueError("canonical setup must clone the project once")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    base = subprocess.check_output(
        ["git", "rev-parse", resolve_base(REPO)], cwd=REPO, text=True
    ).strip()
    block = block.replace(url, shlex.quote(str(REPO)))
    # A local clone carries branches, not the source's remote-tracking refs.
    # Preserve the integration ref the source actually uses for the fork case.
    block = block.replace(
        "cd pyrite\n",
        f"cd pyrite\ngit checkout --detach {head}\ngit update-ref refs/remotes/upstream/dev {base}\n",
        1,
    )
    with tempfile.TemporaryDirectory(prefix="pyrite-setup-") as tmp:
        env = dict(os.environ)
        env["HOME"] = str(Path(tmp) / "home")
        Path(env["HOME"]).mkdir()
        # No caller Git location/config may redirect clone/setup writes.
        for key in list(env):
            if key.startswith("GIT_"):
                env.pop(key)
        git_config = Path(tmp) / "gitconfig"
        git_config.write_text("")
        env["GIT_CONFIG_GLOBAL"] = str(git_config)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env.pop("PYRITE_CONFIG_DIR", None)
        env.pop("PYRITE_DATA_DIR", None)
        env.pop("PYRITE_BASE", None)
        env.pop("PRE_COMMIT_FROM_REF", None)
        env.pop("PRE_COMMIT_TO_REF", None)
        env.pop("PYRITE_PUSH_FULL", None)
        env["COLUMNS"] = "240"
        env["NO_COLOR"] = "1"
        env.pop("FORCE_COLOR", None)
        result = subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + block],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
        )
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            return result.returncode
        expected_path = str(Path(tmp) / "pyrite" / "kb")
        if expected_path not in result.stdout or "design" not in result.stdout.lower():
            print("setup walkthrough: KB path or design search result missing", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
