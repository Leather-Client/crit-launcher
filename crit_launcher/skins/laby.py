"""LabyMod skin catalog client."""

import re
import threading
import urllib.error
import urllib.parse

from ..core.http import http_get_json
from .catalog import _catalog_cached
from .textures import _fetch_skin_png


LABY_SEARCH_URL = "https://laby.net/api/v3/search/textures/skin"
LABY_TAGS_URL = "https://laby.net/api/v3/tags"
LABY_TAG_URL = "https://laby.net/api/v3/tag/{tag_id}"
LABY_ORDERS = ("most_used", "latest", "trending_24h", "trending_7d", "trending_30d")
LABY_PAGE_SIZE = 40
LABY_TAG_PAGE_SIZE = 40
LABY_TAG_LOOKUP_PAGES = 20
LABY_SEARCH_SCAN_PAGES = 20
LABY_HASH_RE = re.compile(r"^[0-9a-f]{16,64}$")
LABY_TEXTURE_URLS = (
    "https://texture.laby.net/{h}.png",
)
_laby_tpl = {"value": None}
_laby_tpl_lock = threading.Lock()


def _laby_host_ok(host: str) -> bool:
    return host == "laby.net" or host.endswith(".laby.net") or host == "textures.minecraft.net"


def _laby_url_ok(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url or "")
    except Exception:
        return False
    return (p.scheme == "https" and _laby_host_ok(p.hostname or "")
            and "/render/" not in p.path and ".." not in urllib.parse.unquote(p.path))


def _laby_template(sample_hash: str):
    with _laby_tpl_lock:
        if _laby_tpl["value"]:
            return _laby_tpl["value"]
    for tpl in LABY_TEXTURE_URLS:
        if _fetch_skin_png(tpl.format(h=sample_hash)):
            with _laby_tpl_lock:
                _laby_tpl["value"] = tpl
            return tpl
    return None


def _laby_name(tags: str) -> str:
    seen, words = set(), []
    for w in (tags or "").split():
        if w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    return " ".join(words[:3]) or "Skin"


def _laby_tag_for_query(query: str, force: bool = False):
    query = (query or "").strip().casefold()
    if not query:
        return None
    tags, matches = [], []
    for page in range(LABY_TAG_LOOKUP_PAGES):
        offset = page * LABY_TAG_PAGE_SIZE
        page_tags = _catalog_cached(
            f"laby:tags:{offset}:{LABY_TAG_PAGE_SIZE}",
            lambda offset=offset: http_get_json(
                f"{LABY_TAGS_URL}?{urllib.parse.urlencode({'offset': offset, 'size': LABY_TAG_PAGE_SIZE})}",
                timeout=20, retries=1),
            force,
        )
        if not isinstance(page_tags, list):
            raise ValueError("Unexpected tag list from laby.net.")
        tags.extend(page_tags)
        matches = [tag for tag in tags if isinstance(tag, dict)
                   and query == str(tag.get("name") or "").casefold()
                   and str(tag.get("id") or "").isdigit()]
        if matches or len(page_tags) < LABY_TAG_PAGE_SIZE:
            break
    if not matches:
        return None
    matches.sort(key=lambda tag: -int(tag.get("use_count") or 0))
    return matches[0]


