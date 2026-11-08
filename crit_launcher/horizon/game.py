"""Preparing the Minecraft files a Horizon build needs."""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from ..config.paths import HORIZON_DATA_DIR, RUNTIME_DIR
from ..core.fsutil import file_is_valid
from ..core.http import http_download, http_get_json
from ..core.logs import log_exception
from ..core.system import CREATE_NO_WINDOW
from ..minecraft.constants import JAVA_RUNTIME_INDEX_URL, RESOURCES_BASE_URL
from ..minecraft.java import (
    find_system_javas,
    install_managed_java,
    managed_java_path,
    mojang_runtime_platform,
    probe_java,
)
from ..minecraft.libraries import download_all, entry_weight
from ..minecraft.meta_cache import meta_cache
from .constants import HORIZON_JAVA_MAJOR
from .layout import horizon_exe, horizon_minecraft_dir


def horizon_ensure_minecraft(mc_version: str, tracker, pinned=None):
    """Download Mojang's own files for mc_version (json, client jar, libraries, assets)."""
    root = horizon_minecraft_dir()
    vfolder = root / "versions" / mc_version
    vjson = vfolder / (mc_version + ".json")
    done = root / (".complete-" + mc_version)
    pin_file = root / ("pinned-%s.json" % mc_version)
    refresh = bool(pinned)
    if pinned:
        root.mkdir(parents=True, exist_ok=True)
        pin_file.write_text(json.dumps(list(pinned)), encoding="utf-8")
    elif pin_file.is_file():
        try:
            pinned = tuple(json.loads(pin_file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pinned = None
    if (done.is_file() and vjson.is_file() and not refresh
            and (vfolder / (mc_version + ".jar")).is_file()):
        return root, vjson
    if done.is_file():
        done.unlink(missing_ok=True)

    tracker.set_task(f"Downloading Minecraft {mc_version}", force=True)
    if pinned:
        json_url, json_sha1 = pinned
    else:
        manifest = meta_cache.get_manifest()
        entry = next((v for v in manifest.get("versions", []) if v.get("id") == mc_version), None)
        if not entry:
            raise RuntimeError(f"Mojang's version list has no Minecraft {mc_version}.")
        json_url, json_sha1 = entry["url"], entry.get("sha1")
    vfolder.mkdir(parents=True, exist_ok=True)
    if not file_is_valid(vjson, None, json_sha1):
        http_download(json_url, vjson, None, json_sha1)

    manifest = json.loads(vjson.read_text(encoding="utf-8"))
    client = (manifest.get("downloads") or {}).get("client") or {}
    if not client.get("url"):
        raise RuntimeError(f"Minecraft {mc_version} has no client download.")

    libraries = {}
    for lib in manifest.get("libraries", []):
        downloads = lib.get("downloads") or {}
        candidates = [downloads.get("artifact")] + list((downloads.get("classifiers") or {}).values())
        for art in candidates:
            if art and art.get("path") and art.get("url"):
                libraries[art["path"]] = {"path": art["path"], "url": art["url"],
                                           "size": art.get("size"), "sha1": art.get("sha1")}

    index_info = manifest.get("assetIndex") or {}
    if not index_info.get("url"):
        raise RuntimeError(f"Minecraft {mc_version} has no asset index.")
    index_path = root / "assets" / "indexes" / ((index_info.get("id") or mc_version) + ".json")
    if not file_is_valid(index_path, None, index_info.get("sha1")):
        http_download(index_info["url"], index_path, None, index_info.get("sha1"))
    objects = json.loads(index_path.read_text(encoding="utf-8")).get("objects", {})
    assets = {}
    for obj in objects.values():
        digest = obj.get("hash")
        if not digest:
            continue
        assets[digest] = {"path": digest[:2] + "/" + digest,
                          "url": RESOURCES_BASE_URL + "/" + digest[:2] + "/" + digest,
                          "size": obj.get("size"), "sha1": digest, "quick": True}

    
    client_entry = {"path": mc_version + ".jar", "url": client["url"],
                    "size": client.get("size"), "sha1": client.get("sha1")}
    total = (entry_weight(client_entry)
             + sum(entry_weight(e) for e in libraries.values())
             + sum(entry_weight(e) for e in assets.values()))
    tracker.reset(max(total, 1))
    tracker.set_task(f"Downloading Minecraft {mc_version} files", force=True)
    download_all([(client_entry, vfolder)], tracker)
    tracker.set_task(f"Downloading Minecraft {mc_version} libraries", force=True)
    download_all([(e, root / "libraries") for e in libraries.values()], tracker)
    tracker.set_task(f"Downloading Minecraft {mc_version} assets", force=True)
    download_all([(e, root / "assets" / "objects") for e in assets.values()], tracker, workers=16)
    done.write_text(str(time.time()), encoding="utf-8")
    return root, vjson


def horizon_ensure_server_jar(mc_version: str, vjson: Path, tracker) -> Path:
    jar = HORIZON_DATA_DIR / "server-jars" / (mc_version + ".jar")
    if file_is_valid(jar):
        return jar
    server = (json.loads(vjson.read_text(encoding="utf-8")).get("downloads") or {}).get("server") or {}
    if not server.get("url"):
        raise RuntimeError(f"Minecraft {mc_version} has no server jar to prepare from.")
    tracker.reset(max(int(server.get("size") or 1), 1))
    tracker.set_task(f"Downloading the Minecraft {mc_version} server", force=True)
    http_download(server["url"], jar, server.get("size"), server.get("sha1"), tracker.add)
    return jar


def horizon_find_java(tracker) -> str:
    """A Java 25+ runtime: an installed one if possible, otherwise Mojang's own download."""
    wanted = HORIZON_JAVA_MAJOR
    candidates = []
    if RUNTIME_DIR.exists():
        for component_dir in sorted(RUNTIME_DIR.iterdir()):
            path = managed_java_path(component_dir.name)
            if path:
                candidates.append(path)
    candidates += find_system_javas()
    best = None
    for candidate in candidates:
        major, _ = probe_java(candidate)
        if major is not None and major >= wanted and (best is None or major < best[0]):
            best = (major, candidate)
    if best:
        return best[1]

    tracker.set_task(f"Finding a Java {wanted} runtime", force=True)
    try:
        index = http_get_json(JAVA_RUNTIME_INDEX_URL, timeout=30)
        platform_entries = index.get(mojang_runtime_platform()) or {}
        for component, entries in platform_entries.items():
            for entry in entries or []:
                name = str(((entry.get("version") or {}).get("name")) or "")
                if name == str(wanted) or name.startswith(str(wanted) + "."):
                    return install_managed_java(component, tracker)
    except Exception:
        log_exception("horizon java lookup")
    raise RuntimeError(
        f"Horizon needs Java {wanted}. Install a JDK {wanted} and point Settings at it, then try again.")


def horizon_run_logged(cmd, cwd: Path, tracker, task: str, java_path=None):
    tracker.set_task(task, force=True)
    env = dict(os.environ)
    if java_path:
        java = Path(java_path).resolve()
        env["JAVA_HOME"] = str(java.parent.parent)
        env["PATH"] = str(java.parent) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1, env=env,
            creationflags=CREATE_NO_WINDOW, shell=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Couldn't start {Path(cmd[0]).name}: {exc}")
    lines = []
    for line in proc.stdout:
        lines.append(line)
        if len(lines) > 400:
            del lines[:200]
    return proc.wait(), "".join(lines)


def horizon_prepare_build(vdir: Path, mc_version: str, mc_root: Path, server_jar: Path,
                          tracker, java_path):
    prep = Path(vdir) / horizon_exe("horizon-prepare")
    if not prep.is_file():
        raise RuntimeError("The Horizon download is missing " + prep.name)
    base = [str(prep), "--root", str(vdir)]
    cmd = base + ["--prepare", "--server-jar", str(server_jar), "--minecraft-dir", str(mc_root)]
    code, output = horizon_run_logged(cmd, vdir, tracker, "Preparing Horizon's game resources", java_path)
    if code != 0:
        
        
        match = re.search(
            r"https://piston-meta\.mojang\.com/v1/packages/([0-9a-f]{40})/%s\.json"
            % re.escape(mc_version), output)
        if match:
            horizon_ensure_minecraft(mc_version, tracker, pinned=(match.group(0), match.group(1)))
            code, output = horizon_run_logged(
                cmd, vdir, tracker, "Preparing Horizon's game resources", java_path)
    if code != 0:
        logging.error("horizon-prepare failed:\n%s", output[-4000:])
        raise RuntimeError("Horizon couldn't build the game resources. See launcher.log for details.")
