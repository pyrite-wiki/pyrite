"""Branding Service — white-label configuration loader.

Loads a branding folder containing ``branding.yaml`` plus optional
logo/wordmark/favicon/og-image assets. Used to re-skin the Pyrite UI
for branded deployments (e.g. investigate.transparencycascade.org)
without forking the code.

Pyrite identity is never hidden: the "Powered by Pyrite" credit is
always rendered in the UI footer and KB export trailer. This service
only governs the operator-configurable chrome above it.

See ``kb/backlog/pyrite-white-labeling.md`` for the full spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exceptions import BrandingInvalidError
from ..utils.yaml import load_yaml_file

logger = logging.getLogger(__name__)

DEFAULT_BRAND_NAME = "Pyrite"
DEFAULT_PRIMARY_COLOR = "#d4a017"  # gold-400 equivalent — today's accent
DEFAULT_FOOTER_CREDIT_URL = "https://pyrite.wiki"

# Module-level, keyed on the resolved branding.yaml path, each holding at
# most one ((mtime_ns, size), message) pair (#445 delta cold read): every
# request to an anonymous route (/config/branding, and the MCP prompt and
# README footer that read the brand) that hits a broken branding.yaml
# builds its own BrandingService, so without this, a stuck operator typo
# logs a full traceback on every single page load. Keyed on
# (mtime, size), not just the path, so a fixed file is re-read and
# re-logged on its next real failure instead of staying silent forever --
# and keyed per-path (not a single shared slot) so two different branding
# dirs in the same process (e.g. two tests, or two ephemeral configs) don't
# evict each other's cached failure.
#
# The value is the exception's MESSAGE (a str), not the exception object
# itself (#445 round 3): re-raising the same exception instance on every
# cache hit appends a frame to its __traceback__ every time, so the cached
# object's traceback -- and every subsequent request's logged traceback --
# grows without bound. A cache hit constructs a fresh BrandingInvalidError
# from the stored message instead.
_failure_cache: dict[str, tuple[tuple[int, int], str]] = {}


@dataclass
class BrandingConfig:
    """Loaded branding configuration. Immutable after load."""

    name: str = DEFAULT_BRAND_NAME
    tagline: str = ""
    primary_color: str = DEFAULT_PRIMARY_COLOR
    site_url: str = ""
    support_url: str = ""
    footer_credit_url: str = DEFAULT_FOOTER_CREDIT_URL

    # Asset filenames (relative to the branding dir). None when absent.
    logo: str | None = None
    wordmark: str | None = None
    favicon: str | None = None
    apple_touch_icon: str | None = None
    og_image: str | None = None

    # Theme variants (optional, override the single-tone logo/wordmark).
    logo_light: str | None = None
    logo_dark: str | None = None
    wordmark_light: str | None = None
    wordmark_dark: str | None = None

    invert_on_dark: bool = False

    meta_description: str = ""

    # MCP agent system-prompt brand (defaults to `name` if unset).
    mcp_agent_prompt_brand: str = ""

    # Absolute path to the branding dir, or None when using defaults.
    # Used internally by BrandingService; not exposed via to_public_dict().
    branding_dir: Path | None = field(default=None, repr=False)

    def logo_url(self) -> str | None:
        return f"/branding/{self.logo}" if self.logo else None

    def wordmark_url(self) -> str | None:
        return f"/branding/{self.wordmark}" if self.wordmark else None

    def favicon_url(self) -> str | None:
        return f"/branding/{self.favicon}" if self.favicon else None

    def og_image_url(self) -> str | None:
        return f"/branding/{self.og_image}" if self.og_image else None

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize for the public /config/branding endpoint.

        Omits internal fields (branding_dir). Resolves asset filenames
        to /branding/* URLs.
        """
        return {
            "name": self.name,
            "tagline": self.tagline,
            "primary_color": self.primary_color,
            "site_url": self.site_url,
            "support_url": self.support_url,
            "footer_credit_url": self.footer_credit_url,
            "logo_url": self.logo_url(),
            "wordmark_url": self.wordmark_url(),
            "favicon_url": self.favicon_url(),
            "og_image_url": self.og_image_url(),
            "invert_on_dark": self.invert_on_dark,
            "meta": {"description": self.meta_description},
        }


