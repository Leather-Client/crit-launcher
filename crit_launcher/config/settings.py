"""Launcher settings: allowed values, defaults and the settings file store."""

import re

from ..core.jsonstore import JsonStore
from .paths import SETTINGS_FILE


THEMES = ("green", "blue", "purple", "red", "orange", "pink", "custom")
UI_STYLES = ("default", "minecraft", "modrinth", "fastclient", "sklauncher")
ADDON_VIEWS = ("browse", "installed")
ADDON_SOURCES = ("modrinth", "curseforge")
COLOR_MODES = ("dark", "light", "oled")
CATALOG_SOURCES = ("labymod", "critical")
DEFAULT_SETTINGS = {
    "active_account": None,
    "active_instance": None,
    "memory_mb": 2048,
    "min_memory_mb": 0,
    "java_path": None,
    "jvm_args": "",
    "game_width": 0,
    "game_height": 0,
    "fullscreen": False,
    "custom_accent": "#4ade80",
    "recent_accents": [],
    "reduce_motion": False,
    "discord_presence": False,
    "bg_pattern": True,
    "sidebar_collapsed": False,
    "theme": "green",
    "ui_style": "default",
    "addon_view": "browse",
    "addon_instance": None,
    "color_mode": "dark",
    "catalog_source": "labymod",
    "addon_source": "modrinth",
    "agreement_accepted": False,
    "agreement_version": 0,
    "server_beta_seen": False,
    "local_skin_warning_seen": [],
    "playtime_seconds": 0,
    "playtime_days": {},
}
settings_store = JsonStore(SETTINGS_FILE, DEFAULT_SETTINGS)


def _clean_hex(value, default="#4ade80"):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        return value.strip().lower()
    return default
