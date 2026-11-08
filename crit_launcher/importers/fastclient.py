"""FastClient instance scanner."""

import re
from pathlib import Path

from .common import _count_dir, _count_images, _loader_from_version_json, _read_json_safe


_FASTCLIENT_FABRIC_LOG_RE = re.compile(r"Loading Minecraft (\S+) with Fabric Loader (\S+)")
_QUILT_LOG_RE = re.compile(r"Loading Minecraft (\S+) with Quilt Loader (\S+)")
_FASTCLIENT_MODLAUNCHER_RE = re.compile(r"ModLauncher running: args \[(.*?)\]")
_FASTCLIENT_VERSION_ARG_RE = re.compile(r"--version,\s*([^,\]]+)")
_FASTCLIENT_TARGET_ARG_RE = re.compile(r"--launchTarget,\s*([^,\]]+)")
_FASTCLIENT_MODDED_VERSION_RE = re.compile(r"^(.+?)-(forge|neoforge)-(.+)$")
FASTCLIENT_ROOT_SOURCE_ID = "__root__"


def _read_first_log_line(logs_dir: Path):
    log_path = logs_dir / "latest.log"
    if not log_path.is_file():
        return None
    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    return line
    except OSError:
        return None
    return None


def _parse_fastclient_log_line(line: str):
    if not line:
        return None
    m = _FASTCLIENT_FABRIC_LOG_RE.search(line)
    if m:
        return "fabric", m.group(1), m.group(2)
    m = _QUILT_LOG_RE.search(line)
    if m:
        return "quilt", m.group(1), m.group(2)
    m = _FASTCLIENT_MODLAUNCHER_RE.search(line)
    if m:
        args_str = m.group(1)
        vm = _FASTCLIENT_VERSION_ARG_RE.search(args_str)
        version_str = vm.group(1).strip() if vm else None
        tm = _FASTCLIENT_TARGET_ARG_RE.search(args_str)
        target = tm.group(1).strip().lower() if tm else ""
        if version_str:
            vm2 = _FASTCLIENT_MODDED_VERSION_RE.match(version_str)
            if vm2:
                mc_version, loader, loader_version = vm2.group(1), vm2.group(2), vm2.group(3)
                return loader, mc_version, loader_version
            loader = "neoforge" if "neoforge" in target else "forge"
            return loader, version_str, None
        loader = "neoforge" if "neoforge" in target else "forge"
        return loader, None, None
    return None


def _fastclient_versions(versions_dir: Path) -> dict:
    versions = {}
    if not versions_dir.is_dir():
        return versions
    for vsub in versions_dir.iterdir():
        if not vsub.is_dir():
            continue
        vjson = _read_json_safe(vsub / f"{vsub.name}.json")
        if not vjson:
            for f in vsub.iterdir():
                if f.suffix == ".json":
                    vjson = _read_json_safe(f)
                    if vjson:
                        break
        if vjson:
            versions[vsub.name] = vjson
    return versions


def _fastclient_profile_version_id(profile_dir: Path):
    for f in profile_dir.iterdir():
        if f.is_file() and f.suffix == ".json":
            data = _read_json_safe(f)
            if isinstance(data, dict):
                vid = (data.get("lastVersionId") or data.get("versionId")
                       or data.get("version") or data.get("mcVersion"))
                if vid:
                    return vid
    return None


def _fastclient_profile_index(root: Path):
    index = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return index
    for f in entries:
        if not (f.is_file() and f.suffix == ".json"):
            continue
        data = _read_json_safe(f)
        profiles_obj = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(profiles_obj, dict):
            continue
        for key, entry in profiles_obj.items():
            if not isinstance(entry, dict):
                continue
            vid = (entry.get("lastVersionId") or entry.get("versionId")
                   or entry.get("version") or entry.get("mcVersion"))
            if not vid:
                continue
            names = {key, entry.get("name")}
            game_dir = entry.get("gameDir")
            if game_dir:
                names.add(Path(game_dir).name)
            for n in names:
                if n:
                    index[n] = vid
    return index


def _scan_fastclient(root: Path) -> list:
    versions = _fastclient_versions(root / "versions")
    found = []

    if (root / "logs" / "latest.log").is_file():
        mc_version = next(iter(versions)) if len(versions) == 1 else None
        found.append({
            "source_id": FASTCLIENT_ROOT_SOURCE_ID,
            "name": "Vanilla",
            "version": mc_version,
            "loader": "vanilla",
            "loader_version": None,
            "has_options": (root / "options.txt").is_file(),
            "mods_count": 0,
            "resourcepacks_count": _count_dir(root / "resourcepacks"),
            "saves_count": _count_dir(root / "saves"),
            "shaderpacks_count": _count_dir(root / "shaderpacks"),
            "schematics_count": _count_dir(root / "schematics"),
            "screenshots_count": _count_images(root / "screenshots"),
            "config_count": 0,
        })

    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        return found
    profile_index = _fastclient_profile_index(root)
    for sub in sorted(profiles_dir.iterdir(), key=lambda p: p.name.lower()):
        if not sub.is_dir():
            continue

        log_info = _parse_fastclient_log_line(_read_first_log_line(sub / "logs"))
        if log_info:
            loader, mc_version, loader_version = log_info
        else:
            version_id = (profile_index.get(sub.name)
                          or _fastclient_profile_version_id(sub))
            if not version_id and sub.name in versions:
                version_id = sub.name
            if not version_id and len(versions) == 1:
                version_id = next(iter(versions))

            vjson = versions.get(version_id)
            if vjson:
                mc_version = vjson.get("inheritsFrom") or vjson.get("id")
                loader, loader_version = _loader_from_version_json(vjson, mc_version)
            else:
                mc_version, loader, loader_version = version_id, "unknown", None

        found.append({
            "source_id": sub.name,
            "name": sub.name,
            "version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "has_options": (sub / "options.txt").is_file(),
            "mods_count": _count_dir(sub / "mods", ".jar"),
            "resourcepacks_count": _count_dir(sub / "resourcepacks"),
            "saves_count": _count_dir(sub / "saves"),
            "shaderpacks_count": _count_dir(sub / "shaderpacks"),
            "schematics_count": _count_dir(sub / "schematics"),
            "screenshots_count": _count_images(sub / "screenshots"),
            "config_count": _count_dir(sub / "config"),
        })
    return found
