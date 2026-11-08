"""JS-bridge API: mod identification and version switching."""

import json
import logging
import os
import re
import shutil
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config.paths import TMP_DIR
from ..core.fsutil import is_within
from ..core.http import http_download, http_get_json, http_post_json
from ..core.logs import log_exception
from ..instances.store import instance_mods_dir, read_instance_meta
from ..minecraft.loaders import LOADER_NICE
from .curseforge import (
    CF_GAME_ID,
    _CF_LOADER_NAMES,
    _cf_download_file,
    _cf_file_compat,
    _cf_get,
    _cf_number,
    _cf_post,
)
from .fingerprint import _mod_fingerprint, _mod_hash
from .modrinth import MODRINTH_API, primary_file


class ModManagerApi:
    """Identifying installed mods and switching their version."""

    def identify_mods(self, instance_name):
        """Match the instance's mod files to Modrinth projects by file hash.

        Returns {"mods": {filename: {project_id, title, slug, link, version_id, version_number,
        version_type}}}. Mods Modrinth doesn't know (or that came from CurseForge) are left out.
        """
        try:
            if not read_instance_meta(instance_name):
                return {"success": False, "error": "Instance not found."}
            folder = instance_mods_dir(instance_name)
            if not folder.is_dir():
                return {"success": True, "mods": {}}
            by_hash = {}
            for p in folder.iterdir():
                low = p.name.lower()
                if p.is_file() and (low.endswith(".jar") or low.endswith(".jar.disabled")):
                    by_hash.setdefault(_mod_hash(p), []).append(p.name)
            if not by_hash:
                return {"success": True, "mods": {}}
            found = http_post_json(f"{MODRINTH_API}/version_files",
                                   {"hashes": list(by_hash), "algorithm": "sha1"}) or {}
            project_ids = sorted({v.get("project_id") for v in found.values() if v.get("project_id")})
            projects = {}
            for i in range(0, len(project_ids), 100):
                chunk = project_ids[i:i + 100]
                url = f"{MODRINTH_API}/projects?ids=" + urllib.parse.quote(json.dumps(chunk))
                for proj in http_get_json(url) or []:
                    projects[proj.get("id")] = proj
            mods = {}
            for digest, version in found.items():
                proj = projects.get(version.get("project_id")) or {}
                slug = proj.get("slug") or version.get("project_id")
                kind = proj.get("project_type") or "mod"
                for name in by_hash.get(digest, []):
                    mods[name] = {
                        "source": "modrinth",
                        "project_id": version.get("project_id"),
                        "title": proj.get("title"),
                        "slug": slug,
                        "link": f"https://modrinth.com/{kind}/{slug}",
                        "version_id": version.get("id"),
                        "version_number": version.get("version_number"),
                        "version_type": version.get("version_type"),
                    }
            # Whatever Modrinth doesn't know may come from CurseForge. A CurseForge problem must
            # not take the Modrinth results away, so it only sets "curseforge" to "unavailable".
            leftovers = [n for names in by_hash.values() for n in names if n not in mods]
            cf_state = "skipped"
            if leftovers:
                try:
                    self._identify_curseforge(folder, leftovers, mods)
                    cf_state = "ok"
                except Exception:
                    log_exception("identify_mods curseforge")
                    cf_state = "unavailable"
            return {"success": True, "mods": mods, "curseforge": cf_state}
        except Exception:
            log_exception("identify_mods")
            return {"success": False, "error": "Couldn't look the mods up on Modrinth."}

    def _identify_curseforge(self, folder: Path, names, mods: dict):
        """Match mod files to CurseForge projects by fingerprint and add them to mods."""
        by_fp = {}
        for name in names:
            by_fp.setdefault(_mod_fingerprint(folder / name), []).append(name)
        found = _cf_post(f"/fingerprints/{CF_GAME_ID}", {"fingerprints": list(by_fp)}) or {}
        matches = {}   # mod id -> [(file info, installed file names)]
        for match in found.get("exactMatches") or []:
            info = match.get("file") or {}
            mod_id = match.get("id") or info.get("modId")
            fp = info.get("fileFingerprint")
            if mod_id and fp in by_fp and info.get("id") is not None:
                matches.setdefault(int(mod_id), []).append((info, by_fp[fp]))
        if not matches:
            return

        def fetch(mod_id):
            try:
                return mod_id, _cf_get(f"/mods/{mod_id}") or {}
            except Exception:
                logging.info("Couldn't read CurseForge project %s", mod_id)
                return mod_id, {}

        with ThreadPoolExecutor(max_workers=4) as pool:
            projects = dict(pool.map(fetch, list(matches)))
        kinds = {1: "release", 2: "beta", 3: "alpha"}
        for mod_id, entries in matches.items():
            proj = projects.get(mod_id) or {}
            slug = proj.get("slug")
            link = (proj.get("links") or {}).get("websiteUrl")
            if not (isinstance(link, str) and link.startswith("https://www.curseforge.com/")):
                link = f"https://www.curseforge.com/minecraft/mc-mods/{slug}" if slug else None
            for info, installed in entries:
                for name in installed:
                    mods[name] = {
                        "source": "curseforge",
                        "project_id": f"cf:{mod_id}",
                        "title": proj.get("name") or info.get("displayName"),
                        "slug": slug,
                        "link": link,
                        "version_id": str(info["id"]),
                        "version_number": str(info.get("displayName") or info.get("fileName") or ""),
                        "version_type": kinds.get(int(info.get("releaseType") or 1), "release"),
                    }

    def get_mod_versions(self, instance_name, project_id, current_version_id=None):
        """All published versions of one mod, each marked compatible or not with this instance."""
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            mc, loader = meta.get("version"), meta.get("loader", "vanilla")
            if str(project_id).startswith("cf:"):
                return self._cf_mod_versions(mc, loader, project_id, current_version_id)
            versions = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(str(project_id))}/version")
            out = []
            for v in versions or []:
                if not v.get("id") or not v.get("files"):
                    continue
                ok_mc = mc in (v.get("game_versions") or [])
                ok_loader = loader in (v.get("loaders") or [])
                if ok_mc and ok_loader:
                    reason = ""
                elif ok_loader:
                    reason = f"Not for Minecraft {mc}"
                elif ok_mc:
                    reason = f"Not for {LOADER_NICE.get(loader, loader)}"
                else:
                    reason = f"Not for {LOADER_NICE.get(loader, loader)} {mc}"
                out.append({
                    "id": str(v["id"]),
                    "name": str(v.get("name") or v.get("version_number") or v["id"]),
                    "number": str(v.get("version_number") or ""),
                    "type": str(v.get("version_type") or "release"),
                    "date": v.get("date_published"),
                    "mc": [str(g) for g in (v.get("game_versions") or [])][-4:],
                    "loaders": [str(g) for g in (v.get("loaders") or [])],
                    "compatible": ok_mc and ok_loader,
                    "reason": reason,
                    "current": str(v["id"]) == str(current_version_id or ""),
                })
            out.sort(key=lambda v: str(v.get("date") or ""), reverse=True)
            return {"success": True, "versions": out[:150], "total": len(out),
                    "minecraft": mc, "loader": loader}
        except Exception:
            log_exception("get_mod_versions")
            return {"success": False, "error": "Couldn't load this mod's versions."}

    def _cf_mod_versions(self, mc, loader, project_id, current_version_id):
        mod_id = _cf_number(project_id)
        raw, total = [], 0
        for index in (0, 50, 100):          # CurseForge pages hold at most 50 files
            page = _cf_get(f"/mods/{mod_id}/files", {"pageSize": 50, "index": index}, unwrap=False) or {}
            batch = [f for f in (page.get("data") or []) if isinstance(f, dict)]
            total = int((page.get("pagination") or {}).get("totalCount") or 0)
            raw += batch
            if len(batch) < 50:
                break
        kinds = {1: "release", 2: "beta", 3: "alpha"}
        out = []
        for f in raw:
            if f.get("id") is None or f.get("isServerPack") or not str(f.get("fileName") or "").lower().endswith(".jar"):
                continue
            ok_mc, ok_loader = _cf_file_compat(f, mc, loader)
            if ok_mc and ok_loader:
                reason = ""
            elif ok_loader:
                reason = f"Not for Minecraft {mc}"
            elif ok_mc:
                reason = f"Not for {LOADER_NICE.get(loader, loader)}"
            else:
                reason = f"Not for {LOADER_NICE.get(loader, loader)} {mc}"
            games = [str(g) for g in (f.get("gameVersions") or [])]
            out.append({
                "id": str(f["id"]),
                "name": str(f.get("displayName") or f.get("fileName") or f["id"]),
                "number": str(f.get("displayName") or f.get("fileName") or ""),
                "type": kinds.get(int(f.get("releaseType") or 1), "release"),
                "date": f.get("fileDate"),
                "mc": [g for g in games if re.match(r"^\d+\.\d+", g)][-4:],
                "loaders": [g for g in games if g.lower() in _CF_LOADER_NAMES],
                "compatible": ok_mc and ok_loader,
                "reason": reason,
                "current": str(f["id"]) == str(current_version_id or ""),
            })
        out.sort(key=lambda v: str(v.get("date") or ""), reverse=True)
        return {"success": True, "versions": out[:150],
                "total": max(len(out), total) if len(raw) >= 150 else len(out),
                "minecraft": mc, "loader": loader}

    def switch_mod_version(self, instance_name, filename, project_id, version_id, allow_incompatible=False):
        """Replace one installed mod file with another version of the same Modrinth project.

        The new file is downloaded and verified first and the old one is removed only after that,
        so a failed switch leaves the old version in place. A disabled mod stays disabled.
        Required dependencies of the new version are not installed.
        """
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            with self._proc_lock:
                if instance_name in self._processes:
                    return {"success": False, "error": "Close the game first - its files are in use."}
            folder = instance_mods_dir(instance_name)
            old_name = Path(str(filename or "")).name
            old_path = folder / old_name
            if not old_name or not old_path.is_file() or not is_within(folder, old_path):
                return {"success": False, "error": "That mod file no longer exists."}
            mc, loader = meta.get("version"), meta.get("loader", "vanilla")
            is_cf = str(project_id).startswith("cf:")
            if is_cf:
                mod_id = _cf_number(project_id)
                if not str(version_id).isdigit():
                    return {"success": False, "error": "Invalid mod version."}
                file_info = _cf_get(f"/mods/{mod_id}/files/{int(version_id)}") or {}
                if int(file_info.get("modId") or 0) != mod_id:
                    return {"success": False, "error": "That version belongs to a different mod."}
                ok_mc, ok_loader = _cf_file_compat(file_info, mc, loader)
                compatible = ok_mc and ok_loader
                new_name = Path(str(file_info.get("fileName") or "")).name
            else:
                version = http_get_json(f"{MODRINTH_API}/version/{urllib.parse.quote(str(version_id))}")
                if str(version.get("project_id")) != str(project_id):
                    return {"success": False, "error": "That version belongs to a different mod."}
                compatible = mc in (version.get("game_versions") or []) and loader in (version.get("loaders") or [])
                file_info = primary_file(version)
                new_name = Path(file_info["filename"]).name if file_info else ""
            if not compatible and not allow_incompatible:
                return {"success": False, "incompatible": True,
                        "error": "That version isn't made for this instance."}
            was_disabled = old_name.lower().endswith(".disabled")
            if not file_info or not new_name:
                return {"success": False, "error": "That version has no downloadable file."}
            if not new_name.lower().endswith(".jar"):
                return {"success": False, "error": "That version isn't a mod .jar file."}
            final_name = new_name + (".disabled" if was_disabled else "")
            dest = folder / final_name
            if not is_within(folder, dest):
                return {"success": False, "error": "Unsafe file name in mod."}
            if is_cf:
                # Download into a scratch folder first, then move it into place once it is complete.
                work = TMP_DIR / f"cf_switch_{uuid.uuid4().hex}"
                try:
                    work.mkdir(parents=True, exist_ok=True)
                    got = _cf_download_file(mod_id, file_info, work)
                    os.replace(work / got, dest)
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            else:
                http_download(file_info["url"], dest, file_info.get("size"),
                              (file_info.get("hashes") or {}).get("sha1"))
            if old_path != dest and old_path.exists():
                old_path.unlink()
            return {"success": True, "file": final_name, "old": old_name}
        except PermissionError:
            return {"success": False, "error": "Couldn't replace it - another program is using the file."}
        except Exception as exc:
            log_exception("switch_mod_version")
            return {"success": False, "error": f"Couldn't switch the mod version: {exc}"}
