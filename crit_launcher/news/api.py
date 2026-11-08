"""JS-bridge API: Minecraft news and public servers."""

import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from ..core.http import http_get_json
from ..core.logs import log_exception


MC_NEWS_HOST = "https://launchercontent.mojang.com"
_mc_news_mem = {"at": 0.0, "value": None}
_pub_servers_mem = {"at": 0.0, "value": None}
PUBLIC_SERVERS = (
    ("mc.ratonii.ro", "Ratonii Network", "Europe", ("minigames", "pvp", "survival"), True, True),
    ("xy.twerion.net", "XY.TWERION.NET", "Asia", ("bedwars", "skyblock", "lifesteal"), True, True),
    ("mc.hypixel.net", "Hypixel", "North America", ("minigames", "pvp", "skyblock", "bedwars"), False, True),
    ("play.cubecraft.net", "CubeCraft", "Europe", ("minigames", "pvp", "bedwars"), False, True),
    ("play.wynncraft.com", "Wynncraft", "North America", ("rpg", "survival"), False, True),
    ("play.pika-network.net", "PikaNetwork", "North America", ("bedwars", "skyblock", "survival"), True, True),
    ("jartex.fun", "JartexNetwork", "Europe", ("survival", "economy", "skyblock"), True, False),
    ("play.extremecraft.net", "ExtremeCraft", "Europe", ("bedwars", "skyblock", "survival"), True, False),
    ("play.manacube.com", "ManaCube", "North America", ("survival", "skyblock", "economy"), False, False),
    ("play.herobrine.org", "Herobrine", "North America", ("skyblock", "survival"), False, False),
    ("hub.mc-complex.com", "Complex Gaming", "North America", ("minigames", "pvp"), False, False),
    ("play.invadedlands.net", "InvadedLands", "North America", ("survival", "economy"), False, False),
    ("bedwarspractice.club", "Bedwars Practice", "North America", ("bedwars", "pvp"), False, True),
    ("play.mccentral.org", "MC Central", "North America", ("minigames", "survival"), False, False),
    ("pvplegacy.net", "PvP Legacy", "North America", ("pvp", "factions"), False, False),
    ("play.pixelmonrealms.com", "Pixelmon Realms", "North America", ("modded", "survival"), False, False),
    ("play.hyperlands.net", "Hyperlands", "North America", ("skyblock", "survival"), False, False),
    ("play.fadecloud.com", "FadeCloud", "North America", ("survival", "economy"), False, False),
    ("gommehd.net", "GommeHD", "Europe", ("bedwars", "minigames", "pvp"), False, True),
    ("play.timolia.de", "Timolia", "Europe", ("minigames", "pvp"), False, False),
    ("play.thearchon.net", "The Archon", "North America", ("survival", "economy"), False, False),
    ("play.vimemc.net", "ViMeMC", "Europe", ("survival", "economy"), False, False),
    ("play.snapcraft.net", "Snapcraft", "North America", ("survival", "skyblock"), False, False),
    ("play.blossomcraft.org", "BlossomCraft", "North America", ("survival", "skyblock"), False, False),
    ("play.skyblock.net", "Skyblock.net", "North America", ("skyblock", "economy"), False, False),
    ("mineland.net", "Mineland", "Europe", ("minigames", "survival"), False, False),
    ("2b2t.org", "2b2t", "North America", ("anarchy", "survival"), False, False),
    ("kaboom.pw", "Kaboom", "North America", ("anarchy",), False, False),
    ("constantiam.net", "Constantiam", "North America", ("anarchy",), False, False),
    ("6b6t.org", "6b6t", "North America", ("anarchy",), False, False),
    ("endcrystal.me", "EndCrystal", "North America", ("anarchy", "pvp"), False, False),
)


