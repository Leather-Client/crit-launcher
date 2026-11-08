"""JS-bridge API: server create/install/delete."""

import hashlib
import re
import shutil
import threading
import time
import urllib.parse

from ..config.paths import SERVERS_DIR
from ..core.fsutil import is_within
from ..core.http import http_download, http_get_json
from ..core.logs import log_exception
from ..core.progress import ProgressTracker
from ..core.system import open_in_file_manager
from ..instances.store import sanitize_instance_name
from ..minecraft.constants import DEFAULT_JAVA_INFO
from ..minecraft.java import resolve_java
from ..minecraft.meta_cache import meta_cache
from .constants import (
    EULA_URL,
    FABRIC_INSTALLER_LIST_URL,
    GAMERULES,
    PAPER_USER_AGENT,
    SERVER_SOFTWARE,
    _GAMERULE_IDS,
)
from .meta import read_server_meta, server_dir, write_server_meta
from .paper import fetch_paper_versions, paper_latest_build
from .properties import _clamp_int, build_server_properties, read_properties, write_properties


class ServerLifecycleApi:
    """Creating, installing and deleting local servers."""

    def _srv_init(self):
        self._srv_rt: dict = {}
        self._srv_installing: set = set()
        self._srv_lock = threading.RLock()

    def _srv_status(self, name):
        with self._srv_lock:
            if name in self._srv_installing:
                return "installing"
            rt = self._srv_rt.get(name)
        return rt.status if rt and rt.status != "offline" else "offline"

    def _srv_summary(self, meta) -> dict:
        name = meta["name"]
        with self._srv_lock:
            rt = self._srv_rt.get(name)
        live = rt is not None and rt.status != "offline"
        props = read_properties(server_dir(name) / "server.properties")
        return {
            "name": name, "software": meta.get("software"), "version": meta.get("version"),
            "loader_version": meta.get("loader_version"), "installed": bool(meta.get("installed")),
            "port": _clamp_int(props.get("server-port"), 1, 65535, meta.get("port", 25565)),
            "max_players": _clamp_int(props.get("max-players"), 1, 1000, 20),
            "memory_mb": meta.get("memory_mb", 2048), "status": self._srv_status(name),
            "players": len(rt.players) if live else 0,
            "uptime": int(time.time() - rt.started) if live and rt.status == "online" else 0,
            "created": meta.get("created"),
            "motd": str(props.get("motd", "A Crit Launcher server"))[:120],
        }

    def _srv_payload(self) -> list:
        items = []
        if SERVERS_DIR.is_dir():
            for d in SERVERS_DIR.iterdir():
                meta = read_server_meta(d.name) if d.is_dir() else None
                if meta:
                    meta["name"] = d.name
                    items.append(self._srv_summary(meta))
        items.sort(key=lambda s: (s.get("created") or 0))
        return items

    def _srv_push(self):
        self.emit("serversChanged", {"servers": self._srv_payload()})

    def get_servers(self):
        try:
            return {"success": True, "servers": self._srv_payload(),
                    "gamerules": [{"id": g[0], "label": g[1], "type": g[2], "default": g[3]} for g in GAMERULES]}
        except Exception:
            log_exception("get_servers")
            return {"success": False, "error": "Couldn't read your servers."}

    def get_server_versions(self, software):
        try:
            releases = [v["id"] for v in meta_cache.get_manifest()["versions"] if v["type"] == "release"]
            if software == "paper":
                versions = fetch_paper_versions()
            elif software == "fabric":
                allowed = set(meta_cache.get_fabric_game_versions())
                versions = [v for v in releases if v in allowed]
            else:
                versions = releases
            return {"success": True, "versions": versions}
        except Exception:
            log_exception("get_server_versions")
            return {"success": False, "error": "Couldn't load versions for that server type."}

    def create_server(self, config):
        try:
            cfg = dict(config or {})
            try:
                name = sanitize_instance_name(cfg.get("name"))
            except ValueError as exc:
                return {"success": False, "error": str(exc).replace("Instance", "Server")}
            software = cfg.get("software")
            if software not in SERVER_SOFTWARE:
                return {"success": False, "error": "Pick Vanilla, Paper or Fabric."}
            version = cfg.get("version")
            if not version:
                return {"success": False, "error": "Select a Minecraft version."}
            if software == "fabric" and not cfg.get("loader_version"):
                return {"success": False, "error": "Select a Fabric loader version."}
            if server_dir(name).exists():
                return {"success": False, "error": "A server with that name already exists."}

            gamerules = {}
            for k, v in (cfg.get("gamerules") or {}).items():
                g = _GAMERULE_IDS.get(k)
                if not g:
                    continue
                gamerules[k] = bool(v) if g[2] == "bool" else _clamp_int(v, 0, 100000, g[3])
            meta = {
                "name": name, "software": software, "version": version,
                "loader_version": cfg.get("loader_version") if software == "fabric" else None,
                "installed": False, "memory_mb": _clamp_int(cfg.get("memory_mb"), 512, 65536, 2048),
                "port": _clamp_int(cfg.get("port"), 1024, 65535, 25565),
                "gamerules": gamerules, "gamerules_applied": False,
                "java_component": None, "java_major": None, "created": time.time(),
                "properties": build_server_properties(cfg, version),
            }
            write_server_meta(name, meta)
            with self._srv_lock:
                self._srv_installing.add(name)
            self._srv_push()
            threading.Thread(target=self._srv_install_worker, args=(name,), daemon=True).start()
            return {"success": True}
        except Exception as exc:
            log_exception("create_server")
            return {"success": False, "error": f"Failed to create server: {exc}"}

    def _srv_install_worker(self, name):
        meta = read_server_meta(name)
        try:
            emit = lambda ev, data: self.emit(ev, dict(data, server=name))
            tracker = ProgressTracker(emit)
            tracker.event = "serverInstallProgress"
            tracker.set_task("Fetching version metadata", force=True)
            manifest = meta_cache.get_manifest()
            entry = next((v for v in manifest["versions"] if v["id"] == meta["version"]), None)
            if not entry:
                raise RuntimeError(f"Minecraft {meta['version']} was not found.")
            vjson = meta_cache.get_version_json(meta["version"], entry["url"])
            java_info = vjson.get("javaVersion") or dict(DEFAULT_JAVA_INFO)
            if not resolve_java(java_info, allow_download=True, tracker=tracker):
                raise RuntimeError("Could not find or download a suitable Java runtime.")
            meta["java_component"] = java_info.get("component")
            meta["java_major"] = java_info.get("majorVersion")

            jar = server_dir(name) / "server.jar"
            tracker.set_task(f"Downloading {meta['software'].capitalize()} {meta['version']}", force=True)
            if meta["software"] == "vanilla":
                dl = vjson.get("downloads", {}).get("server")
                if not dl:
                    raise RuntimeError("Mojang has no server download for this version.")
                tracker.reset(dl.get("size") or 1)
                http_download(dl["url"], jar, dl.get("size"), dl.get("sha1"), tracker.add)
            elif meta["software"] == "paper":
                dl = paper_latest_build(meta["version"])
                tracker.reset(dl.get("size") or 1)
                http_download(dl["url"], jar, dl.get("size"), None, tracker.add, user_agent=PAPER_USER_AGENT)
                sha = (dl.get("checksums") or {}).get("sha256")
                if sha:
                    h = hashlib.sha256(jar.read_bytes()).hexdigest()
                    if h != sha:
                        jar.unlink(missing_ok=True)
                        raise RuntimeError("Paper download failed its checksum.")
            else:
                installers = http_get_json(FABRIC_INSTALLER_LIST_URL)
                inst = next((i for i in installers if i.get("stable")), installers[0])["version"]
                url = (f"https://meta.fabricmc.net/v2/versions/loader/{urllib.parse.quote(meta['version'])}/"
                       f"{urllib.parse.quote(meta['loader_version'])}/{urllib.parse.quote(inst)}/server/jar")
                tracker.reset(1)
                http_download(url, jar, None, None, tracker.add)
            (server_dir(name) / "mods").mkdir(exist_ok=True)
            if meta["software"] == "paper":
                (server_dir(name) / "plugins").mkdir(exist_ok=True)
            write_properties(server_dir(name) / "server.properties", meta.pop("properties", {}))
            meta["installed"] = True
            write_server_meta(name, meta)
            self.emit("serverInstallFinished", {"server": name})
        except Exception as exc:
            log_exception("server install")
            self.emit("serverInstallError", {"server": name, "message": f"Couldn't install {name}: {exc}"})
            shutil.rmtree(server_dir(name), ignore_errors=True)
        finally:
            with self._srv_lock:
                self._srv_installing.discard(name)
            self._srv_push()

    def delete_server(self, name):
        try:
            if self._srv_status(name) != "offline":
                return {"success": False, "error": "Stop the server before deleting it."}
            target = server_dir(name)
            if not target.exists() or not is_within(SERVERS_DIR, target):
                return {"success": False, "error": "Server not found."}
            shutil.rmtree(target, ignore_errors=True)
            with self._srv_lock:
                self._srv_rt.pop(name, None)
            self._srv_push()
            return {"success": True}
        except Exception:
            log_exception("delete_server")
            return {"success": False, "error": "Failed to delete the server."}

    def open_server_folder(self, name):
        try:
            d = server_dir(name)
            if not d.is_dir() or not is_within(SERVERS_DIR, d):
                return {"success": False, "error": "Server not found."}
            open_in_file_manager(d)
            return {"success": True}
        except Exception:
            log_exception("open_server_folder")
            return {"success": False, "error": "Couldn't open the folder."}

    def _srv_eula_accepted(self, name) -> bool:
        try:
            text = (server_dir(name) / "eula.txt").read_text(encoding="utf-8", errors="replace")
            return bool(re.search(r"^eula\s*=\s*true\s*$", text, re.M | re.I))
        except OSError:
            return False

    def accept_eula(self, name):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            stamp = time.strftime("%a %b %d %H:%M:%S %Z %Y")
            (server_dir(name) / "eula.txt").write_text(
                "#By changing the setting below to TRUE you are indicating your agreement to our EULA "
                f"({EULA_URL}).\n#{stamp}\neula=true\n", encoding="utf-8")
            return self.start_server(name)
        except Exception:
            log_exception("accept_eula")
            return {"success": False, "error": "Couldn't update eula.txt."}
