"""JS-bridge API: server players."""

import json
import re
import time

from ..core.logs import log_exception
from .constants import PLAYER_NAME_RE
from .meta import mc_tuple, read_server_meta, server_dir
from .properties import _clamp_int


class ServerPlayersApi:
    """Online players and moderation actions."""

    def get_server_players(self, name):
        try:
            rt = self._srv_online_rt(name)
            ops = set()
            try:
                ops = {o.get("name") for o in json.loads((server_dir(name) / "ops.json").read_text(encoding="utf-8"))}
            except Exception:
                pass
            return {"success": True, "online": rt is not None,
                    "players": [{"name": p, "op": p in ops} for p in (rt.players if rt else [])]}
        except Exception:
            log_exception("get_server_players")
            return {"success": False, "error": "Couldn't read players."}

    def get_player_info(self, name, player):
        rt = self._srv_online_rt(name)
        if not rt or not PLAYER_NAME_RE.match(player or ""):
            return {"success": False, "error": "Player isn't online."}
        pat = rf"{re.escape(player)} has the following entity data: (.+?)\s*$"
        hp = self._srv_ask(rt, f"data get entity {player} Health", pat)
        gm = self._srv_ask(rt, f"data get entity {player} playerGameType", pat)
        out = {"success": True, "health": None, "gamemode": None}
        try:
            if hp:
                out["health"] = float(re.sub(r"[^\d.\-]", "", hp.group(1)))
            if gm:
                out["gamemode"] = ["survival", "creative", "adventure", "spectator"][int(re.sub(r"\D", "", gm.group(1)))]
        except (ValueError, IndexError):
            pass
        return out

    def player_action(self, name, player, action, value=None):
        try:
            rt = self._srv_online_rt(name)
            if not rt:
                return {"success": False, "error": "Server isn't running."}
            if not PLAYER_NAME_RE.match(player or ""):
                return {"success": False, "error": "Invalid player name."}
            meta = read_server_meta(name) or {}
            version = meta.get("version", "")
            cmds = []
            if action == "gamemode":
                if value not in ("survival", "creative", "adventure", "spectator"):
                    return {"success": False, "error": "Unknown game mode."}
                cmds = [f"gamemode {value} {player}"]
            elif action in ("op", "deop", "kill"):
                cmds = [f"{action} {player}"]
            elif action == "heal":
                cmds = [f"effect give {player} minecraft:instant_health 1 30 true",
                        f"effect give {player} minecraft:saturation 1 30 true"]
            elif action == "feed":
                cmds = [f"effect give {player} minecraft:saturation 1 30 true"]
            elif action == "kick":
                cmds = [f"kick {player} Kicked from Crit Launcher"]
            elif action == "ban":
                cmds = [f"ban {player} Banned from Crit Launcher"]
            elif action == "set_health":
                hp = _clamp_int(value, 1, 1024, 20)
                cmds = [f"effect give {player} minecraft:instant_health 1 30 true"]
                if hp < 20:
                    cmds.append(("DELAY", 0.7))
                    cmds.append(f"damage {player} {20 - hp} minecraft:generic_kill")
            elif action == "max_health":
                mh = _clamp_int(value, 1, 1024, 20)
                attr = "minecraft:max_health" if mc_tuple(version) >= (1, 21, 2) else "minecraft:generic.max_health"
                cmds = [f"attribute {player} {attr} base set {mh}"]
            else:
                return {"success": False, "error": "Unknown action."}
            for c in cmds:
                if isinstance(c, tuple):
                    time.sleep(c[1])
                    continue
                self._srv_line(rt, "> " + c)
                if not self._srv_send(rt, c):
                    return {"success": False, "error": "Couldn't reach the server."}
            return {"success": True}
        except Exception:
            log_exception("player_action")
            return {"success": False, "error": "That action failed."}
