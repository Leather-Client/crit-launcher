"""JS-bridge API: Iris and shaders."""

import json
import re
import urllib.parse

from ..core.http import http_get_json
from ..core.logs import log_exception
from ..instances.content import read_mod_jar
from ..instances.store import instance_game_dir, instance_mods_dir, read_instance_meta
from .modrinth import MODRINTH_API, pick_best_version


class ShadersApi:
    """Iris and shader-pack installation."""

    def _fabric_has_iris(self, name):
        mods = instance_mods_dir(name)
        if not mods.is_dir():
            return False
        for p in mods.iterdir():
            low = p.name.lower()
            if not low.endswith(".jar"):
                continue
            if re.match(r"^iris[-_+ ]", low) and "flywheel" not in low:
                return True
            n = (read_mod_jar(p).get("name") or "").lower()
            if n in ("iris", "iris shaders"):
                return True
        return False

    def install_iris(self, instance_name):
        try:
            meta = read_instance_meta(instance_name)
            if not meta or meta.get("loader") != "fabric":
                return {"success": False, "error": "Iris needs a Fabric instance."}
            version = self._find_version("iris", "fabric", meta["version"])
            if not version:
                return {"success": False, "error": f"Iris doesn't support Minecraft {meta['version']} yet."}
            mods_dir = instance_mods_dir(instance_name)
            mods_dir.mkdir(parents=True, exist_ok=True)
            files: list = []
            self._install_mod_with_deps(version, mods_dir, "fabric", meta["version"], {"YL57xq9U"}, files)
            return {"success": True, "files": files}
        except Exception as exc:
            log_exception("install_iris")
            return {"success": False, "error": f"Failed to install Iris: {exc}"}

    def _install_shader(self, project_id, instance_name, meta):
        mc = meta["version"]
        note = None
        base = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version"
        versions = http_get_json(base + "?" + urllib.parse.urlencode({"game_versions": json.dumps([mc])}))
        if not versions:
            versions = http_get_json(base)
            note = f"No exact match for {mc}, installed the newest version instead."
        iris_first = [v for v in versions if "iris" in (v.get("loaders") or [])] or versions
        version = pick_best_version(iris_first)
        if not version:
            raise RuntimeError("That shader pack has no downloadable version.")
        dest = instance_game_dir(instance_name) / "shaderpacks"
        dest.mkdir(parents=True, exist_ok=True)
        return {"success": True, "files": [self._download_version_file(version, dest)], "note": note}
