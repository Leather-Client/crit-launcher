"""Locating, seeding and downloading UI artwork."""

import logging
import shutil
import threading
import urllib.request
from pathlib import Path

from ..config.paths import APP_DIR, DATA_DIR, IS_LINUX, PAGE_DIR, RESOURCE_DIR
from ..core.http import USER_AGENT
from ..core.logs import log_exception


UI_ASSETS_DIR = (DATA_DIR / "ui_assets") if IS_LINUX else (APP_DIR / "assets")
_ASSETS_RAW = "https://raw.githubusercontent.com/snigglaberry/crit-launcher/main/assets/"
HOME_BG_ASSETS = {                       
    "dark_bg.png": _ASSETS_RAW + "dark_Bg.png",
    "light_bg.png": _ASSETS_RAW + "light_bg.png",
}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_assets_lock = threading.Lock()
_ASSET_SEARCH_ROOTS = (UI_ASSETS_DIR, RESOURCE_DIR / "assets", APP_DIR, RESOURCE_DIR)
_ASSET_ALIASES = {"dark_bg.png": "dark_Bg.png"}   


def _find_asset(rel: str, min_size: int = 1):
    """First existing copy of rel in any known asset folder, or None."""
    rel = str(rel).replace("\\", "/")
    head, sep, tail = rel.rpartition("/")
    names = [rel]
    alt = _ASSET_ALIASES.get(tail)
    if alt:
        names.append(head + sep + alt)
    for root in _ASSET_SEARCH_ROOTS:
        for name in names:
            try:
                p = root / name
                if p.is_file() and p.stat().st_size >= min_size:
                    return p
            except OSError:
                continue
    return None


def _publish_to_page_dir(rel: str, src: Path) -> bool:
    """Copy an asset into <page>/assets so the page's relative assets/... URLs resolve in a build.

    Compiled builds unpack index.html into a temp folder, so without this the only way to show the
    art is a huge base64 data URI over the JS bridge, which large backgrounds don't survive.
    """
    try:
        if PAGE_DIR == UI_ASSETS_DIR.parent or not src.is_file():
            return False
        dest = PAGE_DIR / "assets" / rel
        try:
            if dest.is_file() and dest.stat().st_size == src.stat().st_size:
                return True
        except OSError:
            pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return True
    except OSError:
        log_exception("publish asset " + rel)
        return False


def _seed_local_asset(rel: str, dest: Path, min_size: int = 1) -> bool:
    """Copy a shipped asset (bundled/extra assets folder) into dest when it isn't there yet."""
    try:
        if dest.is_file() and dest.stat().st_size >= min_size:
            return True
    except OSError:
        pass
    found = _find_asset(rel, min_size)
    if not found:
        return False
    try:
        if found.resolve() == dest.resolve():
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, dest)
        return dest.is_file() and dest.stat().st_size >= min_size
    except OSError:
        log_exception("copy bundled asset " + rel)
        return False


def _download_asset(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if not data.startswith(_PNG_MAGIC):
            return False
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except Exception:
        logging.warning("Couldn't download %s", url)
        return False
