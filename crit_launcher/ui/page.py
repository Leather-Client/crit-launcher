"""Preparing the HTML page and the home artwork."""

import base64
import shutil
import threading
from pathlib import Path

from ..config.paths import IS_LINUX, PAGE_DIR, RESOURCE_DIR
from ..core.logs import log_exception
from .assets import (
    HOME_BG_ASSETS,
    UI_ASSETS_DIR,
    _assets_lock,
    _download_asset,
    _publish_to_page_dir,
    _seed_local_asset,
)
from .mc_textures import MC_PANO_FILES, _MIN_PANO_BYTES, ensure_mc_panorama


def ensure_home_assets() -> dict:
    """Find/copy/download the home art, return {"success": bool, "files": {name: data-URI}}."""
    with _assets_lock:
        try:
            UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": str(e), "files": {}}
        files = {}
        ok = True
        for name, url in HOME_BG_ASSETS.items():
            path = UI_ASSETS_DIR / name
            
            if not _seed_local_asset(name, path):
                _download_asset(url, path)
            try:
                ready = path.is_file() and path.stat().st_size > 0
            except OSError:
                ready = False
            if not ready:
                ok = False
                continue
            published = _publish_to_page_dir(name, path)
            data = path.read_bytes()
            
            
            if not published or len(data) <= 1_500_000:
                files[name] = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        return {"success": ok, "files": files}


def prepare_page() -> Path:
    """Return the index.html to load. Compiled builds get a fresh copy in PAGE_DIR so assets/ sits beside it."""
    src = RESOURCE_DIR / "index.html"
    if IS_LINUX and (RESOURCE_DIR / "index.linux.html").is_file():
        src = RESOURCE_DIR / "index.linux.html"
    if PAGE_DIR == RESOURCE_DIR:
        return src
    dest = PAGE_DIR / "index.html"
    try:
        PAGE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest
    except OSError:
        log_exception("prepare_page")
        return src


def start_asset_download() -> None:
    """Create assets/ right away and fetch whatever is missing in the background."""
    try:
        UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    if any(not _seed_local_asset(n, UI_ASSETS_DIR / n) for n in HOME_BG_ASSETS):
        threading.Thread(target=ensure_home_assets, daemon=True).start()
    if any(not _seed_local_asset("panorama/" + n, UI_ASSETS_DIR / "panorama" / n, _MIN_PANO_BYTES)
           for n in MC_PANO_FILES):
        threading.Thread(target=ensure_mc_panorama, daemon=True).start()
    
    for name in HOME_BG_ASSETS:
        _publish_to_page_dir(name, UI_ASSETS_DIR / name)
    for name in MC_PANO_FILES:
        _publish_to_page_dir("panorama/" + name, UI_ASSETS_DIR / "panorama" / name)
