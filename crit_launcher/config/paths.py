"""Where the app lives and where its data lives; every path in the launcher comes from here."""

import os
import sys
from pathlib import Path


# crit_launcher/config/paths.py -> crit_launcher/config -> crit_launcher -> <project root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The script `python crit.py` runs; shortcuts created from a source checkout point at it.
ENTRY_SCRIPT = PROJECT_ROOT / "crit.py"


def _resource_dir() -> Path:
    """Folder holding index.html and assets/ (the PyInstaller bundle when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return PROJECT_ROOT


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


RESOURCE_DIR = _resource_dir()
APP_DIR = _app_dir()

IS_LINUX = sys.platform.startswith("linux")


def _default_data_dir() -> Path:
    """Windows keeps data beside the exe. On Linux the app often lives in a read-only
    AppImage mount, so data goes to the XDG data directory instead."""
    if IS_LINUX:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / "crit-launcher"
    return APP_DIR / "launcher_data"


DATA_DIR = Path(os.environ.get("CRIT_LAUNCHER_DATA", str(_default_data_dir())))
INSTANCES_DIR = DATA_DIR / "instances"
HORIZON_DATA_DIR = DATA_DIR / "horizon"
VERSIONS_DIR = DATA_DIR / "versions"
LIBRARIES_DIR = DATA_DIR / "libraries"
ASSETS_DIR = DATA_DIR / "assets"
ASSET_OBJECTS_DIR = ASSETS_DIR / "objects"
ASSET_INDEXES_DIR = ASSETS_DIR / "indexes"
RUNTIME_DIR = DATA_DIR / "runtime"
TMP_DIR = DATA_DIR / "tmp"
SKINS_DIR = DATA_DIR / "skins"
INSTANCE_ICONS_DIR = DATA_DIR / "instance_icons"
SERVERS_DIR = DATA_DIR / "servers"

# Compiled builds unpack index.html into DATA_DIR/web so assets/ can sit beside it.
PAGE_DIR = DATA_DIR / "web" if getattr(sys, "frozen", False) else RESOURCE_DIR

ACCOUNTS_FILE = DATA_DIR / "accounts.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "launcher.log"


def ensure_data_dirs() -> None:
    """Create the data layout. Called once from bootstrap.init(); importing this module has no side effects."""
    for _d in (DATA_DIR, INSTANCES_DIR, VERSIONS_DIR, LIBRARIES_DIR, ASSETS_DIR,
               ASSET_OBJECTS_DIR, ASSET_INDEXES_DIR, RUNTIME_DIR, TMP_DIR, SKINS_DIR,
               INSTANCE_ICONS_DIR, PAGE_DIR, SERVERS_DIR):
        _d.mkdir(parents=True, exist_ok=True)
