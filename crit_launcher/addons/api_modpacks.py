"""JS-bridge API: modpacks."""

import json
import re
import shutil
import threading
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config.paths import TMP_DIR
from ..core.fsutil import is_within
from ..core.http import http_download, http_get_json
from ..core.logs import log_exception
from ..core.progress import ProgressTracker
from ..instances.store import instance_game_dir, unique_instance_name
from ..minecraft.libraries import download_all, entry_weight
from .curseforge import _cf_download_file, _cf_file_sha1, _cf_get, _cf_number
from .modrinth import MODRINTH_API, pick_best_version, primary_file


class ModpacksApi:
    """Installing modpacks as new instances."""

    def get_modpack_versions(self, project_id):
        """Every published version of a modpack, newest first, for the version picker.

        Each entry's "id" is what install_modpack takes as version_id.
        """
        try:
            out = []
            if isinstance(project_id, str) and project_id.startswith("cf:"):
                mod_id = _cf_number(project_id)
                files = _cf_get(f"/mods/{mod_id}/files") or []
                if isinstance(files, dict):
                    files = files.get("data") or []
                files = [f for f in files if isinstance(f, dict) and f.get("id") is not None
                         and not f.get("isServerPack")]
                zips = [f for f in files if str(f.get("fileName") or "").lower().endswith(".zip")]
                files = zips or files
                files.sort(key=lambda f: str(f.get("fileDate") or ""), reverse=True)
                kinds = {1: "release", 2: "beta", 3: "alpha"}
                for f in files:
                    games = [str(g) for g in (f.get("gameVersions") or [])]
                    out.append({
                        "id": str(f["id"]),
                        "name": str(f.get("displayName") or f.get("fileName") or f["id"]),
                        "type": kinds.get(int(f.get("releaseType") or 1), "release"),
                        "date": f.get("fileDate"),
                        "mc": [g for g in games if re.match(r"^\d+\.\d+", g)],
                        "loaders": [g for g in games if not re.match(r"^\d", g)],
                    })
            else:
                versions = http_get_json(
                    f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version")
                for v in versions or []:
                    if not v.get("id") or not v.get("files"):
                        continue
                    out.append({
                        "id": str(v["id"]),
                        "name": str(v.get("name") or v.get("version_number") or v["id"]),
                        "number": str(v.get("version_number") or ""),
                        "type": str(v.get("version_type") or "release"),
                        "date": v.get("date_published"),
                        "mc": [str(g) for g in (v.get("game_versions") or [])],
                        "loaders": [str(g) for g in (v.get("loaders") or [])],
                    })
                out.sort(key=lambda v: str(v.get("date") or ""), reverse=True)
            return {"success": True, "versions": out}
        except Exception:
            log_exception("get_modpack_versions")
            return {"success": False, "error": "Couldn't load this modpack's versions."}

    def install_modpack(self, project_id, version_id=None):
        """Install a modpack as a new instance. version_id picks a specific version from
        get_modpack_versions; without it the newest release is used."""
        if version_id is not None and not isinstance(version_id, (str, int)):
            return {"success": False, "error": "Invalid modpack version."}
        version_id = str(version_id).strip() if version_id not in (None, "") else None
        with self._proc_lock:
            if self._addon_busy:
                return {"success": False, "error": "Another addon install is already running."}
            self._addon_busy = True
        if isinstance(project_id, str) and project_id.startswith("cf:"):
            threading.Thread(target=self._cf_modpack_worker, args=(project_id, version_id),
                             daemon=True).start()
        else:
            threading.Thread(target=self._modpack_worker, args=(project_id, version_id),
                             daemon=True).start()
        return {"success": True}

    def _cf_modpack_worker(self, project_id, version_id=None):
        """Install a CurseForge modpack: manifests list projectID/fileID pairs plus overrides."""
        pack_path = work = None
        try:
            tracker = ProgressTracker(self.emit)
            tracker.event = "addonProgress"
            tracker.set_task("Fetching modpack", force=True)

            mod_id = _cf_number(project_id)
            project = _cf_get(f"/mods/{mod_id}") or {}
            files = _cf_get(f"/mods/{mod_id}/files") or []
            if isinstance(files, dict):
                files = files.get("data") or []
            files = [f for f in files if isinstance(f, dict) and not f.get("isServerPack")]
            zips = [f for f in files if str(f.get("fileName") or "").lower().endswith(".zip")]
            files = zips or files
            files.sort(key=lambda f: str(f.get("fileDate") or ""), reverse=True)
            if version_id:
                chosen = next((f for f in files if str(f.get("id")) == version_id), None)
                if not chosen:
                    raise RuntimeError("That modpack version isn't available anymore.")
            else:
                releases = [f for f in files if int(f.get("releaseType") or 1) == 1]
                pool = releases or files
                if not pool:
                    raise RuntimeError("This modpack has no downloadable file.")
                chosen = pool[0]

            work = TMP_DIR / f"cf_pack_{mod_id}"
            work.mkdir(parents=True, exist_ok=True)
            tracker.reset(chosen.get("fileLength") or 1_000_000)
            tracker.set_task("Downloading modpack", force=True)
            pack_path = work / _cf_download_file(mod_id, chosen, work)

            with zipfile.ZipFile(pack_path) as zf:
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except KeyError:
                    raise RuntimeError("That CurseForge pack is missing its manifest.json.")
            mc = manifest.get("minecraft") or {}
            mc_version = mc.get("version")
            if not mc_version:
                raise RuntimeError("This modpack doesn't say which Minecraft version it needs.")

            loader, loader_version = "vanilla", None
            for entry in (mc.get("modLoaders") or []):
                if not isinstance(entry, dict) or not entry.get("primary"):
                    continue
                kind, _, ver = str(entry.get("id") or "").partition("-")
                if kind == "fabric":
                    loader, loader_version = "fabric", ver
                elif kind == "forge":
                    loader, loader_version = "forge", ver
                elif kind == "neoforge":
                    loader, loader_version = "neoforge", ver
                elif kind in ("quilt", "liteloader"):
                    raise RuntimeError(f"This modpack needs {kind.capitalize()}, which Crit Launcher doesn't support yet.")

            name = unique_instance_name(manifest.get("name") or project.get("name") or "Modpack")
            self._make_instance(name, mc_version, loader, loader_version)
            game_dir = instance_game_dir(name)
            mods_dir = game_dir / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)

            wanted = []
            for item in (manifest.get("files") or []):
                if not isinstance(item, dict) or item.get("required") is False:
                    continue
                pid, fid = item.get("projectID"), item.get("fileID")
                if pid and fid:
                    wanted.append((int(pid), int(fid)))

            tracker.set_task("Resolving modpack files", force=True)

            def _file_info(pair):
                try:
                    info = _cf_get(f"/mods/{pair[0]}/files/{pair[1]}") or {}
                    return info if isinstance(info, dict) else None
                except Exception:
                    return None

            infos = []
            if wanted:
                with ThreadPoolExecutor(max_workers=6) as pool_exec:
                    for info in pool_exec.map(_file_info, wanted):
                        if info:
                            infos.append(info)

            items = []
            for info in infos:
                fname = Path(str(info.get("fileName") or "")).name
                url = info.get("downloadUrl")
                if not fname or not isinstance(url, str) or not url.startswith("https://"):
                    continue
                if not is_within(mods_dir, mods_dir / fname):
                    continue
                items.append(({"path": fname, "url": url, "size": info.get("fileLength"),
                               "sha1": _cf_file_sha1(info)}, mods_dir))

            tracker.reset(sum(entry_weight(e) for e, _ in items) + 1)
            tracker.set_task("Downloading modpack files", force=True)
            download_all(items, tracker, workers=8)

            tracker.set_task("Applying overrides", force=True)
            with zipfile.ZipFile(pack_path) as zf:
                for member in zf.namelist():
                    for prefix in ("overrides/", "client-overrides/"):
                        if not member.startswith(prefix):
                            continue
                        rel = member[len(prefix):]
                        if not rel:
                            break
                        dest = game_dir / rel
                        if not is_within(game_dir, dest):
                            break
                        if member.endswith("/"):
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(zf.read(member))
                        break

            self.push_state()
            self.emit("addonFinished", {"name": name})
        except Exception as exc:
            log_exception("curseforge modpack install failed")
            self.emit("addonError", {"message": str(exc) or "Modpack install failed."})
        finally:
            try:
                if pack_path:
                    Path(pack_path).unlink(missing_ok=True)
                if work:
                    shutil.rmtree(work, ignore_errors=True)
            except OSError:
                pass
            with self._proc_lock:
                self._addon_busy = False

    def _modpack_worker(self, project_id, version_id=None):
        pack_path = None
        try:
            tracker = ProgressTracker(self.emit)
            tracker.event = "addonProgress"
            tracker.set_task("Fetching modpack", force=True)

            project = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}")
            versions = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version")
            if version_id:
                version = next((v for v in versions if str(v.get("id")) == version_id), None)
                if not version:
                    raise RuntimeError("That modpack version isn't available anymore.")
            else:
                version = pick_best_version(versions)
            if not version:
                raise RuntimeError("This modpack has no downloadable versions.")
            files = version.get("files", [])
            pack_file = (next((f for f in files if f.get("filename", "").endswith(".mrpack")), None)
                         or primary_file(version))
            if not pack_file:
                raise RuntimeError("This modpack has no downloadable file.")

            pack_path = TMP_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', project_id)}.mrpack"
            tracker.reset(pack_file.get("size") or 1_000_000)
            tracker.set_task("Downloading modpack", force=True)
            http_download(pack_file["url"], pack_path, pack_file.get("size"),
                          (pack_file.get("hashes") or {}).get("sha1"), tracker.add)

            with zipfile.ZipFile(pack_path) as zf:
                index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
                deps = index.get("dependencies", {})
                mc_version = deps.get("minecraft")
                if not mc_version:
                    raise RuntimeError("This modpack doesn't say which Minecraft version it needs.")

                loader, loader_version = "vanilla", None
                if "fabric-loader" in deps:
                    loader, loader_version = "fabric", deps["fabric-loader"]
                elif "forge" in deps:
                    loader, loader_version = "forge", deps["forge"]
                elif "neoforge" in deps:
                    loader, loader_version = "neoforge", deps["neoforge"]
                elif "quilt-loader" in deps:
                    raise RuntimeError("This modpack needs Quilt, which Crit Launcher doesn't support yet.")

                name = unique_instance_name(index.get("name") or project.get("title") or "Modpack")
                self._make_instance(name, mc_version, loader, loader_version)
                game_dir = instance_game_dir(name)

                items = []
                for f in index.get("files", []):
                    if (f.get("env") or {}).get("client") == "unsupported":
                        continue
                    rel = f.get("path", "")
                    urls = f.get("downloads") or []
                    if not rel or not urls or not is_within(game_dir, game_dir / rel):
                        continue
                    items.append(({
                        "path": rel, "url": urls[0], "size": f.get("fileSize"),
                        "sha1": (f.get("hashes") or {}).get("sha1"),
                    }, game_dir))

                tracker.reset(sum(entry_weight(e) for e, _ in items) + 1)
                tracker.set_task("Downloading modpack files", force=True)
                download_all(items, tracker, workers=8)

                tracker.set_task("Applying overrides", force=True)
                for member in zf.namelist():
                    for prefix in ("overrides/", "client-overrides/"):
                        if member.startswith(prefix):
                            rel = member[len(prefix):]
                            if not rel:
                                break
                            dest = game_dir / rel
                            if not is_within(game_dir, dest):
                                break
                            if member.endswith("/"):
                                dest.mkdir(parents=True, exist_ok=True)
                            else:
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                dest.write_bytes(zf.read(member))
                            break

            self.push_state()
            self.emit("addonFinished", {"name": name})
        except Exception as exc:
            log_exception("modpack install failed")
            self.emit("addonError", {"message": str(exc) or "Modpack install failed."})
        finally:
            if pack_path:
                try:
                    Path(pack_path).unlink(missing_ok=True)
                except OSError:
                    pass
            with self._proc_lock:
                self._addon_busy = False
