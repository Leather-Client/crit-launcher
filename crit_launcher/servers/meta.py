"""Server folders and crit_server.json."""

import json
import os
import re
from pathlib import Path

from ..config.paths import SERVERS_DIR
from .constants import SERVER_META_FILE


def server_dir(name: str) -> Path:
    return SERVERS_DIR / name


def read_server_meta(name: str):
    try:
        return json.loads((server_dir(name) / SERVER_META_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_server_meta(name: str, meta: dict):
    d = server_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (SERVER_META_FILE + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp, d / SERVER_META_FILE)


def mc_tuple(version: str):
    nums = [int(n) for n in re.findall(r"\d+", (version or "").split("-")[0])[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)