def _laby_fetch_page(offset: int, size: int, order: str, search: str = "", force: bool = False,
                     model: str = "all"):
    terms = [term.strip().casefold() for term in re.split(r"[,;]", search or "") if term.strip()]
    if len(terms) > 5:
        raise ValueError("Search for up to five tags at a time.")
    if model not in ("all", "slim", "classic"):
        model = "all"
    tag = _laby_tag_for_query(terms[0], force) if len(terms) == 1 else None
    tag_name = str(tag.get("name") or terms[0]) if tag else ""
    matches, has_more = [], False

    def matches_filters(item, check_tags=True):
        if not isinstance(item, dict):
            return False
        item_tags = str((item or {}).get("tags") or "").casefold()
        if check_tags and terms and not all(term in item_tags for term in terms):
            return False
        is_slim = item.get("slim") is True
        if model == "slim" and not is_slim:
            return False
        if model == "classic" and is_slim:
            return False
        return True

    if tag:
        scan_offset = 0
        exhausted = False
        for _ in range(LABY_SEARCH_SCAN_PAGES):
            query = urllib.parse.urlencode({"size": LABY_PAGE_SIZE, "offset": scan_offset})
            data = _catalog_cached(
                f"laby:tag-feed:{tag['id']}:{scan_offset}",
                lambda query=query, tag_id=int(tag["id"]): http_get_json(
                    f"{LABY_TAG_URL.format(tag_id=tag_id)}?{query}", timeout=20, retries=1),
                force,
            )
            if not isinstance(data, list):
                raise ValueError("Unexpected response from laby.net.")
            matches.extend(item for item in data if matches_filters(item, check_tags=False))
            if len(matches) > offset + size:
                break
            if len(data) < LABY_PAGE_SIZE:
                exhausted = True
                break
            scan_offset += len(data)
        has_more = len(matches) > offset + size or not exhausted and len(matches) >= offset + size
    elif not terms and model == "all":
        query = urllib.parse.urlencode({"size": size, "offset": offset, "order": order})
        data = http_get_json(f"{LABY_SEARCH_URL}?{query}", timeout=20, retries=1)
        feed_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(feed_results, list):
            raise ValueError("Unexpected response from laby.net.")
        matches = feed_results
        has_more = len(feed_results) >= size
    else:
        scan_offset = 0
        exhausted = False
        for _ in range(LABY_SEARCH_SCAN_PAGES):
            query = urllib.parse.urlencode({"size": LABY_PAGE_SIZE, "offset": scan_offset, "order": order})
            feed = _catalog_cached(
                f"laby:search-feed:{order}:{scan_offset}",
                lambda query=query: http_get_json(f"{LABY_SEARCH_URL}?{query}", timeout=20, retries=1),
                force,
            )
            feed_results = feed.get("results") if isinstance(feed, dict) else None
            if not isinstance(feed_results, list):
                raise ValueError("Unexpected response from laby.net.")
            matches.extend(item for item in feed_results if matches_filters(item))
            if len(matches) > offset + size:
                break
            if len(feed_results) < LABY_PAGE_SIZE:
                exhausted = True
                break
            scan_offset += len(feed_results)
        has_more = len(matches) > offset + size or not exhausted and len(matches) >= offset + size

    results = matches[offset:offset + size]
    skins, seen = [], set()
    for r in results:
        if not isinstance(r, dict):
            continue
        h = str(r.get("image_hash") or "").lower()
        if not LABY_HASH_RE.match(h) or h in seen:
            continue
        seen.add(h)
        tags = tag_name or str(r.get("tags") or "")
        try:
            uses = max(0, int(r.get("use_count") or 0))
        except (TypeError, ValueError):
            uses = 0
        skins.append({"hash": h, "name": _laby_name(tags), "tags": tags,
                      "uses": uses, "slim": r.get("slim") is True})
    tpl = _laby_template(skins[0]["hash"]) if skins else None
    if skins and not tpl:
        raise LookupError("laby-texture")
    for sk in skins:
        sk["url"] = tpl.format(h=sk["hash"])
    return skins, has_more


def _laby_error(exc) -> str:
    if isinstance(exc, ValueError) and str(exc).startswith("Search for up to"):
        return str(exc)
    if isinstance(exc, LookupError):
        return "Couldn't find laby.net's skin download address. Try the Critical Skin API source for now."
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "laby.net is limiting requests right now. Try again in a little while."
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
        return "laby.net requires you to sign in to view more skin results."
    return "Couldn't reach laby.net. Check your connection."
