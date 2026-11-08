"""JS-bridge API: skins and capes."""

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..accounts.ely import ely_skin_for
from ..accounts.errors import AuthError
from ..accounts.microsoft import MC_PROFILE_URL
from ..accounts.session import ensure_fresh_account
from ..accounts.store import accounts_store, find_account
from ..config.paths import SKINS_DIR
from ..config.settings import settings_store
from ..core.http import http_get_auth, http_send
from ..core.images import decode_png_uri
from ..core.logs import log_exception
from .library import list_saved_skins, skins_index
from .sources import _skin_url_ok
from .textures import (
    MC_CAPE_URL,
    MC_SKINS_URL,
    _fetch_named_skin,
    _skin_error,
    fetch_texture_uri,
    multipart_skin,
)


class SkinsApi:
    """Skins and capes of the signed-in account, plus the saved-skin library."""

    def _skin_account(self):
        accounts = accounts_store.read()
        acc = find_account(accounts, settings_store.read().get("active_account")) or (accounts[0] if accounts else None)
        if not acc or acc.get("type") not in ("microsoft", "ely"):
            raise AuthError("Skins and capes need a Microsoft or Ely.by account. Add one under Accounts.")
        return ensure_fresh_account(acc)

    def get_live_skins(self, names, active=None):
        try:
            names = [n for n in (names or []) if isinstance(n, str) and n.strip()][:40]
            faces = {}
            if names:
                with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
                    for name, skin in zip(names, pool.map(ely_skin_for, names)):
                        if skin:
                            faces[name] = skin["image"]
            payload = {"success": True, "faces": faces}
            if active:
                acc = find_account(accounts_store.read(), settings_store.read().get("active_account"))
                if acc and acc.get("type") == "offline":
                    local = self.local_skin_state()
                    if local.get("success") and local.get("skin"):
                        payload["active"] = {"name": acc["name"], "image": local["skin"],
                                             "variant": "classic"}
                else:
                    skin = ely_skin_for(str(active))
                    if skin:
                        payload["active"] = skin
            return payload
        except Exception:
            log_exception("get_live_skins")
            return {"success": False, "error": "Couldn't refresh skins."}

    def ely_skin_profile(self):
        try:
            acc = find_account(accounts_store.read(), settings_store.read().get("active_account"))
            if not acc or acc.get("type") != "ely":
                return {"success": False, "error": "No Ely.by account is active."}
            skin = ely_skin_for(acc["name"])
            if not skin:
                return {"success": False, "error": "Ely.by has no skin for that account."}
            return {"success": True, "name": acc["name"], "skin": skin["image"],
                    "variant": skin["variant"], "capes": [], "cape_id": None}
        except Exception:
            log_exception("ely_skin_profile")
            return {"success": False, "error": "Couldn't read your Ely.by skin."}

    def get_skin_profile(self):
        try:
            acc = self._skin_account()
            if acc.get("type") == "ely":
                return self.ely_skin_profile()
            status, prof = http_get_auth(MC_PROFILE_URL, acc["mc_token"])
            if status != 200 or "id" not in prof:
                return {"success": False, "error": _skin_error(status)}
            skins = prof.get("skins") or []
            active = next((x for x in skins if x.get("state") == "ACTIVE"), skins[0] if skins else None)
            capes = [{"id": c.get("id"), "alias": c.get("alias") or "Cape",
                      "active": c.get("state") == "ACTIVE", "image": fetch_texture_uri(c.get("url"))}
                     for c in (prof.get("capes") or [])]
            return {"success": True, "name": prof.get("name"),
                    "variant": str((active or {}).get("variant", "CLASSIC")).lower(),
                    "skin": fetch_texture_uri((active or {}).get("url")), "capes": capes}
        except AuthError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("get_skin_profile")
            return {"success": False, "error": f"Couldn't load your profile: {exc}"}

    def set_skin(self, image, variant="classic"):
        try:
            png = decode_png_uri(image)
            variant = "slim" if variant == "slim" else "classic"
            acc = self._skin_account()
            body, ctype = multipart_skin(variant, png)
            status, _ = http_send("POST", MC_SKINS_URL, acc["mc_token"], body, ctype)
            if status in (200, 204):
                return {"success": True}
            return {"success": False, "error": _skin_error(status)}
        except (AuthError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("set_skin")
            return {"success": False, "error": f"Couldn't change the skin: {exc}"}

    def reset_skin(self):
        try:
            acc = self._skin_account()
            status, _ = http_send("DELETE", MC_SKINS_URL + "/active", acc["mc_token"])
            if status in (200, 204):
                return {"success": True}
            return {"success": False, "error": _skin_error(status)}
        except AuthError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("reset_skin")
            return {"success": False, "error": f"Couldn't reset the skin: {exc}"}

    def set_cape(self, cape_id=None):
        try:
            acc = self._skin_account()
            if cape_id:
                status, _ = http_send("PUT", MC_CAPE_URL, acc["mc_token"],
                                      json.dumps({"capeId": cape_id}).encode("utf-8"), "application/json")
            else:
                status, _ = http_send("DELETE", MC_CAPE_URL, acc["mc_token"])
            if status in (200, 204):
                return {"success": True}
            return {"success": False, "error": _skin_error(status)}
        except AuthError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("set_cape")
            return {"success": False, "error": f"Couldn't change the cape: {exc}"}

    def lookup_player_skin(self, name):
        try:
            name = (name or "").strip()
            if not re.match(r"^[A-Za-z0-9_]{1,16}$", name):
                return {"success": False, "error": "Enter a valid player name."}
            result = _fetch_named_skin(name) or ely_skin_for(name)
            if not result:
                return {"success": False, "error": f'No skin found for "{name}". They may use the default skin.'}
            return {"success": True, **result}
        except Exception:
            log_exception("lookup_player_skin")
            return {"success": False, "error": "Couldn't reach Mojang. Check your connection."}

    def list_saved_skins(self):
        try:
            return {"success": True, "skins": list_saved_skins()}
        except Exception:
            log_exception("list_saved_skins")
            return {"success": False, "error": "Couldn't read your saved skins."}

    def save_skin(self, name, image, variant="classic", source=None):
        try:
            png = decode_png_uri(image)
            name = re.sub(r"\s+", " ", (name or "").strip())[:32] or "My skin"
            skin_id = uuid.uuid4().hex[:10]
            (SKINS_DIR / f"{skin_id}.png").write_bytes(png)
            entry = {"id": skin_id, "name": name,
                     "variant": "slim" if variant == "slim" else "classic", "created": time.time()}
            if isinstance(source, str) and _skin_url_ok(source):
                entry["source"] = source
            skins_index.update(lambda idx: idx.append(entry))
            return {"success": True, "skins": list_saved_skins()}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("save_skin")
            return {"success": False, "error": "Couldn't save the skin."}

    def delete_saved_skin(self, skin_id):
        try:
            skin_id = re.sub(r"[^A-Za-z0-9]", "", str(skin_id))
            (SKINS_DIR / f"{skin_id}.png").unlink(missing_ok=True)
            skins_index.update(lambda idx: idx.__setitem__(slice(None), [e for e in idx if e.get("id") != skin_id]))
            return {"success": True, "skins": list_saved_skins()}
        except Exception:
            log_exception("delete_saved_skin")
            return {"success": False, "error": "Couldn't delete the skin."}
