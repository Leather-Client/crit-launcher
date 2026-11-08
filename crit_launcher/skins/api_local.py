"""JS-bridge API: offline-account skins."""

import logging
import re
import urllib.parse
import urllib.request

from ..accounts.ely import ely_skin_for
from ..accounts.store import accounts_store, find_account
from ..addons.modrinth import MODRINTH_API
from ..config.paths import SKINS_DIR
from ..config.settings import settings_store
from ..core.fsutil import _write_bytes_atomic
from ..core.http import USER_AGENT, http_get_json
from ..core.images import PNG_SIG, decode_png_uri, png_uri
from ..core.logs import log_exception
from ..instances.store import (
    instance_game_dir,
    instance_mods_dir,
    list_instances,
    read_instance_meta,
)
from .capes import MOJANG_CAPES, _CAPE_BY_ID, _cape_url
from .custom_skin_loader import (
    CSL_PROJECT,
    CSL_RUN_ONCE_MSG,
    _csl_find_config,
    _csl_installed,
    _csl_local_dir,
    _csl_write_config,
    _mod_present,
)
from .textures import fetch_texture_uri


class LocalSkinsApi:
    """Skins for offline/Ely accounts through CustomSkinLoader."""

    def _local_context(self, instance_name):
        accounts = accounts_store.read()
        acc = find_account(accounts, settings_store.read().get("active_account")) or (accounts[0] if accounts else None)
        if not acc or acc.get("type") != "offline":
            raise ValueError("Local skins are for offline accounts.")
        username = acc.get("name") or ""
        if not re.match(r"^[A-Za-z0-9_]{1,16}$", username):
            raise ValueError("This account's username can't be used for a local skin.")
        meta = read_instance_meta(instance_name)
        if not meta:
            raise ValueError("Instance not found.")
        if meta.get("loader", "vanilla") not in ("fabric", "forge"):
            raise ValueError("Vanilla instances can't load CustomSkinLoader. Pick a Fabric or Forge instance.")
        return username, meta

    def _csl_ensure(self, instance_name, meta) -> bool:
        if _csl_installed(instance_name):
            return False
        loader, mc = meta.get("loader"), meta.get("version")
        try:
            version = self._find_version(CSL_PROJECT, loader, mc)
        except Exception:
            log_exception("csl lookup")
            raise ValueError("Couldn't reach Modrinth to download CustomSkinLoader. Check your connection.")
        if not version:
            raise ValueError(f"CustomSkinLoader doesn't support {str(loader).capitalize()} {mc}, "
                             "so this version is unsupported for local skins.")
        mods_dir = instance_mods_dir(instance_name)
        mods_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._download_version_file(version, mods_dir)
            for dep in version.get("dependencies", []):
                if dep.get("dependency_type") != "required" or not dep.get("project_id"):
                    continue
                try:
                    proj = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(dep['project_id'])}")
                    if _mod_present(instance_name, proj.get("slug") or proj.get("title") or ""):
                        continue
                    dv = self._find_version(dep["project_id"], loader, mc)
                    if dv:
                        self._download_version_file(dv, mods_dir)
                except Exception:
                    log_exception("csl dependency")
        except Exception:
            log_exception("csl download")
            raise ValueError("Couldn't download CustomSkinLoader. Check your connection and try again.")
        if not _csl_installed(instance_name):
            raise ValueError("CustomSkinLoader didn't download correctly. Try again.")
        return True

    def local_skin_targets(self):
        try:
            out = []
            for inst in list_instances():
                loader = inst.get("loader", "vanilla")
                if loader not in ("fabric", "forge"):
                    continue
                out.append({"name": inst["name"], "version": inst["version"], "loader": loader,
                            "csl": _csl_installed(inst["name"])})
            return {"success": True, "instances": out}
        except Exception:
            log_exception("local_skin_targets")
            return {"success": False, "error": "Failed to list instances."}

    def _offline_username(self):
        accounts = accounts_store.read()
        acc = find_account(accounts, settings_store.read().get("active_account")) or (accounts[0] if accounts else None)
        if not acc or acc.get("type") != "offline":
            raise ValueError("Local skins are for offline accounts.")
        username = acc.get("name") or ""
        if not re.match(r"^[A-Za-z0-9_]{1,16}$", username):
            raise ValueError("This account's username can't be used for a local skin.")
        return username

    def local_skin_plan(self):
        try:
            installed, installable, total = [], [], 0
            support = {}
            for inst in list_instances():
                loader = inst.get("loader", "vanilla")
                if loader not in ("fabric", "forge"):
                    continue
                total += 1
                name = inst["name"]
                if _csl_installed(name):
                    installed.append(name)
                    continue
                key = (loader, inst.get("version"))
                if key not in support:
                    try:
                        support[key] = bool(self._find_version(CSL_PROJECT, loader, inst.get("version")))
                    except Exception:
                        support[key] = True
                if support[key]:
                    installable.append({"name": name, "version": inst.get("version"), "loader": loader})
            return {"success": True, "total": total, "installed": installed, "installable": installable}
        except Exception:
            log_exception("local_skin_plan")
            return {"success": False, "error": "Failed to list instances."}

    def local_skin_state(self):
        try:
            username = self._offline_username()
            skin, cape = None, None
            for inst in list_instances():
                name = inst["name"]
                if inst.get("loader", "vanilla") not in ("fabric", "forge") or not _csl_installed(name):
                    continue
                base = _csl_local_dir(name)
                skin_path = base / "skins" / f"{username}.png"
                cape_path = base / "capes" / f"{username}.png"
                if skin is None and skin_path.is_file():
                    skin = png_uri(skin_path.read_bytes())
                if cape is None and cape_path.is_file():
                    try:
                        cape = _CAPE_BY_ID.get((base / ".crit_cape").read_text(encoding="utf-8").strip())
                    except OSError:
                        cape = None
                if skin and cape:
                    break
            if skin is None:
                stored = SKINS_DIR / f"{username}.png"
                if stored.is_file():
                    skin = png_uri(stored.read_bytes())
            return {"success": True, "name": username, "variant": "classic", "skin": skin,
                    "cape_id": cape["id"] if cape else None,
                    "capes": [{"alias": cape["name"], "active": True}] if cape else []}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("local_skin_state")
            return {"success": False, "error": f"Couldn't read the local skin: {exc}"}

    def local_use_ely(self, name=None):
        """Copy an Ely.by skin onto the offline account's local skin file."""
        try:
            username = self._offline_username()
            source = (name or username).strip()
            skin = ely_skin_for(source)
            if not skin:
                return {"success": False, "error": f"Ely.by has no skin for {source}."}

            png = decode_png_uri(skin["image"])
            _write_bytes_atomic(SKINS_DIR / f"{username}.png", png)
            settings_store.update(lambda s: s.__setitem__("local_ely_source", source))

            result = self.local_set_skin(skin["image"], [i["name"] for i in list_instances()])
            if not result.get("success") and not result.get("results"):
                return {"success": True, "saved": True, "pending": True,
                        "message": "Saved. It applies once an instance has CustomSkinLoader."}
            result["saved"] = True
            return result
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("local_use_ely")
            return {"success": False, "error": "Couldn't copy that Ely.by skin."}

    def local_install_csl(self, instance_name):
        try:
            _, meta = self._local_context(instance_name)
            return {"success": True, "downloaded": self._csl_ensure(instance_name, meta)}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("local_install_csl")
            return {"success": False, "error": f"Couldn't install CustomSkinLoader: {exc}"}

    def _local_bulk(self, install, write, write_config=True):
        wanted = {n for n in (install or []) if isinstance(n, str)}
        results = []
        for inst in list_instances():
            name = inst["name"]
            if inst.get("loader", "vanilla") not in ("fabric", "forge"):
                continue
            has_mod = _csl_installed(name)
            if not has_mod and name not in wanted:
                continue
            downloaded = False
            try:
                username, meta = self._local_context(name)
                if not has_mod:
                    downloaded = self._csl_ensure(name, meta)
                if write_config:
                    if _csl_find_config(name) is None:
                        logging.info("CustomSkinLoader.json not found for %s (looked under %s)",
                                     name, instance_game_dir(name))
                        prefix = "CustomSkinLoader was installed. " if downloaded else ""
                        results.append({"name": name, "ok": False, "downloaded": downloaded,
                                        "error": prefix + CSL_RUN_ONCE_MSG})
                        continue
                    _csl_write_config(name)
                write(_csl_local_dir(name), username)
                results.append({"name": name, "ok": True, "downloaded": downloaded})
            except ValueError as exc:
                results.append({"name": name, "ok": False, "downloaded": downloaded, "error": str(exc)})
            except Exception as exc:
                log_exception("local skin on " + name)
                results.append({"name": name, "ok": False, "downloaded": downloaded, "error": f"Failed: {exc}"})
        if not results:
            return {"success": False, "error": "No instance has CustomSkinLoader yet."}
        return {"success": any(r["ok"] for r in results), "results": results}

    def local_set_skin(self, image, install=None):
        try:
            png = decode_png_uri(image)

            def write(base, username):
                _write_bytes_atomic(base / "skins" / f"{username}.png", png)
            return self._local_bulk(install, write)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("local_set_skin")
            return {"success": False, "error": f"Couldn't set the local skin: {exc}"}

    def local_ely_preview(self, name):
        try:
            skin = ely_skin_for((name or "").strip())
            if not skin:
                return {"success": False, "error": f"No Ely.by skin for {name}."}
            return {"success": True, **skin}
        except Exception:
            log_exception("local_ely_preview")
            return {"success": False, "error": "Couldn't reach Ely.by."}

    def local_capes(self):
        return {"success": True, "capes": [{"id": c["id"], "name": c["name"], "url": _cape_url(c)} for c in MOJANG_CAPES]}

    def local_cape_image(self, cape_id):
        try:
            cape = _CAPE_BY_ID.get(cape_id)
            if not cape:
                return {"success": False, "error": "Unknown cape."}
            uri = fetch_texture_uri(_cape_url(cape))
            return {"success": bool(uri), "image": uri}
        except Exception:
            log_exception("local_cape_image")
            return {"success": False, "error": "Couldn't load that cape."}

    def local_set_cape(self, cape_id=None, install=None):
        try:
            cape, data = None, None
            if cape_id:
                cape = _CAPE_BY_ID.get(cape_id)
                if not cape:
                    return {"success": False, "error": "Unknown cape."}
                req = urllib.request.Request(_cape_url(cape), headers={"User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        data = resp.read(300_001)
                except Exception:
                    raise ValueError("Couldn't download that cape. Check your connection.")
                if not data.startswith(PNG_SIG) or len(data) > 300_000:
                    raise ValueError("That cape texture isn't valid.")

            def write(base, username):
                cape_path = base / "capes" / f"{username}.png"
                if cape is None:
                    cape_path.unlink(missing_ok=True)
                    (base / ".crit_cape").unlink(missing_ok=True)
                else:
                    _write_bytes_atomic(cape_path, data)
                    _write_bytes_atomic(base / ".crit_cape", cape["id"].encode("utf-8"))
            return self._local_bulk(install if cape else None, write, write_config=bool(cape))
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("local_set_cape")
            return {"success": False, "error": f"Couldn't set the cape: {exc}"}

    def local_reset(self):
        try:
            def write(base, username):
                (base / "skins" / f"{username}.png").unlink(missing_ok=True)
                (base / "capes" / f"{username}.png").unlink(missing_ok=True)
                (base / ".crit_cape").unlink(missing_ok=True)
            return self._local_bulk(None, write, write_config=False)
        except Exception as exc:
            log_exception("local_reset")
            return {"success": False, "error": f"Couldn't reset the local skin: {exc}"}
