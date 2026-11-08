"""Skin endpoints, texture fetching and upload helpers."""

import base64
import json
import struct
import urllib.parse
import urllib.request
import uuid

from ..core.http import USER_AGENT, http_get_json
from ..core.images import PNG_SIG, decode_png_uri, png_uri


MC_SKINS_URL = "https://api.minecraftservices.com/minecraft/profile/skins"
MC_CAPE_URL = "https://api.minecraftservices.com/minecraft/profile/capes/active"
MOJANG_LOOKUP_URL = "https://api.mojang.com/users/profiles/minecraft/"
SESSION_PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/"


def _texture_host_ok(host: str) -> bool:
    return (host == "minecraft.net" or host.endswith(".minecraft.net")
            or host == "ely.by" or host.endswith(".ely.by"))


def fetch_texture_uri(url, ely=False):
    if not url:
        return None
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if not _texture_host_ok(urllib.parse.urlparse(url).hostname or ""):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(400_001)
        if len(data) > 400_000 or not data.startswith(PNG_SIG):
            return None
        return png_uri(data)
    except Exception:
        return None


def multipart_skin(variant: str, png: bytes):
    boundary = "----CritLauncher" + uuid.uuid4().hex
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="variant"\r\n\r\n{variant}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="skin.png"\r\n'
            f'Content-Type: image/png\r\n\r\n').encode("utf-8")
    return head + png + f"\r\n--{boundary}--\r\n".encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _skin_error(status: int) -> str:
    if status in (401, 403):
        return "Your session has expired. Sign in again."
    if status == 400:
        return "Minecraft rejected that request."
    if status == 429:
        return "Too many changes in a short time. Wait a bit and try again."
    return f"Minecraft answered with an error (HTTP {status})."


def _fetch_named_skin(name: str):
    try:
        found = http_get_json(MOJANG_LOOKUP_URL + urllib.parse.quote(name), retries=1)
    except Exception:
        return None
    if not found or "id" not in found:
        return None
    try:
        prof = http_get_json(SESSION_PROFILE_URL + found["id"], retries=1)
    except Exception:
        return None
    skin = None
    for prop in prof.get("properties", []):
        if prop.get("name") == "textures":
            try:
                tex = json.loads(base64.b64decode(prop["value"]).decode("utf-8"))
            except Exception:
                continue
            skin = (tex.get("textures") or {}).get("SKIN")
    if not skin or not skin.get("url"):
        return None
    uri = fetch_texture_uri(skin["url"])
    if not uri:
        return None
    try:
        decode_png_uri(uri)
    except ValueError:
        return None
    return {"name": found.get("name", name), "image": uri,
            "variant": "slim" if (skin.get("metadata") or {}).get("model") == "slim" else "classic"}


def _fetch_skin_png(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/png,*/*"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(200_001)
    except Exception:
        return None
    if len(data) > 200_000 or not data.startswith(PNG_SIG) or len(data) < 33:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return data if (width, height) in ((64, 64), (64, 32)) else None
