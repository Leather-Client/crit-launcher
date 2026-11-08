"""CurseForge proxy client."""

import logging
import re
import time
import urllib.error
import urllib.parse
from pathlib import Path

from ..core.fsutil import is_within
from ..core.http import http_download, http_get_json, http_post_json


CURSEFORGE_API = "https://cf-api.skibidiscripts.workers.dev/v1"
CF_GAME_ID = 432                       
CF_CLASS_IDS = {"mod": 6, "resourcepack": 12, "shader": 6552, "modpack": 4471}
CF_LOADER_IDS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
CF_SORT = {"relevance": 2, "downloads": 6, "follows": 2, "newest": 11, "updated": 3}
CF_REQUIRED_RELATION = 3


class CurseForgeError(Exception):
    """Raised with a message that is safe to show in the launcher."""


def _cf_get(path: str, params=None, timeout: int = 20, retries: int = 2, unwrap: bool = True):
    """GET a CurseForge endpoint through the proxy, unwrapping its {"data": ...} envelope."""
    url = f"{CURSEFORGE_API}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    last = None
    for attempt in range(retries + 1):
        try:
            data = http_get_json(url, timeout=timeout, retries=0)
            if not unwrap:
                return data
            return data.get("data") if isinstance(data, dict) and "data" in data else data
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt < retries:
                    wait = 2.0 * (attempt + 1)
                    try:
                        ra = exc.headers.get("Retry-After") if exc.headers else None
                        wait = min(10.0, max(1.0, float(ra)))
                    except (TypeError, ValueError):
                        pass
                    time.sleep(wait)
                    last = exc
                    continue
                raise CurseForgeError("CurseForge is rate limiting requests. Try again in a minute.") from exc
            if exc.code in (400, 401, 403, 404):
                raise CurseForgeError("CurseForge couldn't handle that request.") from exc
            last = exc
        except Exception as exc:
            last = exc
        time.sleep(0.8 * (attempt + 1))
    raise last if last is not None else CurseForgeError("Couldn't reach CurseForge.")


def _cf_number(project_id) -> int:
    raw = str(project_id or "")
    if raw.startswith("cf:"):
        raw = raw[3:]
    if not raw.isdigit():
        raise CurseForgeError("That isn't a CurseForge project.")
    return int(raw)


def _cf_hit(mod: dict) -> dict:
    cats = [c.get("name") for c in (mod.get("categories") or []) if isinstance(c, dict) and c.get("name")]
    authors = [a.get("name") for a in (mod.get("authors") or []) if isinstance(a, dict) and a.get("name")]
    logo = mod.get("logo") if isinstance(mod.get("logo"), dict) else {}
    
    shots = []
    for s in (mod.get("screenshots") or []):
        if not isinstance(s, dict):
            continue
        url = s.get("url") or s.get("thumbnailUrl")
        if isinstance(url, str) and url.startswith("https://"):
            shots.append(url)
    return {
        "gallery": shots,
        "featured_gallery": shots[0] if shots else None,
        "color": None,
        "project_id": f"cf:{mod.get('id')}",
        "slug": None,
        "title": mod.get("name") or "Unknown",
        "description": mod.get("summary") or "",
        "author": ", ".join(authors) or "unknown",
        "downloads": int(mod.get("downloadCount") or 0),
        "follows": None,
        "icon_url": logo.get("thumbnailUrl") or logo.get("url"),
        "categories": cats,
        "display_categories": cats,
        "client_side": "optional",
        "server_side": "optional",
        "date_modified": mod.get("dateModified") or "",
        "source": "curseforge",
    }


def _cf_search(query: str, project_type: str, sort: str, offset: int, limit: int, filters=None):
    """Search CurseForge. Returns Modrinth-shaped hits so the Addons UI renders them as-is."""
    class_id = CF_CLASS_IDS.get(project_type)
    if not class_id:
        return {"hits": [], "total": 0}
    params = {"gameId": CF_GAME_ID, "classId": class_id,
              "sortField": CF_SORT.get(sort, 2), "sortOrder": "desc",
              "index": max(0, int(offset or 0)),           
              "pageSize": max(1, min(50, limit))}
    if query:
        params["searchFilter"] = query
    filters = filters if isinstance(filters, dict) else {}
    ver = filters.get("version")
    if isinstance(ver, str) and re.match(r"^[0-9][0-9A-Za-z_.+\-]{0,19}$", ver):
        params["gameVersion"] = ver
    loaders = [x for x in (filters.get("loaders") or []) if x in CF_LOADER_IDS]
    if project_type == "mod" and loaders:
        params["modLoaderType"] = CF_LOADER_IDS[loaders[0]]
    data = _cf_get("/mods/search", params, unwrap=False) or {}
    mods, pagination = [], {}
    if isinstance(data, list):
        mods = data
    elif isinstance(data, dict):
        mods = data.get("data") or []
        pagination = data.get("pagination") or {}
    hits = [_cf_hit(m) for m in mods if isinstance(m, dict) and m.get("id") is not None]
    return {"hits": hits, "total": int(pagination.get("totalCount") or len(hits))}


