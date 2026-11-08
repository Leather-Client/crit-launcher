"""JS-bridge API: skin catalogs."""

import urllib.request

from ..core.http import USER_AGENT
from ..core.images import PNG_SIG, decode_png_uri, png_uri
from ..core.logs import log_exception
from .catalog import _catalog_cached, _catalog_error, _catalog_tree, _catalog_walk
from .laby import LABY_ORDERS, LABY_PAGE_SIZE, _laby_error, _laby_fetch_page
from .sources import _skin_url_ok


class SkinCatalogApi:
    """Browsing the online skin catalogs."""

    def catalog_all(self, force=False):
        try:
            def load():
                skins = _catalog_tree()
                if skins is None:
                    skins = _catalog_walk()
                skins.sort(key=lambda s: s["name"].lower())
                return skins
            return {"success": True, "skins": _catalog_cached("all", load, bool(force))}
        except Exception as exc:
            log_exception("catalog_all")
            return {"success": False, "error": _catalog_error(exc)}

    def catalog_laby(self, offset=0, order="most_used", force=False, size=10, search="", model="all"):
        try:
            offset = max(0, int(offset or 0))
            order = order if order in LABY_ORDERS else "most_used"
            size = min(LABY_PAGE_SIZE, max(1, int(size or 10)))
            search = str(search or "").strip()[:80]
            model = model if model in ("all", "slim", "classic") else "all"
            key = f"laby:{order}:{model}:{size}:{offset}:{search.casefold()}"
            skins, more = _catalog_cached(
                key, lambda: _laby_fetch_page(offset, size, order, search, bool(force), model), bool(force))
            return {"success": True, "skins": skins, "has_more": more, "next_offset": offset + size}
        except Exception as exc:
            log_exception("catalog_laby")
            return {"success": False, "error": _laby_error(exc)}

    def catalog_fetch_skin(self, url):
        try:
            if not _skin_url_ok(url):
                return {"success": False, "error": "That isn't a catalog skin."}
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read(200_001)
            if not data.startswith(PNG_SIG):
                return {"success": False, "error": "That isn't a PNG image."}
            uri = png_uri(data)
            decode_png_uri(uri)
            return {"success": True, "image": uri}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("catalog_fetch_skin")
            return {"success": False, "error": "Couldn't download that skin. Check your connection."}
