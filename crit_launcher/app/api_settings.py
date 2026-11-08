"""JS-bridge API: settings."""

from ..config.settings import (
    ADDON_SOURCES,
    ADDON_VIEWS,
    CATALOG_SOURCES,
    COLOR_MODES,
    THEMES,
    UI_STYLES,
    _clean_hex,
    settings_store,
)
from ..core.logs import log_exception
from ..core.memory import clamp_memory_mb
from ..minecraft.java import _JAVA_UI_CACHE


class SettingsApi:
    """Saving launcher settings."""

    def save_settings(self, patch: dict):
        try:
            def _apply(settings):
                if "memory_mb" in patch:
                    try:
                        mb = int(patch["memory_mb"])
                        settings["memory_mb"] = clamp_memory_mb(mb)
                    except (TypeError, ValueError):
                        pass
                if "java_path" in patch:
                    settings["java_path"] = (patch["java_path"] or "").strip() or None
                if "min_memory_mb" in patch:
                    try:
                        mm = int(patch["min_memory_mb"] or 0)
                        settings["min_memory_mb"] = 0 if mm < 256 else min(mm, 32768)
                    except (TypeError, ValueError):
                        pass
                if "jvm_args" in patch:
                    settings["jvm_args"] = str(patch["jvm_args"] or "").strip()[:600]
                for key in ("game_width", "game_height"):
                    if key in patch:
                        try:
                            v = int(patch[key] or 0)
                            settings[key] = 0 if v < 1 else max(240, min(v, 7680))
                        except (TypeError, ValueError):
                            pass
                if "fullscreen" in patch:
                    settings["fullscreen"] = bool(patch["fullscreen"])
                if "custom_accent" in patch and _clean_hex(patch["custom_accent"], None):
                    settings["custom_accent"] = _clean_hex(patch["custom_accent"])
                if isinstance(patch.get("recent_accents"), list):
                    settings["recent_accents"] = [_clean_hex(x) for x in patch["recent_accents"]
                                                  if _clean_hex(x, None)][:10]
                if "discord_presence" in patch:
                    settings["discord_presence"] = bool(patch["discord_presence"])
                if "reduce_motion" in patch:
                    settings["reduce_motion"] = bool(patch["reduce_motion"])
                if "bg_pattern" in patch:
                    settings["bg_pattern"] = bool(patch["bg_pattern"])
                if "sidebar_collapsed" in patch:
                    settings["sidebar_collapsed"] = bool(patch["sidebar_collapsed"])
                if patch.get("theme") in THEMES:
                    settings["theme"] = patch["theme"]
                if patch.get("ui_style") in UI_STYLES:
                    settings["ui_style"] = patch["ui_style"]
                if patch.get("addon_view") in ADDON_VIEWS:
                    settings["addon_view"] = patch["addon_view"]
                if "addon_instance" in patch:
                    ai = patch["addon_instance"]
                    settings["addon_instance"] = ai.strip()[:200] if isinstance(ai, str) and ai.strip() else None
                if patch.get("color_mode") in COLOR_MODES:
                    settings["color_mode"] = patch["color_mode"]
                if patch.get("catalog_source") in CATALOG_SOURCES:
                    settings["catalog_source"] = patch["catalog_source"]
                if patch.get("addon_source") in ADDON_SOURCES:
                    settings["addon_source"] = patch["addon_source"]
                if "agreement_accepted" in patch:
                    settings["agreement_accepted"] = bool(patch["agreement_accepted"])
                if "agreement_version" in patch:
                    settings["agreement_version"] = max(0, min(2, int(patch["agreement_version"])))
                if "server_beta_seen" in patch:
                    settings["server_beta_seen"] = bool(patch["server_beta_seen"])
                if isinstance(patch.get("local_skin_warning_seen"), list):
                    settings["local_skin_warning_seen"] = [str(x) for x in patch["local_skin_warning_seen"]][:200]
            settings = settings_store.update(_apply)
            _JAVA_UI_CACHE["at"] = 0.0
            self.push_state()
            self._presence_sync()
            return {"success": True, "settings": settings}
        except Exception:
            log_exception("save_settings")
            return {"success": False, "error": "Failed to save settings."}
