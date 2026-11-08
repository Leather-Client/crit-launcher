"""JS-bridge API: Horizon version info."""

from ..core.logs import log_exception
from ..minecraft.meta_cache import meta_cache
from .constants import HORIZON_MC_VERSION


class HorizonApi:
    """Horizon version information."""

    def get_horizon_mc_versions(self):
        """The Minecraft versions a new Horizon instance can use (a build supports exactly one)."""
        try:
            manifest = meta_cache.get_manifest()
            supported = [v["id"] for v in manifest.get("versions", [])
                         if v.get("type") == "release" and v.get("id") == HORIZON_MC_VERSION]
            return {"success": True, "versions": supported, "minecraft": HORIZON_MC_VERSION}
        except Exception:
            log_exception("get_horizon_mc_versions")
            return {"success": True, "versions": [HORIZON_MC_VERSION],
                    "minecraft": HORIZON_MC_VERSION}
