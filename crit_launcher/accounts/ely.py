"""Ely.by accounts, skins and authlib-injector."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from ..config.paths import RUNTIME_DIR
from ..config.settings import settings_store
from ..core.fsutil import file_is_valid
from ..core.http import USER_AGENT, http_download, http_get_json
from ..core.logs import log_exception
from ..skins.textures import fetch_texture_uri
from .errors import AuthError
from .store import accounts_store


ELY_AUTH_URL = "https://authserver.ely.by"
ELY_SKINS_URL = "https://skinsystem.ely.by"
ELY_AUTH = ELY_AUTH_URL + "/auth/authenticate"
ELY_REFRESH = ELY_AUTH_URL + "/auth/refresh"
ELY_INJECTOR_REPO = "yushijinhun/authlib-injector"
ELY_INJECTOR_API = "https://api.github.com/repos/" + ELY_INJECTOR_REPO + "/releases/latest"


def ely_post(url: str, payload: dict, token=None):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
               "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, {}


def ely_sign_in(username: str, password: str, totp: str = "") -> dict:
    if totp:
        password = password + ":" + totp
    payload = {"username": username, "password": password, "clientToken": _ely_client_token()}
    try:
        status, data = ely_post(ELY_AUTH, payload)
    except Exception:
        raise AuthError("Couldn't reach Ely.by. Check your connection.") from None
    message = (data or {}).get("errorMessage") or (data or {}).get("error") or ""
    if status == 401 and "two factor" in message.lower():
        raise AuthError("__totp__")
    if status != 200 or not data.get("accessToken"):
        raise AuthError(message or "Ely.by couldn't complete that sign-in.")
    profile = data.get("selectedProfile") or (data.get("availableProfiles") or [{}])[0]
    if not profile.get("id"):
        raise AuthError("That Ely.by account has no Minecraft profile.")
    return save_ely_account(profile.get("name"), profile["id"], data["accessToken"],
                            payload["clientToken"])


def _ely_client_token() -> str:
    stored = settings_store.read().get("ely_client_token")
    if stored:
        return stored
    token = uuid.uuid4().hex
    settings_store.update(lambda s: s.__setitem__("ely_client_token", token))
    return token


def save_ely_account(name: str, account_uuid: str, token: str, client_token: str) -> dict:
    account = {"name": name, "uuid": account_uuid, "type": "ely",
               "mc_token": token, "ely_client_token": client_token}

    def upsert(accounts):
        for a in accounts:
            if a.get("uuid") == account_uuid:
                a.update(account)
                return
        accounts.append(account)
    accounts_store.update(upsert)
    settings_store.update(lambda st: st.__setitem__("active_account", account_uuid))
    return account


def refresh_ely_account(account: dict) -> dict:
    token = account.get("mc_token")
    client_token = account.get("ely_client_token") or _ely_client_token()
    if not token:
        raise AuthError("Sign in with Ely.by again.")
    try:
        status, data = ely_post(ELY_REFRESH, {"accessToken": token, "clientToken": client_token})
    except Exception:
        raise AuthError("Couldn't reach Ely.by to refresh your session.") from None
    if status != 200 or not data.get("accessToken"):
        raise AuthError("Your Ely.by session expired. Sign in again from the Accounts page.")
    new_token = data["accessToken"]

    def upd(accounts):
        for a in accounts:
            if a.get("uuid") == account.get("uuid"):
                a["mc_token"] = new_token
                a["ely_client_token"] = client_token
    accounts_store.update(upd)
    return dict(account, mc_token=new_token, ely_client_token=client_token)


_ELY_SKIN_CACHE = {}
_ELY_SKIN_TTL = 5
_ELY_SKIN_MISS_TTL = 60


def ely_skin_for(name: str):
    """Skin and cape for one nick, via the bridge because the png has no CORS."""
    key = (name or "").strip().lower()
    if not key:
        return None

    now = time.time()
    hit = _ELY_SKIN_CACHE.get(key)
    if hit:
        value, stamped, ttl = hit
        if now - stamped < ttl:
            return value

    try:
        data = http_get_json(ELY_SKINS_URL + "/textures/" + urllib.parse.quote(key), retries=0)
    except Exception:
        data = None

    skin = (data or {}).get("SKIN") or {} if isinstance(data, dict) else {}
    uri = fetch_texture_uri(skin.get("url")) if skin.get("url") else None
    if not uri:
        _ELY_SKIN_CACHE[key] = (None, now, _ELY_SKIN_MISS_TTL)
        return None

    cape_url = ((data.get("CAPE") or {}).get("url"))
    result = {"name": name, "image": uri,
              "cape": fetch_texture_uri(cape_url) if cape_url else None,
              "variant": "slim" if (skin.get("metadata") or {}).get("model") == "slim" else "classic"}

    if len(_ELY_SKIN_CACHE) >= 256:
        oldest = min(_ELY_SKIN_CACHE, key=lambda k: _ELY_SKIN_CACHE[k][1])
        _ELY_SKIN_CACHE.pop(oldest, None)
    _ELY_SKIN_CACHE[key] = (result, now, _ELY_SKIN_TTL)
    return result


def authlib_injector() -> Path | None:
    """Download authlib-injector once; Ely.by needs it for skins in game."""
    dest = RUNTIME_DIR / "authlib-injector.jar"
    if file_is_valid(dest, None, None):
        try:
            with zipfile.ZipFile(dest) as zf:
                if any(n.endswith("Premain.class") for n in zf.namelist()):
                    return dest
        except (zipfile.BadZipFile, OSError):
            pass

    for attempt in range(2):
        try:
            release = http_get_json(ELY_INJECTOR_API, timeout=20, retries=0)
            asset = next((a for a in release.get("assets", [])
                          if (a.get("name") or "").endswith(".jar")), None)
            if not asset:
                break
            http_download(asset["browser_download_url"], dest, asset.get("size"))
            with zipfile.ZipFile(dest) as zf:
                if any(n.endswith("Premain.class") for n in zf.namelist()):
                    return dest
        except Exception:
            log_exception("authlib-injector download")
        dest.unlink(missing_ok=True)
        time.sleep(1.0 * (attempt + 1))
    return None
