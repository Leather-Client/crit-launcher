"""JS-bridge API: server settings."""

from ..core.logs import log_exception
from .meta import read_server_meta, server_dir, write_server_meta
from .properties import _clamp_int, read_properties, write_properties


class ServerSettingsApi:
    """server.properties and gamerules."""

    _SRV_BOOL_SETTINGS = {
        "offline_mode": None, "white_list": ("white-list", False), "pvp": ("pvp", True),
        "allow_flight": ("allow-flight", False), "allow_nether": ("allow-nether", True),
        "enable_command_block": ("enable-command-block", False),
        "spawn_monsters": ("spawn-monsters", True), "spawn_animals": ("spawn-animals", True),
        "spawn_npcs": ("spawn-npcs", True),
    }

    _SRV_INT_SETTINGS = {
        "port": ("server-port", 1024, 65535, 25565), "max_players": ("max-players", 1, 1000, 20),
        "view_distance": ("view-distance", 2, 32, 10), "simulation_distance": ("simulation-distance", 2, 32, 10),
        "spawn_protection": ("spawn-protection", 0, 1000, 16),
    }

    def get_server_settings(self, name):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            props = read_properties(server_dir(name) / "server.properties")
            s = {"motd": props.get("motd", "A Crit Launcher server"),
                 "memory_mb": _clamp_int(meta.get("memory_mb"), 512, 65536, 2048),
                 "gamemode": props.get("gamemode", "survival"), "difficulty": props.get("difficulty", "easy"),
                 "offline_mode": props.get("online-mode", "false").strip().lower() != "true"}
            for key, spec in self._SRV_BOOL_SETTINGS.items():
                if spec:
                    s[key] = props.get(spec[0], "true" if spec[1] else "false").strip().lower() == "true"
            for key, (prop, lo, hi, default) in self._SRV_INT_SETTINGS.items():
                s[key] = _clamp_int(props.get(prop), lo, hi, default)
            return {"success": True, "settings": s, "status": self._srv_status(name)}
        except Exception:
            log_exception("get_server_settings")
            return {"success": False, "error": "Couldn't read the server settings."}

    def update_server_settings(self, name, settings):
        try:
            meta = read_server_meta(name)
            if not meta or not meta.get("installed"):
                return {"success": False, "error": "This server isn't installed yet."}
            st = dict(settings or {})
            props = {}
            if "motd" in st:
                props["motd"] = str(st["motd"] or "A Crit Launcher server")[:120]
            if st.get("gamemode") in ("survival", "creative", "adventure", "spectator"):
                props["gamemode"] = st["gamemode"]
            if st.get("difficulty") in ("peaceful", "easy", "normal", "hard"):
                props["difficulty"] = st["difficulty"]
            if "offline_mode" in st:
                props["online-mode"] = not bool(st["offline_mode"])
            for key, spec in self._SRV_BOOL_SETTINGS.items():
                if spec and key in st:
                    props[spec[0]] = bool(st[key])
            for key, (prop, lo, hi, default) in self._SRV_INT_SETTINGS.items():
                if key in st:
                    props[prop] = _clamp_int(st[key], lo, hi, default)
            write_properties(server_dir(name) / "server.properties", props)
            if "memory_mb" in st:
                meta["memory_mb"] = _clamp_int(st["memory_mb"], 512, 65536, 2048)
            if "server-port" in props:
                meta["port"] = props["server-port"]
            write_server_meta(name, meta)
            self._srv_push()
            return {"success": True}
        except Exception as exc:
            log_exception("update_server_settings")
            return {"success": False, "error": f"Couldn't save the settings: {exc}"}
