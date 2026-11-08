"""server.properties reading, writing and generation."""

import re
from pathlib import Path

from .constants import LEGACY_WORLD_TYPES, WORLD_TYPES, _GAMERULE_IDS
from .meta import mc_tuple


def _prop_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).replace("\r", " ").replace("\n", " ")
    return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in s)


def read_properties(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v
    except OSError:
        pass
    return out


def write_properties(path: Path, updates: dict):
    lines, seen = [], set()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line and not line.startswith("#") else None
            if key in updates:
                lines.append(f"{key}={_prop_value(updates[key])}")
                seen.add(key)
            else:
                lines.append(line)
    else:
        lines.append("#Minecraft server properties (written by Crit Launcher)")
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={_prop_value(v)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clamp_int(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def build_server_properties(cfg: dict, version: str) -> dict:
    wt = cfg.get("level_type") if cfg.get("level_type") in WORLD_TYPES else "minecraft:normal"
    if mc_tuple(version) < (1, 16, 0):
        wt = LEGACY_WORLD_TYPES[wt]
    gm = cfg.get("gamemode") if cfg.get("gamemode") in ("survival", "creative", "adventure", "spectator") else "survival"
    diff = cfg.get("difficulty") if cfg.get("difficulty") in ("peaceful", "easy", "normal", "hard") else "easy"
    hardcore = bool(cfg.get("hardcore"))
    props = {
        "motd": (cfg.get("motd") or "A Crit Launcher server")[:120],
        "server-port": _clamp_int(cfg.get("port"), 1024, 65535, 25565),
        "max-players": _clamp_int(cfg.get("max_players"), 1, 1000, 20),
        "gamemode": gm,
        "difficulty": "hard" if hardcore else diff,
        "hardcore": hardcore,
        "pvp": bool(cfg.get("pvp", True)),
        "online-mode": bool(cfg.get("online_mode", False)),
        "allow-flight": bool(cfg.get("allow_flight", False)),
        "allow-nether": bool(cfg.get("allow_nether", True)),
        "spawn-monsters": bool(cfg.get("spawn_monsters", True)),
        "spawn-animals": bool(cfg.get("spawn_animals", True)),
        "spawn-npcs": bool(cfg.get("spawn_npcs", True)),
        "enable-command-block": bool(cfg.get("enable_command_block", False)),
        "generate-structures": bool(cfg.get("generate_structures", True)),
        "white-list": bool(cfg.get("white_list", False)),
        "view-distance": _clamp_int(cfg.get("view_distance"), 2, 32, 10),
        "simulation-distance": _clamp_int(cfg.get("simulation_distance"), 2, 32, 10),
        "spawn-protection": _clamp_int(cfg.get("spawn_protection"), 0, 1000, 16),
        "level-name": re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (cfg.get("level_name") or "world")).strip() or "world",
        "level-seed": (cfg.get("level_seed") or "").strip()[:64],
        "level-type": wt,
    }
    return props


def gamerule_candidates(camel: str, version: str) -> list:
    g = _GAMERULE_IDS.get(camel)
    if not g:
        return []
    names = [camel] + list(g[4])
    if mc_tuple(version) >= (1, 21, 11):
        names = list(g[4]) + [camel]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out
