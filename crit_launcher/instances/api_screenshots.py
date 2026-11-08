"""JS-bridge API: screenshots."""

import base64
from pathlib import Path

from ..core.fsutil import is_within
from ..core.logs import log_exception
from .store import instance_game_dir, read_instance_meta


class ScreenshotsApi:
    """Instance screenshots."""

    def get_instance_screenshots(self, instance_name):
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            folder = instance_game_dir(instance_name) / "screenshots"
            if not folder.is_dir():
                return {"success": True, "screenshots": []}
            shots = []
            for p in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    shots.append({"file": p.name, "taken": p.stat().st_mtime})
            return {"success": True, "screenshots": shots}
        except Exception:
            log_exception("get_instance_screenshots")
            return {"success": False, "error": "Couldn't read the screenshots folder."}

    def get_screenshot_image(self, instance_name, filename):
        try:
            folder = instance_game_dir(instance_name) / "screenshots"
            path = folder / Path(filename).name
            if not path.is_file() or not is_within(folder, path):
                return {"success": False, "error": "Screenshot not found."}
            if path.stat().st_size > 40_000_000:
                return {"success": False, "error": "That screenshot is too large to preview."}
            ext = path.suffix.lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return {"success": True, "image": f"data:{mime};base64,{data}"}
        except Exception:
            log_exception("get_screenshot_image")
            return {"success": False, "error": "Couldn't read that screenshot."}
