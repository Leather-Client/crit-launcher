"""The saved-skins library."""

from ..config.paths import SKINS_DIR
from ..core.images import png_uri
from ..core.jsonstore import JsonStore


skins_index = JsonStore(SKINS_DIR / "index.json", [])


def list_saved_skins() -> list:
    out = []
    for entry in skins_index.read():
        path = SKINS_DIR / f"{entry.get('id', '')}.png"
        if path.is_file():
            out.append({**entry, "image": png_uri(path.read_bytes())})
    return out
