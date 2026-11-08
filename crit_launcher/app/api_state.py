"""JS-bridge API: state snapshots and window events."""

import json

from ..accounts.store import accounts_store, find_account
from ..accounts.tokens import public_account
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
from ..core.memory import memory_ceiling_mb, total_system_memory_mb
from ..instances.icons import INSTANCE_ICONS, custom_instance_icon_path, list_custom_instance_icons
from ..instances.playtime import _playtime_days
from ..instances.store import is_installed, list_instances
from ..minecraft.java import detect_java_for_ui
from ..version import APP_VERSION


class AppStateApi:
    """Window handle, UI events and the state snapshots the page polls."""

    def set_window(self, window):
        self._window = window

    def emit(self, name: str, data=None):
        if self._window is None:
            return
        try:
            payload = json.dumps(data if data is not None else {})
            name_json = json.dumps(name)
            code = f"window.__critEvent && window.__critEvent({name_json}, {payload});"
            self._window.evaluate_js(code)
        except Exception:
            log_exception(f"emit({name}) failed")

    def push_state(self):
        self.emit("stateChanged", self._state_dict())

    def _runtime_dict(self) -> dict:
        with self._proc_lock:
            running = list(self._processes.keys())
            sessions = dict(self._sessions)
            installing = list(self._installing)
        return {
            "running": running,
            "sessions": sessions,
            "installing": installing,
            "playtime_total": int(settings_store.read().get("playtime_seconds", 0)),
            "playtime_days": _playtime_days(),
        }

    def _state_dict(self) -> dict:
        settings = settings_store.read()
        accounts = accounts_store.read()
        active_account = find_account(accounts, settings.get("active_account"))
        if not active_account and accounts:
            active_account = accounts[0]

        instances = []
        for meta in list_instances():
            icon_key = meta.get("icon")
            custom_icon_path = custom_instance_icon_path(icon_key)
            if icon_key in INSTANCE_ICONS:
                instance_icon = "critclient" if icon_key == "oneclient" else icon_key
            elif custom_icon_path and custom_icon_path.is_file():
                instance_icon = icon_key
            else:
                instance_icon = None
            instances.append({
                "name": meta.get("name"),
                "version": meta.get("version"),
                "loader": meta.get("loader", "vanilla"),
                "loader_version": meta.get("loader_version"),
                "installed": is_installed(meta),
                "playtime": int(meta.get("playtime", 0)),
                "icon": instance_icon,
                "created": meta.get("created"),
                "critclient": bool(meta.get("critclient") or meta.get("oneclient")),
            })
        active_instance_name = settings.get("active_instance")
        active_instance = next(
            (i for i in instances if i["name"] == active_instance_name), None)
        if not active_instance and instances:
            active_instance = instances[0]

        java_path, java_line = detect_java_for_ui()
        runtime = self._runtime_dict()

        return {
            "version": APP_VERSION,
            "accounts": [public_account(a) for a in accounts],
            "active_account": public_account(active_account) if active_account else None,
            "instances": instances,
            "custom_icons": list_custom_instance_icons(),
            "active_instance": active_instance,
            "running": runtime["running"],
            "sessions": runtime["sessions"],
            "installing": runtime["installing"],
            "playtime_total": runtime["playtime_total"],
            "playtime_days": runtime["playtime_days"],
            "memory_total_mb": total_system_memory_mb(),
            "memory_ceiling_mb": memory_ceiling_mb(),
            "settings": {
                "memory_mb": int(settings.get("memory_mb", 2048)),
                "min_memory_mb": int(settings.get("min_memory_mb") or 0),
                "java_path": settings.get("java_path"),
                "jvm_args": settings.get("jvm_args") if isinstance(settings.get("jvm_args"), str) else "",
                "game_width": int(settings.get("game_width") or 0),
                "game_height": int(settings.get("game_height") or 0),
                "fullscreen": bool(settings.get("fullscreen", False)),
                "custom_accent": _clean_hex(settings.get("custom_accent")),
                "recent_accents": [_clean_hex(x) for x in (settings.get("recent_accents") or []) if _clean_hex(x, None)][:10],
                "reduce_motion": bool(settings.get("reduce_motion", False)),
                "discord_presence": bool(settings.get("discord_presence", False)),
                "bg_pattern": bool(settings.get("bg_pattern", True)),
                "sidebar_collapsed": bool(settings.get("sidebar_collapsed", False)),
                "theme": settings.get("theme", "green") if settings.get("theme") in THEMES else "green",
                "ui_style": settings.get("ui_style", "default") if settings.get("ui_style") in UI_STYLES else "default",
                "addon_view": settings.get("addon_view") if settings.get("addon_view") in ADDON_VIEWS else "browse",
                "addon_instance": settings.get("addon_instance") if isinstance(settings.get("addon_instance"), str) else None,
                "color_mode": settings.get("color_mode", "dark") if settings.get("color_mode") in COLOR_MODES else "dark",
                "catalog_source": settings.get("catalog_source", "labymod") if settings.get("catalog_source") in CATALOG_SOURCES else "labymod",
                "addon_source": settings.get("addon_source") if settings.get("addon_source") in ADDON_SOURCES else "modrinth",
                "agreement_accepted": bool(settings.get("agreement_accepted", False)),
                "agreement_version": settings.get("agreement_version", 0)
                if isinstance(settings.get("agreement_version", 0), int) else 0,
                "server_beta_seen": bool(settings.get("server_beta_seen", False)),
                "local_skin_warning_seen": [x for x in (settings.get("local_skin_warning_seen") or [])
                                            if isinstance(x, str)],
            },
            "java_ok": java_path is not None,
            "java_version": java_line,
        }

    def get_state(self):
        try:
            return {"success": True, "state": self._state_dict()}
        except Exception as exc:
            log_exception("get_state")
            return {"success": False, "error": str(exc)}

    def get_runtime(self):
        try:
            data = self._runtime_dict()
            data["success"] = True
            return data
        except Exception as exc:
            log_exception("get_runtime")
            return {"success": False, "error": str(exc)}

    def quit_app(self):
        """Close Crit Launcher (used when the first-run agreement is declined)."""
        try:
            if self._window is not None:
                self._window.destroy()
            return {"success": True}
        except Exception:
            log_exception("quit_app")
            return {"success": False, "error": "Couldn't close the launcher."}

    def open_url(self, url):
        try:
            import webbrowser
            if not isinstance(url, str) or not url.startswith(("https://", "http://", "mailto:")):
                return {"success": False}
            webbrowser.open(url)
            return {"success": True}
        except Exception:
            log_exception("open_url")
            return {"success": False}
