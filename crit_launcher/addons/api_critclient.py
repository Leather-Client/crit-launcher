"""JS-bridge API: Crit Client."""

import logging
import shutil
import urllib.parse
from pathlib import Path

from ..core.http import http_download, http_get_json
from ..core.logs import log_exception
from ..core.progress import ProgressTracker
from ..instances.store import instance_dir, instance_mods_dir
from .critclient import CRIT_CLIENT_BASE, CRIT_CLIENT_MODS, CRIT_CLIENT_VERSIONS_URL


class CritClientApi:
    """The bundled Crit Client mod set."""

    def _critclient_versions(self) -> dict:
        data = http_get_json(CRIT_CLIENT_VERSIONS_URL, timeout=15, retries=1)
        out = {}
        if not isinstance(data, dict):
            return out
        for loader in ("fabric", "forge"):
            entries = data.get(loader)
            if not isinstance(entries, dict):
                continue
            clean = {}
            for mc, jar in entries.items():
                if (isinstance(mc, str) and isinstance(jar, str) and jar.lower().endswith(".jar")
                        and Path(jar).name == jar and "/" not in jar and "\\" not in jar):
                    clean[mc] = jar
            if clean:
                out[loader] = clean
        return out

    def get_critclient_info(self):
        try:
            versions = self._critclient_versions()
            if not versions:
                return {"success": False, "error": "Crit Client has no versions available right now."}
            return {"success": True, "versions": versions}
        except Exception:
            log_exception("get_critclient_info")
            return {"success": False, "error": "Couldn't reach the Crit Client server to check supported versions."}

    def _critclient_worker(self, name, mc_version, loader, jar_name):
        try:
            self.emit("installStarted", {"instance": name, "title": f"Installing Crit Client into {name}..."})
            tracker = ProgressTracker(self.emit)
            mods_dir = instance_mods_dir(name)
            mods_dir.mkdir(parents=True, exist_ok=True)

            tracker.reset(1_000_000)
            tracker.set_task("Downloading Crit Client", force=True)
            jar_name = Path(jar_name).name
            http_download(CRIT_CLIENT_BASE + urllib.parse.quote(jar_name), mods_dir / jar_name,
                          progress_cb=tracker.add)

            tracker.set_task("Checking which mods are available", force=True)
            picks = []
            for slug in CRIT_CLIENT_MODS:
                try:
                    version = self._find_version(slug, loader, mc_version)
                except Exception:
                    log_exception(f"crit client mod lookup: {slug}")
                    version = None
                if version:
                    picks.append((slug, version))
                else:
                    logging.info("Crit Client: %s has no %s build for %s, skipping", slug, loader, mc_version)

            tracker.reset(len(picks) + 1, unit="steps")
            visited, files = set(), []
            for slug, version in picks:
                tracker.set_task(f"Downloading {slug}", force=True)
                pid = version.get("project_id")
                if pid in visited:
                    tracker.add(1)
                    continue
                visited.add(pid)
                try:
                    self._install_mod_with_deps(version, mods_dir, loader, mc_version, visited, files)
                except Exception:
                    log_exception(f"crit client mod install failed: {slug}")
                tracker.add(1)
            tracker.add(1)

            self.emit("installFinished", {"instance": name, "message": f"Crit Client installed into {name}"})
        except Exception as exc:
            log_exception("crit client install failed")
            shutil.rmtree(instance_dir(name), ignore_errors=True)
            self.emit("installError", {"message": f"Crit Client install failed: {exc}"})
        finally:
            with self._proc_lock:
                self._installing.discard(name)
            self.push_state()
