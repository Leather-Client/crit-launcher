"""JS-bridge API: changelog."""

import json
import time
import urllib.error

from ..core.http import http_get_json, http_get_text
from ..core.logs import log_exception
from .changelog import CHANGELOG_CACHE, CHANGELOG_REPO, _changelog_mem


class ChangelogApi:
    """Release notes."""

    def get_changelog(self, force=False):
        try:
            now = time.time()
            if not force and _changelog_mem["value"] and now - _changelog_mem["at"] < 600:
                return {"success": True, **_changelog_mem["value"]}
            rel = http_get_json(f"https://api.github.com/repos/{CHANGELOG_REPO}/releases/latest",
                                timeout=15, retries=1)
            asset = next((a for a in rel.get("assets", [])
                          if (a.get("name") or "").lower() == "changelog.txt"), None)
            text = None
            if asset and asset.get("browser_download_url"):
                try:
                    text = http_get_text(asset["browser_download_url"]).strip()
                except Exception:
                    log_exception("changelog.txt download")
            if not text:
                text = (rel.get("body") or "").strip()
            if not text:
                text = "No changelog was published with this release."
            assets = rel.get("assets", [])
            setup = next((a for a in assets if (a.get("name") or "").lower() == "setup.exe"), None) or next(
                (a for a in assets if (a.get("name") or "").lower().endswith(".exe")
                 and "setup" in (a.get("name") or "").lower()), None)
            value = {"tag": rel.get("tag_name"), "name": rel.get("name"),
                     "date": rel.get("published_at"), "text": text[:20000],
                     "setup_url": setup.get("browser_download_url") if setup else None,
                     "setup_size": int(setup.get("size") or 0) if setup else 0,
                     "assets": assets, "release_tag": rel.get("tag_name")}
            _changelog_mem["at"], _changelog_mem["value"] = now, value
            try:
                CHANGELOG_CACHE.write_text(json.dumps(value), encoding="utf-8")
            except OSError:
                pass
            return {"success": True, **value}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"success": False, "error": "No releases have been published yet."}
            log_exception("get_changelog")
        except Exception:
            log_exception("get_changelog")
        try:
            cached = json.loads(CHANGELOG_CACHE.read_text(encoding="utf-8"))
            return {"success": True, "cached": True, **cached}
        except Exception:
            return {"success": False, "error": "Couldn't load the changelog. Check your connection."}
