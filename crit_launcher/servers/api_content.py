"""JS-bridge API: server plugins and mods."""

import json
import urllib.parse
from pathlib import Path

from ..addons.modrinth import MODRINTH_API, pick_best_version
from ..core.fsutil import is_within
from ..core.http import http_get_json
from ..core.logs import log_exception
from ..instances.content import read_mod_jar
from .constants import PLUGIN_LOADERS
from .meta import read_server_meta, server_dir
from .plugins import read_plugin_jar


class ServerContentApi:
    """Server plugins and mods."""

    def _srv_content_dir(self, meta):
        if meta.get("software") == "fabric":
            return server_dir(meta["name"]) / "mods"
        if meta.get("software") == "paper":
            return server_dir(meta["name"]) / "plugins"
        return None

    def get_server_content(self, name):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            meta["name"] = name
            folder = self._srv_content_dir(meta)
            if folder is None:
                return {"success": True, "items": [], "kind": None, "software": "vanilla"}
            folder.mkdir(exist_ok=True)
            reader = read_mod_jar if meta["software"] == "fabric" else read_plugin_jar
            items = []
            for p in folder.iterdir():
                low = p.name.lower()
                if low.endswith(".jar.disabled"):
                    enabled, stem = False, p.name[:-13]
                elif low.endswith(".jar") and p.is_file():
                    enabled, stem = True, p.name[:-4]
                else:
                    continue
                info = reader(p)
                items.append({"file": p.name, "name": info["name"] or stem, "version": info["version"],
                              "description": info["description"], "authors": info["authors"], "enabled": enabled})
            items.sort(key=lambda i: i["name"].lower())
            return {"success": True, "items": items, "software": meta["software"],
                    "kind": "mods" if meta["software"] == "fabric" else "plugins"}
        except Exception:
            log_exception("get_server_content")
            return {"success": False, "error": "Couldn't read the mods folder."}

    def _srv_content_file(self, name, filename):
        meta = read_server_meta(name)
        if not meta:
            raise RuntimeError("Server not found.")
        meta["name"] = name
        folder = self._srv_content_dir(meta)
        path = folder / Path(filename).name
        if not path.exists() or not is_within(folder, path):
            raise RuntimeError("File not found.")
        return path

    def toggle_server_content(self, name, filename, enabled):
        try:
            if self._srv_status(name) not in ("offline",):
                return {"success": False, "error": "Stop the server before enabling or disabling mods."}
            p = self._srv_content_file(name, filename)
            if enabled and p.name.lower().endswith(".jar.disabled"):
                p.rename(p.with_name(p.name[:-9]))
            elif not enabled and p.name.lower().endswith(".jar"):
                p.rename(p.with_name(p.name + ".disabled"))
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Couldn't change that file."}

    def delete_server_content(self, name, filename):
        try:
            if self._srv_status(name) not in ("offline",):
                return {"success": False, "error": "Stop the server before removing mods."}
            self._srv_content_file(name, filename).unlink()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Couldn't delete that file."}

    def search_server_mods(self, name, query="", offset=0):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            sw = meta["software"]
            if sw == "vanilla":
                return {"success": True, "hits": [], "total": 0, "unsupported": True}
            facets = [["project_type:mod"], [f"versions:{meta['version']}"]]
            if sw == "fabric":
                facets += [["categories:fabric"], ["server_side:required", "server_side:optional"]]
            else:
                facets += [[f"categories:{c}" for c in PLUGIN_LOADERS]]
            params = {"query": query or "", "limit": "24", "offset": str(max(0, int(offset or 0))),
                      "facets": json.dumps(facets), "index": "relevance" if query else "downloads"}
            data = http_get_json(f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params))
            return {"success": True, "hits": data.get("hits", []), "total": data.get("total_hits", 0)}
        except Exception:
            log_exception("search_server_mods")
            return {"success": False, "error": "Failed to search Modrinth."}

    def _srv_find_version(self, project_id, loaders, mc):
        params = {"loaders": json.dumps(loaders), "game_versions": json.dumps([mc])}
        url = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version?" + urllib.parse.urlencode(params)
        return pick_best_version(http_get_json(url))

    def _srv_install_deps(self, version, dest, loaders, mc, visited, files, depth=0):
        files.append(self._download_version_file(version, dest))
        if depth >= 3:
            return
        for dep in version.get("dependencies", []):
            pid = dep.get("project_id")
            if dep.get("dependency_type") != "required" or not pid or pid in visited:
                continue
            visited.add(pid)
            try:
                dv = (http_get_json(f"{MODRINTH_API}/version/{urllib.parse.quote(dep['version_id'])}")
                      if dep.get("version_id") else self._srv_find_version(pid, loaders, mc))
                if dv:
                    self._srv_install_deps(dv, dest, loaders, mc, visited, files, depth + 1)
            except Exception:
                log_exception(f"server dependency {pid} failed")

    def install_server_mod(self, name, project_id):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            meta["name"] = name
            folder = self._srv_content_dir(meta)
            if folder is None:
                return {"success": False, "error": "Vanilla servers can't run mods. Make a Fabric or Paper server."}
            loaders = ["fabric"] if meta["software"] == "fabric" else PLUGIN_LOADERS
            version = self._srv_find_version(project_id, loaders, meta["version"])
            if not version:
                return {"success": False,
                        "error": f"No version of this supports {meta['software'].capitalize()} {meta['version']}."}
            folder.mkdir(exist_ok=True)
            files: list = []
            self._srv_install_deps(version, folder, loaders, meta["version"], {project_id}, files)
            return {"success": True, "files": files}
        except Exception as exc:
            log_exception("install_server_mod")
            return {"success": False, "error": f"Failed to install: {exc}"}
