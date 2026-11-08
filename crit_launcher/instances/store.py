"""Instance folders and instance.json metadata."""

import json
import os
import re
from pathlib import Path

from ..config.paths import INSTANCES_DIR
from ..minecraft.constants import INSTALL_SCHEMA
from ..minecraft.loaders import MOD_LOADERS
from ..minecraft.meta_cache import _meta_lock


_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_instance_name(name: str) -> str:
    name = (name or "").strip().rstrip(".")
    if not name:
        raise ValueError("Instance name cannot be empty.")
    if len(name) > 64:
        raise ValueError("Instance name is too long.")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError("Instance name contains invalid characters.")
    if os.path.isabs(name):
        raise ValueError("Instance name cannot be an absolute path.")
    if _INVALID_NAME_RE.search(name):
        raise ValueError('Instance name cannot contain: < > : " / \\ | ? *')
    return name


def instance_dir(name: str) -> Path:
    safe = sanitize_instance_name(name)
    return INSTANCES_DIR / safe


def read_instance_meta(name: str) -> dict | None:
    meta_path = instance_dir(name) / "instance.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_instance_meta(name: str, meta: dict):
    path = instance_dir(name) / "instance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _meta_lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def update_instance_meta(name: str, mutator):
    with _meta_lock:
        meta = read_instance_meta(name)
        if meta is None:
            return None
        mutator(meta)
        write_instance_meta(name, meta)
        return meta


def is_installed(meta: dict) -> bool:
    return bool(meta.get("installed")) and meta.get("schema") == INSTALL_SCHEMA


def list_instances() -> list:
    result = []
    if not INSTANCES_DIR.exists():
        return result
    for child in sorted(INSTANCES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir():
            try:
                meta = read_instance_meta(child.name)
            except ValueError:
                continue
            if meta:
                result.append(meta)
    result.sort(key=lambda m: m["name"].lower())
    return result


def instance_game_dir(name: str) -> Path:
    return instance_dir(name) / "game"


def instance_mods_dir(name: str) -> Path:
    return instance_game_dir(name) / "mods"


def instance_config_dir(name: str) -> Path:
    return instance_game_dir(name) / "config"


def ensure_game_layout(name: str, loader: str):
    game = instance_game_dir(name)
    folders = ["resourcepacks", "shaderpacks", "saves"]
    if loader in MOD_LOADERS:
        folders += ["mods", "config"]
    for f in folders:
        (game / f).mkdir(parents=True, exist_ok=True)


def unique_instance_name(base: str) -> str:
    cleaned = _INVALID_NAME_RE.sub("-", base or "").replace("..", "-").strip().rstrip(".")
    cleaned = (cleaned or "Modpack")[:56]
    candidate = cleaned
    n = 2
    while instance_dir(candidate).exists():
        candidate = f"{cleaned} ({n})"
        n += 1
    return candidate
