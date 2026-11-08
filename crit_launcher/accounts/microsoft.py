"""Microsoft / Xbox / Minecraft sign-in."""

import json
import time
import urllib.parse
import uuid

from ..config.settings import settings_store
from ..core.http import http_get_auth, http_send
from ..core.logs import log_exception
from .errors import AuthError
from .store import accounts_store


MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MS_CLIENT_ID = "00000000402b5328"
MS_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
MS_REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
MS_AUTHORIZE_URL = "https://login.live.com/oauth20_authorize.srf"
MS_TOKEN_URL = "https://login.live.com/oauth20_token.srf"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_XBOX_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"


def profile_for_token(token: str):
    status, prof = http_get_auth(MC_PROFILE_URL, token)
    if status in (401, 403):
        raise AuthError("That session token isn't valid (it has probably expired).")
    if status == 404:
        raise AuthError("That account doesn't own Minecraft: Java Edition.")
    if status != 200 or "id" not in prof:
        raise AuthError(f"Couldn't check the token (HTTP {status}).")
    return prof["name"], str(uuid.UUID(prof["id"]))


_XBOX_ERRORS = {
    2148916233: "This Microsoft account doesn't have an Xbox profile yet. Sign in once at xbox.com to create one, then try again.",
    2148916235: "Xbox Live isn't available in your country or region.",
    2148916236: "This account needs adult verification on the Xbox site before it can sign in.",
    2148916237: "This account needs adult verification on the Xbox site before it can sign in.",
    2148916238: "This is a child account. An adult has to add it to a Microsoft family before it can sign in.",
}


def ms_authorize_url() -> str:
    query = urllib.parse.urlencode({
        "client_id": MS_CLIENT_ID, "response_type": "code", "redirect_uri": MS_REDIRECT_URI,
        "scope": MS_SCOPE, "prompt": "select_account"})
    return f"{MS_AUTHORIZE_URL}?{query}"


def ms_token_request(fields: dict) -> dict:
    body = urllib.parse.urlencode({
        "client_id": MS_CLIENT_ID, "scope": MS_SCOPE, "redirect_uri": MS_REDIRECT_URI, **fields}).encode("utf-8")
    status, data = http_send("POST", MS_TOKEN_URL, None, body, "application/x-www-form-urlencoded")
    if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        desc = data.get("error_description") if isinstance(data, dict) else None
        raise AuthError(f"Microsoft rejected the sign-in{(': ' + str(desc)) if desc else ''} (HTTP {status}).")
    return data


def ms_to_minecraft(ms_access_token: str):
    xbl, last = None, None
    for prefix in ("", "t=", "d="):
        body = {"Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                               "RpsTicket": prefix + ms_access_token},
                "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}
        status, data = http_send("POST", XBL_AUTH_URL, None, json.dumps(body).encode("utf-8"),
                                 "application/json", headers={"x-xbl-contract-version": "1"})
        if status == 200 and isinstance(data, dict) and data.get("Token"):
            xbl = data
            break
        last = status
    if not xbl:
        raise AuthError(f"Xbox Live sign-in failed (HTTP {last}).")
    try:
        uhs = xbl["DisplayClaims"]["xui"][0]["uhs"]
    except (KeyError, IndexError, TypeError):
        raise AuthError("Xbox Live gave an unexpected answer.")

    body = {"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
            "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}
    status, data = http_send("POST", XSTS_AUTH_URL, None, json.dumps(body).encode("utf-8"),
                             "application/json", headers={"x-xbl-contract-version": "1"})
    if status == 401 and isinstance(data, dict):
        code = data.get("XErr")
        raise AuthError(_XBOX_ERRORS.get(code, f"Xbox Live refused the sign-in (error {code})."))
    if status != 200 or not isinstance(data, dict) or not data.get("Token"):
        raise AuthError(f"Xbox security token request failed (HTTP {status}).")
    xsts = data["Token"]
    try:
        uhs = data["DisplayClaims"]["xui"][0]["uhs"] or uhs
    except (KeyError, IndexError, TypeError):
        pass

    body = {"identityToken": f"XBL3.0 x={uhs};{xsts}"}
    status, data = http_send("POST", MC_XBOX_LOGIN_URL, None, json.dumps(body).encode("utf-8"), "application/json")
    if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        if status == 429:
            raise AuthError("Too many sign-in attempts. Wait a bit and try again.")
        raise AuthError(f"Minecraft sign-in failed (HTTP {status}).")
    return data["access_token"], time.time() + float(data.get("expires_in") or 86400)


def save_ms_account(name: str, account_uuid: str, token: str, expires, refresh_token) -> dict:
    account = {"name": name, "uuid": account_uuid, "type": "microsoft",
               "mc_token": token, "mc_expires": expires, "refresh_token": refresh_token}

    def upsert(accounts):
        for a in accounts:
            if a.get("uuid") == account_uuid:
                a.update(account)
                a.pop("session_only", None)
                return
        accounts.append(account)
    accounts_store.update(upsert)
    settings_store.update(lambda st: st.__setitem__("active_account", account_uuid))
    return account


def ms_sign_in(tokens: dict) -> dict:
    mc_token, mc_exp = ms_to_minecraft(tokens["access_token"])
    name, account_uuid = profile_for_token(mc_token)
    return save_ms_account(name, account_uuid, mc_token, mc_exp, tokens.get("refresh_token"))


def refresh_ms_account(account: dict) -> dict:
    rt = account.get("refresh_token")
    if not rt:
        raise AuthError("Your Microsoft session expired. Sign in again from the Accounts page.")
    try:
        tokens = ms_token_request({"grant_type": "refresh_token", "refresh_token": rt})
    except AuthError:
        raise AuthError("Your Microsoft session expired. Sign in again from the Accounts page.")
    except Exception:
        log_exception("ms refresh")
        raise AuthError("Couldn't reach Microsoft to refresh your session. Check your connection.")
    try:
        mc_token, mc_exp = ms_to_minecraft(tokens["access_token"])
    except AuthError:
        raise
    except Exception:
        log_exception("ms refresh chain")
        raise AuthError("Couldn't reach Minecraft to refresh your session. Check your connection.")
    new_rt = tokens.get("refresh_token") or rt

    def upd(accounts):
        for a in accounts:
            if a.get("uuid") == account.get("uuid"):
                a["mc_token"], a["mc_expires"], a["refresh_token"] = mc_token, mc_exp, new_rt
    accounts_store.update(upd)
    return dict(account, mc_token=mc_token, mc_expires=mc_exp, refresh_token=new_rt)
