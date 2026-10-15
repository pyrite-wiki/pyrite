"""Tests for BrandingService — white-label branding loader.

Covers: load from folder, fallback to defaults, missing-file handling,
theme-variant resolution, asset URL mapping, ETag generation.
"""

import textwrap
from pathlib import Path

import pytest

from pyrite.services.branding_service import (
    DEFAULT_BRAND_NAME,
    DEFAULT_PRIMARY_COLOR,
    BrandingConfig,
    BrandingService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip())


@pytest.fixture
def branding_dir(tmp_path: Path) -> Path:
    """A fully-populated branding folder for Transparency Cascade."""
    d = tmp_path / "branding"
    _write(
        d / "branding.yaml",
        """
        name: "Transparency Cascade Press"
        tagline: "Tracing the money, naming the actors, connecting the patterns"
        primary_color: "#c93b3b"
        site_url: "https://investigate.transparencycascade.org"
        support_url: "mailto:contact@transparencycascade.org"
        footer_credit_url: "https://pyrite.wiki"

        logo: "logo.png"
        wordmark: "wordmark.png"
        invert_on_dark: true

        meta:
          description: "Independent investigative journalism."

        mcp:
          agent_prompt_brand: "Transparency Cascade"
        """,
    )
    (d / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (d / "wordmark.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return d


@pytest.fixture
def minimal_branding_dir(tmp_path: Path) -> Path:
    """Only name set — everything else should fall through to defaults."""
    d = tmp_path / "minimal"
    _write(d / "branding.yaml", 'name: "Minimal Brand"\n')
    return d


# ---------------------------------------------------------------------------
# Loader — full config
# ---------------------------------------------------------------------------


class TestBrandingServiceLoadsFromFolder:
    def test_loads_name_and_primary_color(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.name == "Transparency Cascade Press"
        assert cfg.primary_color == "#c93b3b"

    def test_loads_tagline_and_urls(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.tagline == "Tracing the money, naming the actors, connecting the patterns"
        assert cfg.site_url == "https://investigate.transparencycascade.org"
        assert cfg.support_url == "mailto:contact@transparencycascade.org"
        assert cfg.footer_credit_url == "https://pyrite.wiki"

    def test_loads_logo_and_wordmark_filenames(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.logo == "logo.png"
        assert cfg.wordmark == "wordmark.png"
        assert cfg.invert_on_dark is True

    def test_loads_meta_description(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.meta_description == "Independent investigative journalism."

    def test_loads_mcp_agent_prompt_brand(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.mcp_agent_prompt_brand == "Transparency Cascade"


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestBrandingServiceFallsBack:
    def test_no_branding_dir_uses_defaults(self):
        cfg = BrandingService(None).get()
        assert cfg.name == DEFAULT_BRAND_NAME
        assert cfg.primary_color == DEFAULT_PRIMARY_COLOR
        assert cfg.logo is None
        assert cfg.wordmark is None

    def test_missing_branding_dir_uses_defaults(self, tmp_path: Path):
        missing = tmp_path / "does-not-exist"
        cfg = BrandingService(missing).get()
        assert cfg.name == DEFAULT_BRAND_NAME

    def test_empty_branding_dir_uses_defaults(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        cfg = BrandingService(empty).get()
        assert cfg.name == DEFAULT_BRAND_NAME

    def test_missing_fields_fall_through_to_defaults(self, minimal_branding_dir: Path):
        cfg = BrandingService(minimal_branding_dir).get()
        assert cfg.name == "Minimal Brand"
        assert cfg.primary_color == DEFAULT_PRIMARY_COLOR  # not in yaml → default
        assert cfg.logo is None
        assert cfg.invert_on_dark is False  # default

    def test_mcp_brand_defaults_to_name_when_unset(self, minimal_branding_dir: Path):
        cfg = BrandingService(minimal_branding_dir).get()
        # mcp.agent_prompt_brand falls back to the top-level name
        assert cfg.mcp_agent_prompt_brand == "Minimal Brand"


# ---------------------------------------------------------------------------
# Asset URL mapping
# ---------------------------------------------------------------------------


class TestAssetUrls:
    def test_logo_url_is_none_when_no_file(self, minimal_branding_dir: Path):
        cfg = BrandingService(minimal_branding_dir).get()
        assert cfg.logo_url() is None

    def test_logo_url_when_present(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.logo_url() == "/branding/logo.png"

    def test_wordmark_url_when_present(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        assert cfg.wordmark_url() == "/branding/wordmark.png"

    def test_resolves_asset_path_inside_branding_dir(self, branding_dir: Path):
        svc = BrandingService(branding_dir)
        path = svc.resolve_asset("logo.png")
        assert path == branding_dir / "logo.png"
        assert path.exists()

    def test_resolve_asset_rejects_traversal(self, branding_dir: Path):
        """Asset resolution must not escape the branding dir."""
        svc = BrandingService(branding_dir)
        assert svc.resolve_asset("../../../etc/passwd") is None
        assert svc.resolve_asset("/etc/passwd") is None

    def test_resolve_asset_returns_none_for_missing_file(self, branding_dir: Path):
        svc = BrandingService(branding_dir).resolve_asset("nope.png")
        assert svc is None


# ---------------------------------------------------------------------------
# Theme variants
# ---------------------------------------------------------------------------


class TestThemeVariants:
    def test_light_dark_variants_load(self, tmp_path: Path):
        d = tmp_path / "variant"
        _write(
            d / "branding.yaml",
            """
            name: "Variant Brand"
            logo_light: "logo-black.png"
            logo_dark: "logo-white.png"
            wordmark_light: "wordmark-black.png"
            wordmark_dark: "wordmark-white.png"
            invert_on_dark: false
            """,
        )
        for f in ("logo-black.png", "logo-white.png", "wordmark-black.png", "wordmark-white.png"):
            (d / f).write_bytes(b"fake")
        cfg = BrandingService(d).get()
        assert cfg.logo_light == "logo-black.png"
        assert cfg.logo_dark == "logo-white.png"
        assert cfg.wordmark_light == "wordmark-black.png"
        assert cfg.wordmark_dark == "wordmark-white.png"


# ---------------------------------------------------------------------------
# API serialization
# ---------------------------------------------------------------------------


class TestToPublicDict:
    def test_public_dict_shape(self, branding_dir: Path):
        cfg = BrandingService(branding_dir).get()
        public = cfg.to_public_dict()
        # Required fields
        assert public["name"] == "Transparency Cascade Press"
        assert public["primary_color"] == "#c93b3b"
        assert public["logo_url"] == "/branding/logo.png"
        assert public["wordmark_url"] == "/branding/wordmark.png"
        assert public["invert_on_dark"] is True
        assert public["footer_credit_url"] == "https://pyrite.wiki"

    def test_public_dict_omits_secrets(self, branding_dir: Path):
        """Nothing sensitive is in branding, but verify no extras leak."""
        cfg = BrandingService(branding_dir).get()
        public = cfg.to_public_dict()
        assert "branding_dir" not in public
        assert "_path" not in public

    def test_defaults_dict_has_name_pyrite(self):
        cfg = BrandingService(None).get()
        public = cfg.to_public_dict()
        assert public["name"] == DEFAULT_BRAND_NAME
        assert public["logo_url"] is None


# ---------------------------------------------------------------------------
# Validation and the failure cache (#408, #445). These classes lived in the
# /site tests until #953 removed the renderer; the rules they pin are
# BrandingService's and still serve /config/branding, the MCP
# `research_topic` prompt and the KB README footer.
# ---------------------------------------------------------------------------


class TestBrandingNestedMappingValidation:
    """#445 cold read: ``meta:`` and ``mcp:`` are read with ``.get()``
    immediately after the top-level mapping check, so ``meta: [x]`` or
    ``mcp: [x]`` (a YAML list where a mapping is expected) still raised a
    raw ``AttributeError`` -- the top-level ``isinstance(data, dict)`` guard
    only covers the outermost document.
    """

    @pytest.mark.parametrize("bad_key", ["meta", "mcp"])
    def test_nested_non_mapping_is_a_branding_invalid_error_not_an_attributeerror(
        self, tmp_path, bad_key
    ):
        from pyrite.exceptions import BrandingInvalidError

        branding = tmp_path / "branding"
        branding.mkdir()
        (branding / "branding.yaml").write_text(f"name: Acme\n{bad_key}: [x]\n")

        svc = BrandingService(branding)
        with pytest.raises(BrandingInvalidError):
            svc.get()


class TestBrandingScalarFieldValidation:
    """#445 delta cold read: BrandingService validated the top-level
    document and the two nested mappings (round 2), but not that the
    scalar fields it reads (``name``, ``site_url``, etc.) are actually
    strings. ``name: [x]`` came out of /config/branding as a JSON array
    where every consumer expects text (the endpoint half of this is in
    tests/test_branding_endpoints.py).
    """

    @pytest.mark.parametrize(
        "yaml_text",
        [
            "name: [x]\n",
            "site_url: [x]\n",
            "tagline: 123\n",
            "primary_color: true\n",
            "meta:\n  description: [x]\n",
            "mcp:\n  agent_prompt_brand: [x]\n",
        ],
        ids=[
            "name-list",
            "site_url-list",
            "tagline-number",
            "primary_color-bool",
            "meta.description-list",
            "mcp.agent_prompt_brand-list",
        ],
    )
    def test_non_string_scalar_field_is_branding_invalid_error(self, tmp_path, yaml_text):
        from pyrite.exceptions import BrandingInvalidError

        branding = tmp_path / "branding"
        branding.mkdir()
        (branding / "branding.yaml").write_text(yaml_text)

        svc = BrandingService(branding)
        with pytest.raises(BrandingInvalidError):
            svc.get()


class TestBrandingFailureCacheDoesNotLeakTracebackFrames:
    """#445 round 3: a cache hit re-raised the SAME stored exception object
    (``raise cached[1]``). Python appends a frame to ``__traceback__`` on
    every raise of that object, so the cached exception's traceback grows
    without bound across requests, and the central handler's
    ``logger.error(..., exc_info=exc)`` prints the whole (growing)
    traceback on every single ``/config/branding`` request.
    """

    def test_traceback_length_does_not_grow_across_repeated_loads(self, tmp_path):
        import traceback

        from pyrite.exceptions import BrandingInvalidError

        branding = tmp_path / "branding"
        branding.mkdir()
        (branding / "branding.yaml").write_text("- a list, not a mapping\n")

        svc = BrandingService(branding)
        lengths = []
        for _ in range(8):
            svc._config = None  # force _load() again, same object, same mtime
            with pytest.raises(BrandingInvalidError) as excinfo:
                svc.get()
            lengths.append(len(traceback.extract_tb(excinfo.value.__traceback__)))

        # The first call is a real parse failure (a deeper stack, through
        # _load_raw); every call after that is a cache hit and must raise a
        # FRESH exception each time, so its traceback is a fixed, shallow
        # depth -- not growing call over call. Comparing only the cache-hit
        # calls (index 1 on) isolates the bug: re-raising the same stored
        # exception object appends a frame on every raise, so those lengths
        # would climb 1, 2, 3, ... instead of staying constant.
        cache_hit_lengths = lengths[1:]
        assert len(set(cache_hit_lengths)) == 1, (
            f"traceback frame count grew across repeated cache-hit loads of "
            f"the same unchanged file (first call, a real parse, is index 0 "
            f"and expected to differ): {lengths}"
        )


class TestBrandingFailureCacheKey:
    """#445 round 3: keyed on mtime alone, a fixed file whose mtime happens
    not to change (``cp -p``, ``rsync -t``, tar extraction, or a filesystem
    with 1-second mtime granularity racing two writes in the same second)
    keeps failing forever with the stale cached error. Keying on
    ``(st_mtime_ns, st_size)`` catches a same-second edit as long as the
    byte size differs, which covers the realistic "operator fixed a typo"
    case without needing a content hash.
    """

    def test_same_mtime_different_size_is_not_treated_as_the_same_failure(self, tmp_path):
        import os

        from pyrite.exceptions import BrandingInvalidError

        branding = tmp_path / "branding"
        branding.mkdir()
        yaml_path = branding / "branding.yaml"
        yaml_path.write_text("- a list, not a mapping\n")

        svc = BrandingService(branding)
        with pytest.raises(BrandingInvalidError):
            svc.get()

        # Fix the file's content but pin its mtime to the same value a
        # coarse-grained filesystem or a `cp -p`/`rsync -t` copy would
        # produce -- the size changes, the mtime does not.
        st = yaml_path.stat()
        yaml_path.write_text("name: Acme\n")
        os.utime(yaml_path, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert yaml_path.stat().st_mtime_ns == st.st_mtime_ns, "test setup: mtime must be pinned"

        svc2 = BrandingService(branding)
        cfg = svc2.get()  # must NOT raise the stale cached failure
        assert cfg.name == "Acme", cfg

    def test_file_deleted_between_is_file_and_stat_is_treated_as_missing(
        self, tmp_path, monkeypatch
    ):
        """A TOCTOU gap: ``is_file()`` passes, then the file is removed
        before the later, unguarded ``yaml_path.stat()`` call runs (the one
        that reads the cache key). Must fall back to defaults (the same
        outcome as the file never having existed), not raise
        FileNotFoundError.

        ``Path.is_file()`` itself calls ``self.stat()`` internally (and
        already handles ENOENT, returning False), so a naive "delete on the
        first stat() call" monkeypatch deletes the file during is_file()'s
        own check and never reaches the real gap this test targets. The
        delete has to happen on the *second* stat() call -- the one after
        is_file() has already returned True.
        """
        from pyrite.services.branding_service import DEFAULT_BRAND_NAME

        branding = tmp_path / "branding"
        branding.mkdir()
        yaml_path = branding / "branding.yaml"
        yaml_path.write_text("name: Acme\n")

        real_stat = Path.stat
        calls = {"n": 0}

        def _stat_then_delete_on_second_call(self, *args, **kwargs):
            if self == yaml_path:
                calls["n"] += 1
                if calls["n"] == 2:
                    yaml_path.unlink()
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _stat_then_delete_on_second_call)

        svc = BrandingService(branding)
        cfg = svc.get()
        assert cfg.name == DEFAULT_BRAND_NAME, cfg
