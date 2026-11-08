"""PaperMC download API."""

import json
import re
import time
import urllib.parse
import urllib.request

from .constants import PAPER_API, PAPER_USER_AGENT
from .meta import mc_tuple


def fetch_paper_versions() -> list:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(PAPER_API, headers={"User-Agent": PAPER_USER_AGENT,
                                                             "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:
            last = exc
            time.sleep(0.8 * (attempt + 1))
    else:
        raise last
    raw = data.get("versions", {})
    flat = []
    if isinstance(raw, dict):
        for group in raw.values():
            flat.extend(group)
    else:
        flat = list(raw)
    flat = [str(v) for v in flat if re.fullmatch(r"\d+(\.\d+)+", str(v))]
    if not flat:
        raise RuntimeError("Paper returned no versions.")
    return sorted(set(flat), key=mc_tuple, reverse=True)


def paper_latest_build(version: str) -> dict:
    req = urllib.request.Request(f"{PAPER_API}/versions/{urllib.parse.quote(version)}/builds",
                                 headers={"User-Agent": PAPER_USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        builds = json.loads(resp.read().decode("utf-8"))
    if isinstance(builds, dict):
        builds = builds.get("builds", [])
    if not builds:
        raise RuntimeError(f"Paper has no builds for {version}.")
    pick = next((b for b in builds if str(b.get("channel", "")).upper() == "STABLE"), builds[0])
    dl = (pick.get("downloads") or {}).get("server:default")
    if not dl or not dl.get("url"):
        raise RuntimeError("Paper build has no download.")
    return dl
