"""CustomSkinLoader config for offline/Ely skins."""

import json
import os
import re
from pathlib import Path

from ..core.fsutil import _write_bytes_atomic
from ..instances.store import instance_game_dir, instance_mods_dir


CSL_PROJECT = "customskinloader"


def _csl_find_config(instance_name: str):
    game = instance_game_dir(instance_name)
    primary = game / "CustomSkinLoader" / "CustomSkinLoader.json"
    if primary.is_file():
        return primary
    skip = {"saves", "mods", "resourcepacks", "shaderpacks", "screenshots", "logs",
            "crash-reports", "schematics"}
    stack = [(game, 0)]
    try:
        while stack:
            cur, depth = stack.pop()
            with os.scandir(cur) as it:
                for e in it:
                    low = e.name.lower()
                    if e.is_file(follow_symlinks=False):
                        if low == "customskinloader.json" and cur.name.lower() == "customskinloader":
                            return Path(e.path)
                    elif e.is_dir(follow_symlinks=False) and depth < 2 and low not in skip:
                        stack.append((Path(e.path), depth + 1))
    except OSError:
        pass
    return None


def _csl_config_path(instance_name: str) -> Path:
    return _csl_find_config(instance_name) or (
        instance_game_dir(instance_name) / "CustomSkinLoader" / "CustomSkinLoader.json")


def _csl_local_dir(instance_name: str) -> Path:
    return _csl_config_path(instance_name).parent / "LocalSkin"


CSL_CONFIG_VERSION = "15.0.1"
CSL_CONFIG_BUILD = 40
CSL_RUN_ONCE_MSG = "You must run the game at least once to use this feature."


def _csl_write_config(instance_name: str):
    path = _csl_config_path(instance_name)
    version, build = CSL_CONFIG_VERSION, CSL_CONFIG_BUILD
    try:
        old = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(old, dict):
            version = old.get("version", version)
            build = old.get("buildNumber", build)
    except (OSError, ValueError):
        pass
    cfg = {
        "version": version,
        "buildNumber": build,
        "loadlist": [
            {"name": "GameProfile", "type": "GameProfile"},
            {"name": "LocalSkin", "type": "Legacy", "checkPNG": False,
             "skin": "LocalSkin/skins/{USERNAME}.png", "model": "auto",
             "cape": "LocalSkin/capes/{USERNAME}.png", "elytra": "LocalSkin/elytras/{USERNAME}.png"},
        ],
        "enableTransparentSkin": True,
        "forceLoadAllTextures": True,
        "enableCape": True,
        "threadPoolSize": 8,
        "enableLogStdOut": False,
        "cacheExpiry": 30,
        "forceUpdateSkull": False,
        "enableLocalProfileCache": False,
        "enableCacheAutoClean": False,
        "forceDisableCache": False,
    }
    _write_bytes_atomic(path, (json.dumps(cfg, indent=2) + "\n").encode("utf-8"))


def _norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _mod_present(instance_name: str, needle: str) -> bool:
    mods = instance_mods_dir(instance_name)
    if not mods.is_dir():
        return False
    n = _norm_name(needle)
    return any(p.is_file() and p.suffix.lower() == ".jar" and n in _norm_name(p.name) for p in mods.iterdir())


def _csl_installed(instance_name: str) -> bool:
    return _mod_present(instance_name, "customskinloader")
