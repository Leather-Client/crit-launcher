"""JS-bridge API: add-on search and install."""

import json
import re
import time
import urllib.parse
from pathlib import Path

from ..core.fsutil import is_within
from ..core.http import http_download, http_get_json
from ..core.logs import log_exception
from ..instances.store import (
    instance_game_dir,
    instance_mods_dir,
    is_installed,
    list_instances,
    read_instance_meta,
)
from ..minecraft.loaders import LOADER_NICE, MOD_LOADERS
from .constants import ADDON_TYPES
from .curseforge import (
    CF_CLASS_IDS,
    CurseForgeError,
    _cf_dependencies,
    _cf_get,
    _cf_number,
    _cf_pick_file,
    _cf_search,
    curseforge_install_to,
)
from .modrinth import MODRINTH_API, pick_best_version, primary_file


_addon_cats_mem = {"at": 0.0, "value": None}


class AddonsApi:
    """Searching and installing mods, resource packs and shaders."""

    def _search_curseforge(self, query, project_type, index, offset, limit, filters):
        if project_type not in CF_CLASS_IDS:
            return {"success": False, "error": "CurseForge has no data packs. Switch the source to Modrinth."}
        try:
            found = _cf_search(query, project_type, index, offset, limit, filters)
            return {"success": True, "hits": found["hits"], "source": "curseforge",
                    "total": int(found.get("total") or 0)}
        except CurseForgeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("search_curseforge")
            return {"success": False, "error": "Couldn't search CurseForge."}

    def search_addons(self, query="", project_type="mod", index=None, offset=0, limit=30, filters=None,
                      source="modrinth"):
        if source == "curseforge":
            try:
                if project_type not in ADDON_TYPES:
                    project_type = "mod"
                if index not in ("relevance", "downloads", "follows", "newest", "updated"):
                    index = "relevance" if query else "downloads"
                off = max(0, int(offset or 0))
                lim = max(1, min(int(limit or 30), 100))
            except (TypeError, ValueError):
                off, lim = 0, 30
            return self._search_curseforge(query, project_type, index, off, lim, filters)
        try:
            if project_type not in ADDON_TYPES:
                project_type = "mod"
            if index not in ("relevance", "downloads", "follows", "newest", "updated"):
                index = "relevance" if query else "downloads"
            try:
                offset = max(0, int(offset or 0))
                limit = max(1, min(int(limit or 30), 100))
            except (TypeError, ValueError):
                offset, limit = 0, 30
            facets = [[f"project_type:{project_type}"]]
            if isinstance(filters, dict):
                ok = re.compile(r"^[A-Za-z0-9_.+\-]{1,40}$")
                ver = filters.get("version")
                if isinstance(ver, str) and ok.match(ver):
                    facets.append([f"versions:{ver}"])
                loaders = [x for x in (filters.get("loaders") or []) if isinstance(x, str) and ok.match(x)]
                if loaders:
                    facets.append([f"categories:{x}" for x in loaders])
                for cat in (filters.get("categories") or [])[:12]:
                    if isinstance(cat, str) and ok.match(cat):
                        facets.append([f"categories:{cat}"])
            params = {
                "query": query or "",
                "limit": str(limit),
                "offset": str(offset),
                "facets": json.dumps(facets),
                "index": index,
            }
            url = f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params)
            data = http_get_json(url)
            return {"success": True, "hits": data.get("hits", []),
                    "total": int(data.get("total_hits", 0) or 0), "source": "modrinth"}
        except Exception:
            log_exception("search_addons")
            return {"success": False, "error": "Failed to search Modrinth."}

    def get_addon_categories(self):
        try:
            now = time.time()
            if _addon_cats_mem["value"] and now - _addon_cats_mem["at"] < 86400:
                return {"success": True, "categories": _addon_cats_mem["value"]}
            data = http_get_json(f"{MODRINTH_API}/tag/category")
            cats = [{"name": c.get("name"), "project_type": c.get("project_type"), "header": c.get("header")}
                    for c in data if isinstance(c, dict) and c.get("name")]
            _addon_cats_mem["at"], _addon_cats_mem["value"] = now, cats
            return {"success": True, "categories": cats}
        except Exception:
            log_exception("get_addon_categories")
            return {"success": False, "error": "Couldn't load categories."}

    def get_addon(self, project_id):
        try:
            data = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}")
            return {"success": True, "project": data}
        except Exception:
            log_exception("get_addon")
            return {"success": False, "error": "Failed to load addon info."}

    def get_addon_targets(self, project_type="mod"):
        try:
            result = []
            for inst in list_instances():
                loader = inst.get("loader", "vanilla")
                if project_type == "mod" and loader not in MOD_LOADERS:
                    continue
                if project_type == "shader" and loader != "fabric":
                    continue
                item = {
                    "name": inst["name"], "version": inst["version"],
                    "loader": loader, "loader_version": inst.get("loader_version"),
                    "installed": is_installed(inst),
                }
                if project_type == "shader":
                    item["iris"] = self._fabric_has_iris(inst["name"])
                result.append(item)
            return {"success": True, "instances": result}
        except Exception:
            log_exception("get_addon_targets")
            return {"success": False, "error": "Failed to list instances."}

    def _find_version(self, project_id, loader=None, mc_version=None):
        params = {}
        if loader:
            params["loaders"] = json.dumps([loader])
        if mc_version:
            params["game_versions"] = json.dumps([mc_version])
        url = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return pick_best_version(http_get_json(url))

    def _download_version_file(self, version, dest_dir: Path):
        file_info = primary_file(version)
        if not file_info:
            raise RuntimeError("That version has no downloadable file.")
        filename = Path(file_info["filename"]).name
        dest = dest_dir / filename
        if not is_within(dest_dir, dest):
            raise RuntimeError("Unsafe file name in addon.")
        http_download(file_info["url"], dest, file_info.get("size"),
                      (file_info.get("hashes") or {}).get("sha1"))
        return filename

    def _install_mod_with_deps(self, version, mods_dir, loader, mc_version, visited, files, depth=0):
        files.append(self._download_version_file(version, mods_dir))
        if depth >= 3:
            return
        for dep in version.get("dependencies", []):
            if dep.get("dependency_type") != "required":
                continue
            pid = dep.get("project_id")
            if not pid or pid in visited:
                continue
            visited.add(pid)
            try:
                if dep.get("version_id"):
                    dep_version = http_get_json(
                        f"{MODRINTH_API}/version/{urllib.parse.quote(dep['version_id'])}")
                else:
                    dep_version = self._find_version(pid, loader, mc_version)
                if dep_version:
                    self._install_mod_with_deps(dep_version, mods_dir, loader, mc_version,
                                                visited, files, depth + 1)
            except Exception:
                log_exception(f"dependency {pid} failed")

    def get_addon_dependencies(self, project_id, instance_name=None):
        if isinstance(project_id, str) and project_id.startswith("cf:"):
            return self._cf_addon_dependencies(project_id, instance_name)
        try:
            meta = read_instance_meta(instance_name) if instance_name else None
            mc_version = meta["version"] if meta else None
            loader = meta.get("loader") if meta else None
            version = self._find_version(project_id, loader, mc_version)
            if not version:
                return {"success": True, "dependencies": []}
            deps = []
            for dep in version.get("dependencies", []):
                if dep.get("dependency_type") != "required":
                    continue
                pid = dep.get("project_id")
                if not pid:
                    continue
                try:
                    proj = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(pid)}")
                    deps.append({"project_id": pid, "title": proj.get("title", pid),
                                "icon_url": proj.get("icon_url")})
                except Exception:
                    deps.append({"project_id": pid, "title": pid, "icon_url": None})
            return {"success": True, "dependencies": deps}
        except Exception:
            log_exception("get_addon_dependencies")
            return {"success": False, "error": "Couldn't load dependencies."}

    def _cf_addon_dependencies(self, project_id, instance_name=None):
        try:
            meta = read_instance_meta(instance_name) if instance_name else None
            mc_version = meta["version"] if meta else None
            loader = meta.get("loader") if meta else None
            mod_id = _cf_number(project_id)
            files = _cf_get(f"/mods/{mod_id}/files", {"gameVersion": mc_version}) or []
            if isinstance(files, dict):
                files = files.get("data") or []
            chosen = _cf_pick_file(files, mc_version, loader)
            deps = []
            for dep in _cf_dependencies(mod_id, chosen.get("id") if chosen else None):
                dep_id = int(dep["modId"])
                title, icon_url = f"CurseForge mod {dep_id}", None
                try:
                    proj = _cf_get(f"/mods/{dep_id}") or {}
                    if isinstance(proj, dict):
                        title = proj.get("name") or title
                        logo = proj.get("logo") if isinstance(proj.get("logo"), dict) else {}
                        icon_url = logo.get("thumbnailUrl") or logo.get("url")
                except Exception:
                    pass
                deps.append({"project_id": f"cf:{dep_id}", "title": title, "icon_url": icon_url})
            return {"success": True, "dependencies": deps}
        except CurseForgeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("get_addon_dependencies curseforge")
            return {"success": False, "error": "Couldn't load dependencies."}

    def _install_curseforge(self, project_id, project_type, instance_name, include_deps):
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            mc_version = meta["version"]
            loader = meta.get("loader", "vanilla")
            if project_type == "mod":
                if loader not in MOD_LOADERS:
                    return {"success": False,
                            "error": f"{LOADER_NICE.get(loader, 'Vanilla')} instances can't run mods."}
                dest_dir = instance_mods_dir(instance_name)
            elif project_type == "resourcepack":
                dest_dir = instance_game_dir(instance_name) / "resourcepacks"
            elif project_type == "shader":
                dest_dir = instance_game_dir(instance_name) / "shaderpacks"
            else:
                return {"success": False, "error": "CurseForge installs support mods, resource packs and shaders."}
            dest_dir.mkdir(parents=True, exist_ok=True)
            files = curseforge_install_to(_cf_number(project_id), dest_dir, mc_version,
                                          loader if project_type == "mod" else None,
                                          set(), 0, bool(include_deps))
            return {"success": True, "files": files}
        except CurseForgeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("install_curseforge")
            return {"success": False, "error": f"Failed to install: {exc}"}

    def install_addon(self, project_id, project_type, instance_name, include_deps=True):
        if isinstance(project_id, str) and project_id.startswith("cf:"):
            return self._install_curseforge(project_id, project_type, instance_name, include_deps)
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            mc_version = meta["version"]
            loader = meta.get("loader", "vanilla")

            if project_type == "mod":
                if loader not in MOD_LOADERS:
                    return {"success": False,
                            "error": f"{LOADER_NICE.get(loader, 'Vanilla')} instances can't run mods."}
                version = self._find_version(project_id, loader, mc_version)
                if not version:
                    return {"success": False,
                            "error": f"No version of this mod supports {LOADER_NICE.get(loader, loader)} {mc_version}."}
                mods_dir = instance_mods_dir(instance_name)
                mods_dir.mkdir(parents=True, exist_ok=True)
                files: list = []
                if include_deps:
                    self._install_mod_with_deps(version, mods_dir, loader, mc_version,
                                                {project_id}, files)
                else:
                    files.append(self._download_version_file(version, mods_dir))
                return {"success": True, "files": files}

            if project_type == "resourcepack":
                note = None
                version = self._find_version(project_id, None, mc_version)
                if not version:
                    version = self._find_version(project_id)
                    note = f"No exact match for {mc_version}, installed the newest version instead."
                if not version:
                    return {"success": False, "error": "That resource pack has no downloadable version."}
                dest_dir = instance_game_dir(instance_name) / "resourcepacks"
                dest_dir.mkdir(parents=True, exist_ok=True)
                name = self._download_version_file(version, dest_dir)
                return {"success": True, "files": [name], "note": note}

            if project_type == "shader":
                if loader != "fabric":
                    return {"success": False, "error": "Shaders install into Fabric instances (with Iris)."}
                return self._install_shader(project_id, instance_name, meta)

            return {"success": False, "error": "Unsupported addon type."}
        except Exception as exc:
            log_exception("install_addon")
            return {"success": False, "error": f"Failed to install: {exc}"}
