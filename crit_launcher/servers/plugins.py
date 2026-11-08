"""Reading plugin jars."""

import logging
import re
import zipfile
from pathlib import Path

from ..instances.content import _authors_text, _clean_text, _zip_read


def read_plugin_jar(path: Path) -> dict:
    info = {"name": None, "version": None, "description": None, "authors": None,
            "loader": "paper", "_icon": None}
    try:
        with zipfile.ZipFile(path) as zf:
            raw = _zip_read(zf, "paper-plugin.yml") or _zip_read(zf, "plugin.yml")
            if raw:
                text = raw.decode("utf-8-sig", "replace")

                def grab(key):
                    m = re.search(rf"^{key}\s*:\s*['\"]?(.+?)['\"]?\s*$", text, re.M)
                    return m.group(1) if m else None
                info.update(name=grab("name"), version=grab("version"),
                            description=grab("description"), authors=grab("author"))
    except Exception:
        logging.info("Couldn't read plugin metadata from %s", path.name)
    info["name"] = _clean_text(info["name"], 80)
    info["description"] = _clean_text(info["description"])
    info["authors"] = _authors_text(info["authors"])
    return info
