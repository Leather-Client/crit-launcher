"""JS-bridge API: version lists."""

from ..core.logs import log_exception
from .meta_cache import meta_cache


class VersionsApi:
    """Minecraft / Fabric / Forge / NeoForge version lists."""

    def get_versions(self, include_snapshots=False):
        try:
            manifest = meta_cache.get_manifest()
            allowed = ("release", "snapshot", "old_beta", "old_alpha")
            versions = [v for v in manifest["versions"] if v.get("type") in allowed]
            if not include_snapshots:
                versions = [v for v in versions if v.get("type") == "release"]
            latest = manifest.get("latest", {})
            return {"success": True, "versions": versions,
                    "latest_release": latest.get("release"),
                    "latest_snapshot": latest.get("snapshot"),
                    "snapshots": any(v.get("type") == "snapshot" for v in versions)}
        except Exception:
            log_exception("get_versions")
            return {"success": False, "error": "Failed to load versions"}

    def get_fabric_loaders(self, minecraft_version=None):
        try:
            loaders = meta_cache.get_fabric_loaders(minecraft_version)
            return {"success": True, "loaders": loaders}
        except Exception as exc:
            log_exception("get_fabric_loaders")
            return {"success": False, "error": str(exc) or "Fabric loader could not be retrieved."}

    def get_fabric_game_versions(self):
        try:
            return {"success": True, "versions": meta_cache.get_fabric_game_versions()}
        except Exception as exc:
            log_exception("get_fabric_game_versions")
            return {"success": False, "error": str(exc) or "Fabric versions could not be retrieved."}

    def get_forge_versions(self, minecraft_version):
        try:
            versions = meta_cache.get_forge_versions(minecraft_version)
            return {"success": True, "versions": versions}
        except Exception as exc:
            log_exception("get_forge_versions")
            return {"success": False, "error": str(exc) or "Forge versions could not be retrieved."}

    def get_neoforge_versions(self, minecraft_version):
        try:
            versions = meta_cache.get_neoforge_versions(minecraft_version)
            return {"success": True, "versions": versions}
        except Exception as exc:
            log_exception("get_neoforge_versions")
            return {"success": False, "error": str(exc) or "NeoForge versions could not be retrieved."}

    def get_neoforge_mc_versions(self):
        try:
            return {"success": True, "versions": meta_cache.get_neoforge_mc_versions()}
        except Exception as exc:
            log_exception("get_neoforge_mc_versions")
            return {"success": False, "error": str(exc) or "NeoForge versions could not be retrieved."}

    def get_forge_mc_versions(self):
        try:
            return {"success": True, "versions": meta_cache.get_forge_mc_versions()}
        except Exception as exc:
            log_exception("get_forge_mc_versions")
            return {"success": False, "error": str(exc) or "Forge versions could not be retrieved."}
