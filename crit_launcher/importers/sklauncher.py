"""SKLauncher instance scanner."""

import re
from pathlib import Path

from ..minecraft.loaders import MOD_LOADERS
from .common import _count_dir, _count_images
from .fastclient import _parse_fastclient_log_line, _read_first_log_line


_SK_LOG_VERSION_RES = (
    re.compile(r"Minecraft Version:\s*(\d[\w.\-]*)"),
    re.compile(r"Loading Minecraft (\d[\w.\-]*)"),
    re.compile(r"--version,?\s*(\d[\w.\-]*)"),
    re.compile(r"Client\s+(\d+\.\d+(?:\.\d+)?)\b"),
)


def _sk_vanilla_version_from_log(logs_dir: Path):
    log_path = logs_dir / "latest.log"
    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 80:
                    break
                for rx in _SK_LOG_VERSION_RES:
                    m = rx.search(line)
                    if m:
                        return m.group(1)
    except OSError:
        pass
    return None


def _sk_game_dir(sub: Path):
    for cand in (sub, sub / ".minecraft", sub / "minecraft"):
        if (cand / "logs" / "latest.log").is_file():
            return cand
    return None


def _scan_sklauncher(root: Path) -> list:
    instances_dir = root / "instances"
    if not instances_dir.is_dir():
        return []
    found = []
    for sub in sorted(instances_dir.iterdir(), key=lambda p: p.name.lower()):
        if not sub.is_dir():
            continue
        game_dir = _sk_game_dir(sub)
        if not game_dir:
            continue
        logs_dir = game_dir / "logs"
        log_info = _parse_fastclient_log_line(_read_first_log_line(logs_dir))
        if log_info:
            loader, mc_version, loader_version = log_info
            if not mc_version:
                mc_version = _sk_vanilla_version_from_log(logs_dir)
        else:
            loader, loader_version = "vanilla", None
            mc_version = _sk_vanilla_version_from_log(logs_dir)
        found.append({
            "source_id": sub.name,
            "name": sub.name,
            "version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "has_options": (game_dir / "options.txt").is_file(),
            "mods_count": _count_dir(game_dir / "mods", ".jar") if loader in MOD_LOADERS else 0,
            "resourcepacks_count": _count_dir(game_dir / "resourcepacks"),
            "saves_count": _count_dir(game_dir / "saves"),
            "shaderpacks_count": _count_dir(game_dir / "shaderpacks"),
            "schematics_count": _count_dir(game_dir / "schematics"),
            "screenshots_count": _count_images(game_dir / "screenshots"),
            "config_count": _count_dir(game_dir / "config") if loader in MOD_LOADERS else 0,
        })
    return found
