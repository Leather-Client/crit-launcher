"""Modrinth App instance scanner."""

from pathlib import Path

from ..minecraft.loaders import MOD_LOADERS
from .common import _count_dir, _count_images
from .fastclient import _parse_fastclient_log_line, _read_first_log_line
from .sklauncher import _sk_vanilla_version_from_log


def _scan_modrinth(root: Path) -> list:
    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        return []
    found = []
    for sub in sorted(profiles_dir.iterdir(), key=lambda p: p.name.lower()):
        if not sub.is_dir():
            continue
        logs_dir = sub / "logs"
        has_log = (logs_dir / "latest.log").is_file()
        log_info = _parse_fastclient_log_line(_read_first_log_line(logs_dir)) if has_log else None
        if log_info:
            loader, mc_version, loader_version = log_info
            if not mc_version:
                mc_version = _sk_vanilla_version_from_log(logs_dir)
        elif has_log:
            loader, loader_version = "vanilla", None
            mc_version = _sk_vanilla_version_from_log(logs_dir)
        else:
            loader, loader_version, mc_version = "unknown", None, None
        found.append({
            "source_id": sub.name,
            "name": sub.name,
            "version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "has_options": (sub / "options.txt").is_file(),
            "mods_count": _count_dir(sub / "mods", ".jar") if loader in MOD_LOADERS else 0,
            "resourcepacks_count": _count_dir(sub / "resourcepacks"),
            "saves_count": _count_dir(sub / "saves"),
            "shaderpacks_count": _count_dir(sub / "shaderpacks"),
            "schematics_count": _count_dir(sub / "schematics"),
            "screenshots_count": _count_images(sub / "screenshots"),
            "config_count": _count_dir(sub / "config") if loader in MOD_LOADERS else 0,
        })
    return found
