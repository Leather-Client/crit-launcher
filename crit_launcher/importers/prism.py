"""Prism / MultiMC instance scanner."""

from pathlib import Path

from ..minecraft.loaders import MOD_LOADERS
from .common import _count_dir, _count_images, _loader_from_mmc_pack, _read_json_safe


def _prism_game_dir(instance_dir: Path) -> Path:
    plain = instance_dir / "minecraft"
    dotted = instance_dir / ".minecraft"
    if plain.is_dir():
        return plain
    if dotted.is_dir():
        return dotted
    return plain


def _scan_prismlauncher(root: Path) -> list:
    instances_dir = root / "instances"
    if not instances_dir.is_dir():
        return []
    found = []
    for sub in sorted(instances_dir.iterdir(), key=lambda p: p.name.lower()):
        pack = _read_json_safe(sub / "mmc-pack.json") if sub.is_dir() else None
        if not pack:
            continue
        mc_version, loader, loader_version = _loader_from_mmc_pack(pack.get("components"))
        game_dir = _prism_game_dir(sub)
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
