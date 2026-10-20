"""Commands for adding extras to source installs (there is no PyPI wheel)."""

import json
import shlex
from importlib.metadata import PackageNotFoundError, distribution
from urllib.parse import unquote, urlsplit


def extra_install_command(extra: str) -> str:
    """Use PEP 610 metadata to keep the current checkout or VCS revision."""
    try:
        raw = distribution("pyrite").read_text("direct_url.json")
        data = json.loads(raw) if raw else {}
    except (PackageNotFoundError, OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    url = data.get("url", "")
    directory = data.get("dir_info", {})
    vcs = data.get("vcs_info", {})
    if isinstance(url, str) and url.startswith("file:") and isinstance(directory, dict):
        if directory.get("editable"):
            parts = urlsplit(url)
            path = unquote(parts.path)
            if parts.netloc:
                path = f"//{parts.netloc}{path}"
            elif len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            return "pip install -e " + shlex.quote(f"{path}[{extra}]")
    if isinstance(url, str) and isinstance(vcs, dict) and vcs.get("vcs") == "git":
        revision = vcs.get("commit_id") or vcs.get("requested_revision")
        source = "git+" + url
        if revision:
            source += "@" + str(revision)
        if data.get("subdirectory"):
            source += "#subdirectory=" + str(data["subdirectory"])
    else:
        source = "git+https://github.com/pyrite-wiki/pyrite.git@dev"
    return "pip install " + shlex.quote(f"pyrite[{extra}] @ {source}")