class NewsApi:
    """Minecraft news and the public server list."""

    def get_mc_news(self, force=False):
        try:
            now = time.time()
            if not force and _mc_news_mem["value"] is not None and now - _mc_news_mem["at"] < 900:
                return {"success": True, "items": _mc_news_mem["value"]}
            data = None
            for url in (MC_NEWS_HOST + "/v2/news.json", MC_NEWS_HOST + "/news.json"):
                try:
                    data = http_get_json(url, timeout=15, retries=1)
                    break
                except Exception:
                    log_exception("get_mc_news " + url)
            if not isinstance(data, dict):
                data = {}

            def absolute(u):
                if not isinstance(u, str) or not u:
                    return None
                if u.startswith(("http://", "https://")):
                    return u
                return MC_NEWS_HOST + (u if u.startswith("/") else "/" + u)

            items = []
            for e in (data.get("entries") or []):
                if not isinstance(e, dict):
                    continue
                title = str(e.get("title") or "").strip()
                if not title:
                    continue
                img = e.get("newsPageImage") or e.get("playPageImage") or {}
                raw = img.get("url") if isinstance(img, dict) else None
                image, alt = absolute(raw), None
                if isinstance(raw, str) and raw.startswith("/") and not raw.startswith("/v2/"):
                    alt = MC_NEWS_HOST + "/v2" + raw
                link = e.get("readMoreLink")
                items.append({"title": title, "date": str(e.get("date") or "")[:25], "image": image,
                              "image_alt": alt,
                              "link": link if isinstance(link, str) and link.startswith(("https://", "http://")) else None})
            for a in (data.get("article_grid") or []):
                if not isinstance(a, dict):
                    continue
                tile = a.get("default_tile") or {}
                title = str(tile.get("title") or "").strip()
                if not title:
                    continue
                raw = ((tile.get("image") or {}).get("imageURL"))
                link = a.get("articleURL")
                if isinstance(link, str) and link.startswith("/"):
                    link = "https://www.minecraft.net" + link
                items.append({"title": title, "date": str(a.get("publish_date") or "")[:25],
                              "image": absolute(raw) if not (isinstance(raw, str) and raw.startswith("/")) else "https://www.minecraft.net" + raw,
                              "image_alt": None,
                              "link": link if isinstance(link, str) and link.startswith(("https://", "http://")) else None})
            if not items:
                try:
                    pn = http_get_json(MC_NEWS_HOST + "/v2/javaPatchNotes.json", timeout=15, retries=1)
                    for e in (pn.get("entries") or []):
                        if not isinstance(e, dict):
                            continue
                        title = str(e.get("title") or "").strip() or ("Minecraft " + str(e.get("version") or "").strip())
                        img = e.get("image") if isinstance(e.get("image"), dict) else {}
                        raw = img.get("url")
                        items.append({"title": title, "date": str(e.get("date") or "")[:25], "image": absolute(raw),
                                      "image_alt": (MC_NEWS_HOST + "/v2" + raw) if isinstance(raw, str) and raw.startswith("/") and not raw.startswith("/v2/") else None,
                                      "link": None})
                except Exception:
                    log_exception("get_mc_news patch notes")
            items.sort(key=lambda x: x["date"], reverse=True)
            items = items[:12]
            if not items:
                raise RuntimeError("news feed was empty")
            _mc_news_mem["at"], _mc_news_mem["value"] = now, items
            return {"success": True, "items": items}
        except Exception:
            log_exception("get_mc_news")
            if _mc_news_mem["value"]:
                return {"success": True, "items": _mc_news_mem["value"], "cached": True}
            return {"success": False, "error": "Couldn't load the Minecraft news. Check your connection."}

    def get_public_servers(self, force=False):
        try:
            now = time.time()
            if not force and _pub_servers_mem["value"] is not None and now - _pub_servers_mem["at"] < 600:
                return {"success": True, "servers": _pub_servers_mem["value"]}

            def version_info(v):
                nums = re.findall(r"\d+\.\d+(?:\.\d+)?", str(v or ""))
                if not nums:
                    return "", "", str(v or "")[:24]
                key = lambda x: tuple(int(p) for p in x.split("."))
                lo, hi = min(nums, key=key), max(nums, key=key)
                return lo, hi, (lo if lo == hi else f"{lo} - {hi}")

            def probe(entry):
                host, name, region, tags, offline, featured = entry
                try:
                    d = http_get_json("https://api.mcsrvstat.us/3/" + urllib.parse.quote(host), timeout=10, retries=0)
                except Exception:
                    return None
                if not isinstance(d, dict) or not d.get("online"):
                    return None
                players = d.get("players") if isinstance(d.get("players"), dict) else {}
                version = d.get("version")
                if isinstance(version, list):
                    version = version[0] if version else ""
                vmin, vmax, label = version_info(version)
                icon = d.get("icon")
                motd = d.get("motd") if isinstance(d.get("motd"), dict) else {}
                lines = [str(x).strip()[:90] for x in (motd.get("clean") or []) if str(x).strip()][:2]
                return {"host": host, "name": name, "region": region, "tags": list(tags),
                        "offline": bool(offline), "featured": bool(featured),
                        "online": int(players.get("online") or 0), "max": int(players.get("max") or 0),
                        "version": label, "vmin": vmin, "vmax": vmax, "motd": lines,
                        "icon": icon if isinstance(icon, str) and icon.startswith("data:image/") else None}

            with ThreadPoolExecutor(max_workers=10) as ex:
                found = [r for r in ex.map(probe, PUBLIC_SERVERS) if r]
            found.sort(key=lambda s: s["online"], reverse=True)
            if not found:
                raise RuntimeError("no server answered")
            _pub_servers_mem["at"], _pub_servers_mem["value"] = now, found
            return {"success": True, "servers": found}
        except Exception:
            log_exception("get_public_servers")
            if _pub_servers_mem["value"]:
                return {"success": True, "servers": _pub_servers_mem["value"], "cached": True}
            return {"success": False, "error": "Couldn't reach the server list."}
