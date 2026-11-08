"""Mod loader names and NeoForge version helpers."""

from .constants import NEOFORGE_LEGACY_MAVEN, NEOFORGE_LEGACY_MC, NEOFORGE_MAVEN


MOD_LOADERS = ("fabric", "forge", "neoforge")
LOADER_NICE = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "horizon": "Horizon"}


def neoforge_version_to_mc(version: str):
    if "+snapshot" in version:
        return None
    parts = version.split("-")[0].split(".")
    if len(parts) < 3 or not all(p.isdigit() for p in parts):
        return None
    major = int(parts[0])
    if major >= 26:
        return f"{parts[0]}.{parts[1]}" if parts[2] == "0" else f"{parts[0]}.{parts[1]}.{parts[2]}"
    if major < 20:
        return None
    return f"1.{major}" if parts[1] == "0" else f"1.{major}.{parts[1]}"


def neoforge_installer_info(mc_version: str, loader_version: str):
    if mc_version == NEOFORGE_LEGACY_MC:
        full = f"{mc_version}-{loader_version}"
        return full, f"{NEOFORGE_LEGACY_MAVEN}{full}/forge-{full}-installer.jar"
    return loader_version, f"{NEOFORGE_MAVEN}{loader_version}/neoforge-{loader_version}-installer.jar"
