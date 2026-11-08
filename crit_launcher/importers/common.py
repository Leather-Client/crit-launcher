"""Shared helpers for reading other launchers' data."""

import json
import os
import re
from pathlib import Path

from ..core.system import current_os_name


def _home_dir() -> Path:
    return Path.home()


def _windows_appdata() -> Path:
    env = os.environ.get("APPDATA")
    return Path(env) if env else _home_dir() / "AppData" / "Roaming"


def _launcher_roots(launcher: str) -> list:
    system = current_os_name()
    home = _home_dir()
    if launcher == "prismlauncher":
        if system == "windows":
            return [_windows_appdata() / "PrismLauncher"]
        if system == "osx":
            return [home / "Library" / "Application Support" / "PrismLauncher"]
        return [home / ".local" / "share" / "PrismLauncher",
                home / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher"]
    if launcher == "polymc":
        if system == "windows":
            return [_windows_appdata() / "PolyMC"]
        if system == "osx":
            return [home / "Library" / "Application Support" / "PolyMC"]
        return [home / ".local" / "share" / "PolyMC",
                home / ".var" / "app" / "org.polymc.PolyMC" / "data" / "polymc"]
    if launcher == "sklauncher":
        if system == "windows":
            return [_windows_appdata() / ".sklauncher"]
        if system == "osx":
            return [home / "Library" / "Application Support" / ".sklauncher"]
        return [home / ".sklauncher"]
    if launcher == "fastclient":
        if system == "windows":
            return [_windows_appdata() / ".fastclient"]
        if system == "osx":
            return [home / "Library" / "Application Support" / ".fastclient"]
        return [home / ".fastclient"]
    if launcher == "modrinth":
        if system == "windows":
            return [_windows_appdata() / "ModrinthApp",
                    _windows_appdata() / "com.modrinth.theseus"]
        if system == "osx":
            return [home / "Library" / "Application Support" / "ModrinthApp",
                    home / "Library" / "Application Support" / "com.modrinth.theseus"]
        return [home / ".local" / "share" / "ModrinthApp",
                home / ".local" / "share" / "com.modrinth.theseus"]
    return []


LAUNCHER_LABELS = {
    "prismlauncher": "Prism Launcher",
    "polymc": "PolyMC",
    "sklauncher": "SKLauncher",
    "fastclient": "Fast Client",
    "modrinth": "Modrinth App",
}


def _read_json_safe(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _maven_version(name: str, prefix: str) -> str:
    return name[len(prefix):] if name.startswith(prefix) else ""


def _loader_from_version_json(vjson: dict, mc_version: str):
    for lib in vjson.get("libraries", []):
        lname = lib.get("name", "")
        if lname.startswith("net.fabricmc:fabric-loader:"):
            return "fabric", _maven_version(lname, "net.fabricmc:fabric-loader:")
        if lname.startswith("net.minecraftforge:forge:"):
            full = _maven_version(lname, "net.minecraftforge:forge:")
            prefix = mc_version + "-"
            return "forge", (full[len(prefix):] if full.startswith(prefix) else full)
    vid = vjson.get("id", "")
    m = re.match(r"^fabric-loader-([\w.]+)-", vid)
    if m:
        return "fabric", m.group(1)
    m = re.match(r"^(?:[\w.]+-)?forge-([\w.]+)$", vid)
    if m:
        return "forge", m.group(1)
    return "vanilla", None


def _loader_from_mmc_pack(components: list):
    mc_version = None
    loader, loader_version = "vanilla", None
    for c in components or []:
        uid = c.get("uid", "")
        ver = c.get("version") or c.get("cachedVersion")
        if uid == "net.minecraft":
            mc_version = ver
        elif uid == "net.fabricmc.fabric-loader":
            loader, loader_version = "fabric", ver
        elif uid == "net.minecraftforge":
            loader, loader_version = "forge", ver
        elif uid == "org.quiltmc.quilt-loader":
            loader, loader_version = "quilt", ver
        elif uid == "com.neoforged":
            loader, loader_version = "neoforge", ver
    return mc_version, loader, loader_version


def _count_images(path: Path) -> int:
    if not path.is_dir():
        return 0
    try:
        return sum(1 for p in path.iterdir()
                   if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    except OSError:
        return 0


def _count_dir(path: Path, suffix=None) -> int:
    if not path.is_dir():
        return 0
    try:
        if suffix:
            return sum(1 for p in path.iterdir() if p.is_file() and p.suffix.lower() == suffix)
        return sum(1 for p in path.iterdir())
    except OSError:
        return 0
