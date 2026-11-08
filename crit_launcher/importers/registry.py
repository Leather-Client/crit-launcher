"""Scanner registry and import source resolution."""

from pathlib import Path

from ..core.logs import log_exception
from .common import LAUNCHER_LABELS, _launcher_roots
from .fastclient import FASTCLIENT_ROOT_SOURCE_ID, _scan_fastclient
from .modrinth_app import _scan_modrinth
from .prism import _prism_game_dir, _scan_prismlauncher
from .sklauncher import _scan_sklauncher, _sk_game_dir


_LAUNCHER_SCANNERS = {
    "prismlauncher": _scan_prismlauncher,
    "polymc": _scan_prismlauncher,
    "sklauncher": _scan_sklauncher,
    "fastclient": _scan_fastclient,
    "modrinth": _scan_modrinth,
}


def scan_all_launchers() -> list:
    sources = []
    for launcher, scanner in _LAUNCHER_SCANNERS.items():
        for root in _launcher_roots(launcher):
            if not root.is_dir():
                continue
            try:
                instances = scanner(root)
            except Exception:
                log_exception(f"scanning {launcher} at {root}")
                continue
            if instances:
                sources.append({
                    "launcher": launcher, "label": LAUNCHER_LABELS[launcher],
                    "root": str(root), "instances": instances,
                })
            break
    return sources


def _guess_launcher_for_dir(path: Path):
    name = path.name.lower()
    for key, hints in (("modrinth", ("modrinth", "theseus")), ("sklauncher", ("sklauncher",)),
                       ("fastclient", ("fastclient", "fast client")), ("polymc", ("polymc",)),
                       ("prismlauncher", ("prism",))):
        if any(h in name for h in hints):
            return key
    for key, scanner in _LAUNCHER_SCANNERS.items():
        try:
            if scanner(path):
                return key
        except Exception:
            continue
    return None


def scan_custom_launcher_dir(launcher: str, root: Path) -> dict:
    scanner = _LAUNCHER_SCANNERS.get(launcher)
    if not scanner:
        raise ValueError("Unknown launcher type.")
    return {"launcher": launcher, "label": LAUNCHER_LABELS[launcher], "root": str(root),
            "instances": scanner(root), "custom": True}


def _find_scanned_instance(launcher: str, source_id: str, custom_root=None):
    if custom_root:
        root = Path(custom_root)
        scanner = _LAUNCHER_SCANNERS.get(launcher)
        if not scanner or not root.is_dir():
            return None, None
        for inst in scanner(root):
            if inst["source_id"] == source_id:
                return inst, root
        return None, None
    for root in _launcher_roots(launcher):
        if not root.is_dir():
            continue
        scanner = _LAUNCHER_SCANNERS.get(launcher)
        if not scanner:
            return None, None
        for inst in scanner(root):
            if inst["source_id"] == source_id:
                return inst, root
        return None, None
    return None, None


def _source_paths(launcher: str, source_id: str, root: Path):
    if launcher in ("prismlauncher", "polymc"):
        game_dir = _prism_game_dir(root / "instances" / source_id)
        return (game_dir / "options.txt", game_dir / "mods",
                game_dir / "resourcepacks", game_dir / "saves", game_dir / "config",
                game_dir / "shaderpacks", game_dir / "schematics", game_dir / "screenshots")
    if launcher == "sklauncher":
        game_dir = _sk_game_dir(root / "instances" / source_id) or (root / "instances" / source_id)
        return (game_dir / "options.txt", game_dir / "mods",
                game_dir / "resourcepacks", game_dir / "saves", game_dir / "config",
                game_dir / "shaderpacks", game_dir / "schematics", game_dir / "screenshots")
    if launcher == "modrinth":
        profile_dir = root / "profiles" / source_id
        return (profile_dir / "options.txt", profile_dir / "mods",
                profile_dir / "resourcepacks", profile_dir / "saves", profile_dir / "config",
                profile_dir / "shaderpacks", profile_dir / "schematics", profile_dir / "screenshots")
    if launcher == "fastclient":
        profile_dir = root if source_id == FASTCLIENT_ROOT_SOURCE_ID else root / "profiles" / source_id
        return (profile_dir / "options.txt", profile_dir / "mods",
                profile_dir / "resourcepacks", profile_dir / "saves", profile_dir / "config",
                profile_dir / "shaderpacks", profile_dir / "schematics", profile_dir / "screenshots")
    return None, None, None, None, None, None, None, None
