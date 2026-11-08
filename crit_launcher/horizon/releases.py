"""Finding, verifying and unpacking signed Horizon builds."""

import base64
import json
import platform
import re
import shutil
import tarfile
import time
import urllib.error
import zipfile
from pathlib import Path

from ..config.paths import TMP_DIR
from ..core.ed25519 import horizon_ed25519_verify
from ..core.fsutil import file_sha256, is_within
from ..core.http import http_download, http_get_bytes, http_get_json
from ..core.system import current_os_name
from ..instances.store import _INVALID_NAME_RE
from .constants import HORIZON_API, HORIZON_MARKER, HORIZON_SIG, HORIZON_SUMS, HORIZON_TRUSTED_KEYS
from .layout import horizon_asset_name, horizon_exe, horizon_version_dir


def horizon_version_label(release: dict) -> str:
    tag = str(release.get("tag_name") or "")
    if not (tag == "nightly" or tag.startswith("nightly-")):
        return tag
    title = str(release.get("name") or "")
    date = commit = None
    if title.startswith("Nightly "):
        parts = [p.strip() for p in title[len("Nightly "):].split("\u00b7")]
        if parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
            date = parts[0]
        if len(parts) > 1 and re.fullmatch(r"[0-9a-fA-F]+", parts[1]):
            commit = parts[1]
    date = date or str(release.get("published_at") or "")[:10]
    return ("nightly-%s-%s" % (date, commit)) if commit else ("nightly-%s" % date)


def horizon_releases(asset_name: str) -> list:
    try:
        data = http_get_json(HORIZON_API + "/releases?per_page=100", timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise RuntimeError("GitHub's rate limit was reached. Try again in a few minutes.")
        raise RuntimeError(f"Couldn't fetch Horizon's releases (HTTP {exc.code}).")
    except Exception as exc:
        raise RuntimeError(f"Couldn't reach GitHub to download Horizon: {exc}")

    found = []
    for release in data or []:
        tag = str(release.get("tag_name") or "")
        if release.get("draft"):
            continue
        nightly = tag == "nightly" or tag.startswith("nightly-")
        game_tag = len(tag) > 1 and tag[0] == "v" and tag[1].isdigit()
        if not (nightly or game_tag):
            continue
        assets = {a.get("name"): a for a in release.get("assets") or []}
        if asset_name not in assets:
            continue
        found.append({"release": release, "asset": assets[asset_name],
                      "version": horizon_version_label(release), "tag": tag})
    found.sort(key=lambda e: str(e["release"].get("published_at") or ""), reverse=True)
    return found


def horizon_signed_sha256(release: dict, asset_name: str, version: str) -> str:
    assets = {a.get("name"): a for a in release.get("assets") or []}
    if HORIZON_SUMS not in assets or HORIZON_SIG not in assets:
        raise RuntimeError("This Horizon build isn't signed, so it wasn't installed.")
    sums = http_get_bytes(assets[HORIZON_SUMS]["browser_download_url"], limit=1 << 20)
    signature = http_get_bytes(assets[HORIZON_SIG]["browser_download_url"],
                               limit=1 << 20).decode("utf-8", "replace")
    channel = "nightly" if version.startswith("nightly-") else str(release.get("tag_name") or "")
    message = ("horizon release %s\n" % channel).encode() + sums
    keys = [base64.b64decode(k) for k in HORIZON_TRUSTED_KEYS]
    signed = False
    for line in signature.splitlines():
        try:
            raw = base64.b64decode(line.strip(), validate=True)
        except Exception:
            continue
        if len(raw) == 64 and any(horizon_ed25519_verify(k, message, raw) for k in keys):
            signed = True
            break
    if not signed:
        raise RuntimeError("The Horizon download's signature didn't match its release key, "
                           "so it wasn't installed.")
    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(None, 1)
        if (len(parts) == 2 and parts[1].lstrip("*") == asset_name
                and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0])):
            return parts[0].lower()
    raise RuntimeError("That Horizon file isn't in the signed checksums, so it wasn't installed.")


def horizon_unpack(archive: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                if not is_within(dest, dest / member.filename):
                    raise RuntimeError("Unsafe path in the Horizon download: " + member.filename)
            zf.extractall(dest)
        return
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            target = dest / member.name
            if not is_within(dest, target) or member.isdev():
                raise RuntimeError("Unsafe entry in the Horizon download: " + member.name)
            if member.issym() and not is_within(dest, target.parent / member.linkname):
                raise RuntimeError("Unsafe link in the Horizon download: " + member.name)
            if member.islnk() and not is_within(dest, dest / member.linkname):
                raise RuntimeError("Unsafe link in the Horizon download: " + member.name)
        try:
            tf.extractall(dest, filter="data")
        except TypeError:
            tf.extractall(dest)


def horizon_install_build(tracker):
    """Download, verify and unpack Horizon's newest signed build. Returns (version, folder)."""
    asset_name = horizon_asset_name()
    if not asset_name:
        raise RuntimeError("Horizon has no build for %s %s. It supports Windows x64, Linux x86-64 "
                           "and macOS Apple Silicon." % (platform.system(), platform.machine()))
    releases = horizon_releases(asset_name)
    if not releases:
        raise RuntimeError("Horizon has no installable build for this system right now.")
    for entry in releases:
        vdir = horizon_version_dir(entry["version"])
        if (vdir / HORIZON_MARKER).is_file():
            return entry["version"], vdir

    entry = releases[0]
    version, asset = entry["version"], entry["asset"]
    expected = horizon_signed_sha256(entry["release"], asset_name, version)
    vdir = horizon_version_dir(version)

    tracker.reset(max(int(asset.get("size") or 1), 1))
    tracker.set_task(f"Downloading Horizon {version}", force=True)
    suffix = ".zip" if asset_name.endswith(".zip") else ".tar.gz"
    archive = TMP_DIR / ("horizon-" + _INVALID_NAME_RE.sub("_", version) + suffix)
    http_download(asset["browser_download_url"], archive, asset.get("size"), None, tracker.add)
    if file_sha256(archive) != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError("The Horizon download didn't match its signed checksum, so it wasn't installed.")

    tracker.set_task("Unpacking Horizon", force=True)
    unpacked = TMP_DIR / ("horizon-unpack-" + _INVALID_NAME_RE.sub("_", version))
    shutil.rmtree(unpacked, ignore_errors=True)
    try:
        horizon_unpack(archive, unpacked)
        children = list(unpacked.iterdir())
        top = children[0] if len(children) == 1 and children[0].is_dir() else unpacked
        client = top / horizon_exe("horizon-client")
        if not client.is_file():
            raise RuntimeError("The Horizon download has no " + client.name)
        if current_os_name() != "windows":
            for name in ("horizon-client", "horizon-prepare", "horizon-ctl", "horizon-server"):
                tool = top / name
                if tool.is_file():
                    tool.chmod(tool.stat().st_mode | 0o755)
        shutil.rmtree(vdir, ignore_errors=True)
        vdir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(top), str(vdir))
        (vdir / HORIZON_MARKER).write_text(json.dumps({
            "version": version, "tag": entry["tag"], "asset": asset_name,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)
        archive.unlink(missing_ok=True)
    return version, vdir
