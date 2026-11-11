"""JS-bridge API: self-update."""

import os
import threading
import time
from pathlib import Path

from ..config.paths import DATA_DIR, IS_LINUX
from ..config.settings import settings_store
from ..core.http import http_download
from ..core.logs import log_exception
from ..version import APP_VERSION
from .changelog import CHANGELOG_REPO, _version_tuple
from .signature import update_signed_sha256


class UpdatesApi:
    """Launcher self-update."""

    def check_for_updates(self, force=False):
        try:
            res = self.get_changelog(bool(force))
            if not res.get("success"):
                return {"success": False, "error": res.get("error") or "Couldn't check for updates."}
            tag = res.get("tag") or ""
            muted = settings_store.read().get("update_muted_at") == APP_VERSION
            return {"success": True, "current": APP_VERSION, "latest": tag,
                    "available": _version_tuple(tag) > _version_tuple(APP_VERSION),
                    "installer": bool(res.get("setup_url")), "muted": muted}
        except Exception:
            log_exception("check_for_updates")
            return {"success": False, "error": "Couldn't check for updates."}

    def mute_updates(self):
        try:
            settings_store.update(lambda st: st.__setitem__("update_muted_at", APP_VERSION))
            return {"success": True}
        except Exception:
            log_exception("mute_updates")
            return {"success": False, "error": "Couldn't save that."}

    def install_update(self):
        if IS_LINUX:
            try:
                import webbrowser
                webbrowser.open(f"https://github.com/{CHANGELOG_REPO}/releases/latest")
            except Exception:
                log_exception("open releases page")
            return {"success": False,
                    "error": "Opened the releases page in your browser. Download the new AppImage there."}
        if os.name != "nt":
            return {"success": False, "error": "Automatic updating is only available on Windows."}
        threading.Thread(target=self._update_worker, daemon=True).start()
        return {"success": True}

    def _update_worker(self):
        try:
            res = self.get_changelog(True)
            url = res.get("setup_url") if res.get("success") else None
            if not url:
                self.emit("updateError", {"message": "The latest release has no setup.exe to install."})
                return
            size = int(res.get("setup_size") or 0)
            dest = DATA_DIR / "update" / "setup.exe"

            # A hijacked release is only dangerous because we run it, so the
            # signature has to be checked before anything reaches startfile.
            try:
                expected = update_signed_sha256(res, Path(url).name)
            except RuntimeError as exc:
                self.emit("updateError", {"message": str(exc)})
                return
            done = [0]

            def progress(n):
                done[0] += n
                if size:
                    self.emit("updateProgress", {"percent": min(100, int(done[0] * 100 / size))})

            self.emit("updateProgress", {"percent": 0})
            http_download(url, dest, size or None, expected, progress_cb=progress)
            self.emit("updateInstalling", {})
            os.startfile(str(dest))
            time.sleep(1.5)
            if self._window is not None:
                self._window.destroy()
        except Exception:
            log_exception("update")
            self.emit("updateError", {"message": "Couldn't download the update. Check your connection and try again."})
