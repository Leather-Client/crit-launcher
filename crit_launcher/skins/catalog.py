"""The Critical skin catalog (GitHub backed)."""

import threading
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from ..core.http import http_get_json


CATALOG_REPO = "snigglaberry/critical-skin-api"
CATALOG_API = f"https://api.github.com/repos/{CATALOG_REPO}/contents/skin"
CATALOG_RAW_PREFIX = f"/{CATALOG_REPO}/"
CATALOG_TTL = 900
_catalog_cache = {}
_catalog_lock = threading.Lock()


def _catalog_cached(key, loader, force=False):
    now = time.time()
    if not force:
        with _catalog_lock:
            hit = _catalog_cache.get(key)
        if hit and now - hit[0] < CATALOG_TTL:
            return hit[1]
    data = loader()
    with _catalog_lock:
        _catalog_cache[key] = (now, data)
    return data


def _catalog_listing(sub=""):
    url = CATALOG_API + (("/" + urllib.parse.quote(sub)) if sub else "")
    data = http_get_json(url, timeout=20, retries=1)
    if not isinstance(data, list):
        raise ValueError("Unexpected response from GitHub.")
    return data


def _catalog_error(exc) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return "GitHub is limiting requests right now. Try again in a little while."
        if exc.code == 404:
            return "The skin catalog couldn't be found."
    return "Couldn't reach the skin catalog. Check your connection."


def _catalog_raw_url(letter: str, fname: str) -> str:
    return (f"https://raw.githubusercontent.com/{CATALOG_REPO}/main/skin/"
            f"{urllib.parse.quote(letter)}/{urllib.parse.quote(fname)}")


def _catalog_tree():
    try:
        data = http_get_json(f"https://api.github.com/repos/{CATALOG_REPO}/git/trees/main?recursive=1",
                             timeout=30, retries=1)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise
        return None
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("truncated"):
        return None
    out = []
    for node in data.get("tree") or []:
        parts = (node.get("path") or "").split("/")
        if (node.get("type") == "blob" and len(parts) == 3 and parts[0] == "skin"
                and parts[2].lower().endswith(".png")):
            out.append({"name": parts[2][:-4], "url": _catalog_raw_url(parts[1], parts[2])})
    return out


def _catalog_walk():
    letters = [i["name"] for i in _catalog_listing() if i.get("type") == "dir" and i.get("name")]

    def one(letter):
        out = []
        for i in _catalog_listing(letter):
            fname = i.get("name") or ""
            if i.get("type") != "file" or not fname.lower().endswith(".png"):
                continue
            url = i.get("download_url") or _catalog_raw_url(letter, fname)
            if _catalog_url_ok(url):
                out.append({"name": fname[:-4], "url": url})
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, letters))
    return [s for part in results for s in part]


def _catalog_url_ok(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url or "")
    except Exception:
        return False
    path = urllib.parse.unquote(p.path)
    return (p.scheme == "https" and p.hostname == "raw.githubusercontent.com"
            and path.startswith(CATALOG_RAW_PREFIX) and ".." not in path)