def _cf_file_sha1(file_info: dict):
    for h in (file_info.get("hashes") or []):
        if isinstance(h, dict) and int(h.get("algo") or 0) == 1 and h.get("value"):
            return h["value"]
    return None


def _cf_pick_file(files, mc_version=None, loader=None):
    """Newest release file that matches the instance's Minecraft version + loader."""
    usable = [f for f in (files or []) if isinstance(f, dict) and f.get("id") is not None]
    if mc_version:
        usable = [f for f in usable if mc_version in (f.get("gameVersions") or [])]
    if loader and loader in CF_LOADER_IDS:
        want = loader.lower()
        usable = [f for f in usable
                  if want in {str(g).lower() for g in (f.get("gameVersions") or [])}] or usable
    releases = [f for f in usable if int(f.get("releaseType") or 1) == 1]
    pool = releases or usable
    pool.sort(key=lambda f: str(f.get("fileDate") or ""), reverse=True)
    return pool[0] if pool else None


def _cf_download_file(mod_id: int, file_info: dict, dest_dir: Path):
    name = Path(str(file_info.get("fileName") or "")).name
    if not name:
        raise CurseForgeError("That CurseForge file has no name.")
    dest = dest_dir / name
    if not is_within(dest_dir, dest):
        raise CurseForgeError("Unsafe file name in addon.")
    url = file_info.get("downloadUrl")
    if not url:
        
        detail = _cf_get(f"/mods/{mod_id}/files/{file_info['id']}") or {}
        url = detail.get("downloadUrl") if isinstance(detail, dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CurseForgeError("CurseForge didn't give a download link for that file.")
    http_download(url, dest, file_info.get("fileLength"), _cf_file_sha1(file_info))
    return name


def _cf_dependencies(mod_id: int, file_id) -> list:
    if file_id is None:
        return []
    try:
        deps = _cf_get(f"/mods/{mod_id}/dependencies", {"fileId": file_id}) or []
    except CurseForgeError:
        return []
    return [d for d in deps if isinstance(d, dict) and d.get("modId")
            and int(d.get("relationType") or 0) == CF_REQUIRED_RELATION]


def curseforge_install_to(mod_id: int, dest_dir: Path, mc_version=None, loader=None,
                          visited=None, depth: int = 0, with_deps: bool = True) -> list:
    """Install a CurseForge project (plus its required dependencies) into dest_dir."""
    visited = visited if visited is not None else set()
    visited.add(mod_id)
    files = _cf_get(f"/mods/{mod_id}/files", {"gameVersion": mc_version})
    if isinstance(files, dict):
        files = files.get("data") or []
    chosen = _cf_pick_file(files, mc_version, loader)
    if not chosen:
        raise CurseForgeError(f"No CurseForge file of this project supports {mc_version or 'this setup'}.")
    out = [_cf_download_file(mod_id, chosen, dest_dir)]
    if not with_deps or depth >= 3:
        return out
    for dep in _cf_dependencies(mod_id, chosen.get("id")):
        dep_id = int(dep["modId"])
        if dep_id in visited:
            continue
        try:
            out += curseforge_install_to(dep_id, dest_dir, mc_version, loader, visited, depth + 1)
        except CurseForgeError:
            logging.warning("CurseForge dependency %s failed", dep_id)
    return out


_CF_LOADER_NAMES = ("forge", "fabric", "quilt", "neoforge")


def _cf_post(path: str, payload, timeout: int = 30):
    """POST to a CurseForge endpoint through the proxy and unwrap its {"data": ...} envelope."""
    try:
        data = http_post_json(f"{CURSEFORGE_API}{path}", payload, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise CurseForgeError(f"CurseForge lookup was refused (HTTP {exc.code}).") from exc
    return data.get("data") if isinstance(data, dict) and "data" in data else data


def _cf_file_compat(file_info: dict, mc, loader):
    """(matches the Minecraft version, matches the mod loader) for a CurseForge file.

    CurseForge lists loaders and Minecraft versions in one gameVersions list. A file that
    names no loader at all (old files) is not held against the loader."""
    games = [str(g) for g in (file_info.get("gameVersions") or [])]
    lower = {g.lower() for g in games}
    named = [g for g in lower if g in _CF_LOADER_NAMES]
    return mc in games, (str(loader).lower() in lower) or not named
