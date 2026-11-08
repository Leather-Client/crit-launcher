"""Minecraft GUI textures and panorama for the Minecraft theme."""

import base64
import logging
import shutil
import threading
import urllib.request
import zipfile
from pathlib import Path

from ..config.paths import VERSIONS_DIR
from ..core.http import USER_AGENT, http_get_json
from ..core.logs import log_exception
from ..minecraft.constants import VERSION_MANIFEST_URL
from .assets import (
    UI_ASSETS_DIR,
    _ASSETS_RAW,
    _PNG_MAGIC,
    _download_asset,
    _publish_to_page_dir,
    _seed_local_asset,
)


MC_TEX_DIR = UI_ASSETS_DIR / "mc"
_MC_SPRITES = "assets/minecraft/textures/gui/sprites/widget/"
MC_TEX_MEMBERS = {
    "button.png": _MC_SPRITES + "button.png",
    "button_highlighted.png": _MC_SPRITES + "button_highlighted.png",
    "button_disabled.png": _MC_SPRITES + "button_disabled.png",
    "minecraft.png": "assets/minecraft/textures/gui/title/minecraft.png",
}
_MC_REQUIRED = ("button.png",)
MC_PANO_DIR = UI_ASSETS_DIR / "panorama"
MC_PANO_FILES = ["panorama_%d.png" % i for i in range(6)]
MC_PANO_ASSETS = {n: _ASSETS_RAW + n for n in MC_PANO_FILES}
_MIN_PANO_BYTES = 4096                    


def ensure_mc_panorama() -> dict:
    """Make sure all six panorama faces are in assets/panorama/, downloading any that are missing."""
    with _mc_tex_lock:
        try:
            MC_PANO_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": str(e)}
        ok = True
        files = {}
        for name, url in MC_PANO_ASSETS.items():
            path = MC_PANO_DIR / name
            if not _seed_local_asset("panorama/" + name, path, _MIN_PANO_BYTES):
                _download_asset(url, path)
            if path.exists() and path.stat().st_size >= _MIN_PANO_BYTES:
                _publish_to_page_dir("panorama/" + name, path)
                
                files[name] = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            else:
                ok = False
        return {"success": ok, "dir": str(MC_PANO_DIR), "files": files}


_mc_tex_lock = threading.Lock()


def _mc_missing() -> list:
    return [n for n in MC_TEX_MEMBERS if not (MC_TEX_DIR / n).exists() or (MC_TEX_DIR / n).stat().st_size == 0]


def _mc_extract(jar: Path, missing: list) -> None:
    try:
        with zipfile.ZipFile(jar) as zf:
            names = set(zf.namelist())
            for n in list(missing):
                member = MC_TEX_MEMBERS[n]
                if member not in names:
                    continue
                data = zf.read(member)
                if data.startswith(_PNG_MAGIC):
                    (MC_TEX_DIR / n).write_bytes(data)
                    missing.remove(n)
    except Exception:
        logging.warning("Couldn't read textures from %s", jar)


def _mc_download_client():
    try:
        manifest = http_get_json(VERSION_MANIFEST_URL)
        latest = manifest["latest"]["release"]
        entry = next(v for v in manifest["versions"] if v["id"] == latest)
        url = http_get_json(entry["url"])["downloads"]["client"]["url"]
        dest = MC_TEX_DIR / "_client.part"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return dest
    except Exception:
        log_exception("_mc_download_client")
        return None


def ensure_mc_textures() -> dict:
    with _mc_tex_lock:
        try:
            MC_TEX_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": str(e), "files": {}}
        missing = _mc_missing()
        
        for name in list(missing):
            if _seed_local_asset("mc/" + name, MC_TEX_DIR / name):
                missing.remove(name)
        if missing:
            try:
                jars = sorted((p for p in VERSIONS_DIR.glob("*/*.jar") if p.parent.name == p.stem),
                              key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                jars = []
            for jar in jars:
                _mc_extract(jar, missing)
                if not missing:
                    break
        if missing:
            part = _mc_download_client()
            if part:
                try:
                    _mc_extract(part, missing)
                finally:
                    try:
                        part.unlink()
                    except OSError:
                        pass
        files = {}
        for n in MC_TEX_MEMBERS:
            p = MC_TEX_DIR / n
            if p.exists() and p.stat().st_size > 0:
                _publish_to_page_dir("mc/" + n, p)
                files[n] = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        return {"success": all(n in files for n in _MC_REQUIRED), "files": files}
