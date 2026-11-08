"""JS-bridge API: artwork."""

from ..core.logs import log_exception
from .mc_textures import ensure_mc_panorama, ensure_mc_textures
from .page import ensure_home_assets


class UiAssetsApi:
    """Fetching the page's artwork."""

    def ensure_assets(self):
        """index.html calls this when assets/<bg>.png isn't readable yet (first run, packaged exe)."""
        try:
            return ensure_home_assets()
        except Exception:
            log_exception("ensure_assets")
            return {"success": False, "files": {}}

    def ensure_mc_textures(self):
        try:
            return ensure_mc_textures()
        except Exception:
            log_exception("ensure_mc_textures")
            return {"success": False, "files": {}}

    def ensure_mc_panorama(self):
        try:
            return ensure_mc_panorama()
        except Exception:
            log_exception("ensure_mc_panorama")
            return {"success": False}
