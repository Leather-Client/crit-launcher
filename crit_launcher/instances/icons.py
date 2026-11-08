"""Built-in and custom instance icons."""

import base64
import logging
import re
from pathlib import Path

from ..config.paths import INSTANCE_ICONS_DIR
from ..core.logs import log_exception


INSTANCE_ICONS = {"box", "shirt", "cloth", "scroll", "flame", "hammer", "circle", "triangle", "star",
                  "hexagon", "diamond", "heart", "cat", "ghost", "zap", "gem", "critclient", "oneclient"}


def sanitize_custom_icon_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip().rstrip(".")
    name = name[:100].rstrip(" .")
    if not name:
        name = "Image"
    if name.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def custom_instance_icon_path(icon_key: str) -> Path | None:
    match = re.fullmatch(r"custom:(.+)", str(icon_key or ""))
    if not match:
        return None
    name = match.group(1)
    if sanitize_custom_icon_name(name) != name:
        return None
    path = INSTANCE_ICONS_DIR / name
    if path.is_file():
        return path
    legacy_path = INSTANCE_ICONS_DIR / f"{name}.png"
    if legacy_path.is_file():
        return legacy_path
    return path


def list_custom_instance_icons() -> list:
    result = []
    paths = []
    for path in INSTANCE_ICONS_DIR.iterdir():
        try:
            paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            log_exception(f"inspect custom instance icon {path.name}")
    for _, path in sorted(paths, key=lambda item: (item[0], item[1].name.lower())):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() == ".tmp":
            continue
        try:
            data = path.read_bytes()
        except OSError:
            log_exception(f"read custom instance icon {path.name}")
            continue
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            logging.error("Invalid custom instance icon file: %s", path.name)
            continue
        name = path.stem if path.suffix.lower() == ".png" else path.name
        if sanitize_custom_icon_name(name) != name:
            logging.error("Invalid custom instance icon filename: %s", path.name)
            continue
        result.append({
            "key": f"custom:{name}",
            "name": name,
            "data": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        })
    return result
