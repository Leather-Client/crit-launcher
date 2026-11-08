"""URL validation and fetching for catalog skins."""

from .catalog import _catalog_url_ok
from .laby import _laby_url_ok


def _skin_url_ok(url: str) -> bool:
    return _catalog_url_ok(url) or _laby_url_ok(url)