class BrandingService:
    """Loads and serves BrandingConfig from a folder.

    If the folder is None, missing, or empty, falls back to Pyrite
    defaults. Config is loaded lazily on first get() and cached.
    """

    def __init__(self, branding_dir: Path | str | None):
        self._branding_dir: Path | None = Path(branding_dir) if branding_dir else None
        self._config: BrandingConfig | None = None

    def get(self) -> BrandingConfig:
        if self._config is None:
            self._config = self._load()
        return self._config

    def reload(self) -> BrandingConfig:
        """Force reload (useful if the branding folder changed on disk)."""
        self._config = None
        return self.get()

    def resolve_asset(self, filename: str) -> Path | None:
        """Resolve an asset filename to an absolute path within the branding dir.

        Returns None if the branding dir is not set, the file doesn't
        exist, or the resolved path escapes the branding dir (traversal
        attempts).
        """
        if self._branding_dir is None or not self._branding_dir.is_dir():
            return None
        # Reject absolute paths and any "../" traversal attempts. Resolve
        # both sides to compare with symlinks normalized.
        candidate = (self._branding_dir / filename).resolve()
        try:
            base = self._branding_dir.resolve()
            candidate.relative_to(base)
        except ValueError:
            logger.warning("Asset traversal attempt rejected: %s", filename)
            return None
        if not candidate.is_file():
            return None
        return candidate

    # ------------------------------------------------------------------

    def _load(self) -> BrandingConfig:
        if self._branding_dir is None or not self._branding_dir.is_dir():
            return BrandingConfig()

        yaml_path = self._branding_dir / "branding.yaml"
        if not yaml_path.is_file():
            return BrandingConfig(branding_dir=self._branding_dir)

        try:
            st = yaml_path.stat()
        except FileNotFoundError:
            # TOCTOU: is_file() passed above, then something (a rewrite via
            # temp-file-and-rename, a delete) removed it before this stat()
            # ran (#445 round 3). Treat exactly like "never had a
            # branding.yaml" rather than letting the raw OS error escape.
            return BrandingConfig(branding_dir=self._branding_dir)

        path_key = str(yaml_path)
        # (mtime_ns, size), not mtime alone (#445 round 3): a filesystem
        # with 1-second mtime granularity, or a copy tool that preserves
        # timestamps (`cp -p`, `rsync -t`, most tar extraction), can leave
        # an edited file's mtime unchanged from the broken version's --
        # keyed on mtime alone, a fixed file would keep re-raising the
        # stale cached failure forever. Comparing the byte size too catches
        # any edit that isn't a same-second, same-length in-place overwrite
        # (not a content hash, but far cheaper, and covers the realistic
        # "operator fixed a typo" case).
        cache_key = (st.st_mtime_ns, st.st_size)
        cached = _failure_cache.get(path_key)
        if cached is not None and cached[0] == cache_key:
            # Same file, same (mtime, size) as a failure already logged:
            # raise a FRESH exception carrying the same message, not the
            # stored object itself (see _failure_cache above) -- re-raising
            # the same object appends a frame to its __traceback__ on every
            # raise, so the cached exception's traceback (and therefore
            # every /config/branding request's logged traceback) grows
            # without bound across repeat requests (#445 round 3).
            raise BrandingInvalidError(cached[1])

        try:
            cfg = self._load_raw(yaml_path)
        except BrandingInvalidError as e:
            logger.error("Invalid branding config: %s", e, exc_info=e)
            _failure_cache[path_key] = (cache_key, str(e))
            raise
        return cfg

    def _load_raw(self, yaml_path: Path) -> BrandingConfig:
        try:
            data = load_yaml_file(yaml_path) or {}
        except Exception as e:
            raise BrandingInvalidError(f"{yaml_path} could not be parsed: {e}") from e
        if not isinstance(data, dict):
            raise BrandingInvalidError(
                f"{yaml_path} must be a YAML mapping, got {type(data).__name__}"
            )

        meta = data.get("meta") or {}
        mcp = data.get("mcp") or {}
        # The top-level check above only covers the outermost document --
        # `meta: [x]` or `mcp: [x]` still parses as valid YAML with `data` a
        # dict, so `.get()` on a nested key returns a list, and the reads
        # below (`meta.get(...)`, `mcp.get(...)`) would raise a raw
        # AttributeError instead of the named error (#445 cold read).
        if not isinstance(meta, dict):
            raise BrandingInvalidError(
                f"{yaml_path}: 'meta' must be a YAML mapping, got {type(meta).__name__}"
            )
        if not isinstance(mcp, dict):
            raise BrandingInvalidError(
                f"{yaml_path}: 'mcp' must be a YAML mapping, got {type(mcp).__name__}"
            )

        # Every scalar field BrandingConfig exposes must be a string (or
        # absent) -- a list or number reads fine as far as .get() is
        # concerned, but silently breaks the field's contract: name: [x]
        # serializes through to_public_dict() as a JSON array where every
        # consumer (the web branding store, the <title> tag, the MCP prompt)
        # expects text, and site_url: [x] reaches the web app's canonical
        # URL the same way (#445 delta cold read).
        string_fields = {
            "name": data.get("name"),
            "tagline": data.get("tagline"),
            "primary_color": data.get("primary_color"),
            "site_url": data.get("site_url"),
            "support_url": data.get("support_url"),
            "footer_credit_url": data.get("footer_credit_url"),
            "logo": data.get("logo"),
            "wordmark": data.get("wordmark"),
            "favicon": data.get("favicon"),
            "apple_touch_icon": data.get("apple_touch_icon"),
            "logo_light": data.get("logo_light"),
            "logo_dark": data.get("logo_dark"),
            "wordmark_light": data.get("wordmark_light"),
            "wordmark_dark": data.get("wordmark_dark"),
            "meta.og_image_path": meta.get("og_image_path"),
            "meta.description": meta.get("description"),
            "mcp.agent_prompt_brand": mcp.get("agent_prompt_brand"),
        }
        for field_name, value in string_fields.items():
            if value is not None and not isinstance(value, str):
                raise BrandingInvalidError(
                    f"{yaml_path}: '{field_name}' must be a string, got {type(value).__name__}"
                )

        name = data.get("name", DEFAULT_BRAND_NAME)
        mcp_brand = mcp.get("agent_prompt_brand") or name

        return BrandingConfig(
            name=name,
            tagline=data.get("tagline", ""),
            primary_color=data.get("primary_color", DEFAULT_PRIMARY_COLOR),
            site_url=data.get("site_url", ""),
            support_url=data.get("support_url", ""),
            footer_credit_url=data.get("footer_credit_url", DEFAULT_FOOTER_CREDIT_URL),
            logo=data.get("logo"),
            wordmark=data.get("wordmark"),
            favicon=data.get("favicon"),
            apple_touch_icon=data.get("apple_touch_icon"),
            og_image=meta.get("og_image_path"),
            logo_light=data.get("logo_light"),
            logo_dark=data.get("logo_dark"),
            wordmark_light=data.get("wordmark_light"),
            wordmark_dark=data.get("wordmark_dark"),
            invert_on_dark=bool(data.get("invert_on_dark", False)),
            meta_description=meta.get("description", ""),
            mcp_agent_prompt_brand=mcp_brand,
            branding_dir=self._branding_dir,
        )
