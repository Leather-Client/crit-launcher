"""Horizon on-disk layout and asset naming."""

import json
from pathlib import Path

from ..config.paths import HORIZON_DATA_DIR
from ..core.system import current_arch, current_os_name
from ..instances.store import _INVALID_NAME_RE
from .constants import HORIZON_MC_VERSION


def horizon_asset_name():
    system, arch = current_os_name(), current_arch()
    if system == "windows" and arch == "64":
        return "horizon-windows-x64.zip"
    if system == "osx" and arch == "arm64":
        return "horizon-macos-arm64.zip"
    if system == "linux" and arch == "64":
        return "horizon-linux-x86_64.tar.gz"
    return None


def horizon_exe(base: str) -> str:
    return base + (".exe" if current_os_name() == "windows" else "")


def horizon_versions_dir() -> Path:
    return HORIZON_DATA_DIR / "versions"


def horizon_version_dir(version: str) -> Path:
    safe = _INVALID_NAME_RE.sub("_", str(version or ""))[:120] or "build"
    return horizon_versions_dir() / safe


def horizon_minecraft_dir() -> Path:
    return HORIZON_DATA_DIR / "minecraft"


def horizon_minecraft_version(vdir: Path) -> str:
    """The Minecraft version a Horizon build was made for (research-lock.json in its folder)."""
    try:
        lock = json.loads((Path(vdir) / "research-lock.json").read_text(encoding="utf-8"))
        return str(((lock.get("minecraft") or {}).get("version")) or HORIZON_MC_VERSION)
    except (OSError, ValueError):
        return HORIZON_MC_VERSION


def horizon_resources_dir(vdir: Path) -> Path:
    return Path(vdir) / ".local" / "horizon-client" / horizon_minecraft_version(vdir)
