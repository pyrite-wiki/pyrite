"""Public branding endpoints — white-label config + static asset serving.

Mounted OUTSIDE /api (no auth required) so the login page can fetch
branding before the user is authenticated. The asset endpoint only
serves files from within the configured branding dir; any path
traversal attempt resolves to 404.
"""

from __future__ import annotations

import logging
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import PyriteConfig
from ..services.branding_service import BrandingService
from .api import get_config
from .static import add_csp_extra

logger = logging.getLogger(__name__)

# Operator-supplied files differ from the baseline policy only by `sandbox`:
# opened directly, they have no script, form or plugin capability. The
# headers themselves come from `static.security_headers`, applied to every
# non-API response.
BRANDING_ASSET_CSP_EXTRA = "sandbox"

branding_router = APIRouter(tags=["Branding"])

# Cached per-config service. Rebuilt on config change (detected via identity).
_service_cache: tuple[int, BrandingService] | None = None


def get_branding_service(
    config: PyriteConfig = Depends(get_config),
) -> BrandingService:
    global _service_cache
    if _service_cache is None or _service_cache[0] != id(config):
        _service_cache = (id(config), BrandingService(config.settings.branding_dir))
    return _service_cache[1]


@branding_router.get("/config/branding")
def get_branding_config(
    svc: BrandingService = Depends(get_branding_service),
) -> dict:
    """Public: returns branding configuration for the frontend."""
    return svc.get().to_public_dict()


@branding_router.get("/branding/{filename}")
def get_branding_asset(
    request: Request,
    filename: str,
    svc: BrandingService = Depends(get_branding_service),
) -> FileResponse:
    """Public: serves an asset from the branding folder.

    Returns 404 if no branding dir is configured, the file doesn't
    exist, or the resolved path escapes the branding dir.
    """
    path = svc.resolve_asset(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    add_csp_extra(request, BRANDING_ASSET_CSP_EXTRA)
    mime, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=mime or "application/octet-stream")
