"""Reading mods, resource packs and their icons from disk."""

import base64
import json
import logging
import re
import zipfile
from pathlib import Path

from ..core.fsutil import is_within
from .store import instance_game_dir


def _zip_read(zf: zipfile.ZipFile, member: str):
    member = member.replace("\\", "/").lstrip("./").lstrip("/")
    try:
        return zf.read(member)
    except KeyError:
        lower = member.lower()
        for n in zf.namelist():
            if n.lower() == lower:
                return zf.read(n)
    return None


def _clean_text(value, limit: int = 240):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        value = _flatten_component(value)
    text = re.sub(r"\u00a7.", "", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if text else None


def _flatten_component(c) -> str:
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(_flatten_component(x) for x in c)
    if isinstance(c, dict):
        out = str(c.get("text", "") or "")
        return out + "".join(_flatten_component(x) for x in c.get("extra", []))
    return str(c)


def _authors_text(authors):
    if not authors:
        return None
    if isinstance(authors, str):
        return _clean_text(authors, 80)
    names = []
    for a in authors:
        if isinstance(a, dict):
            a = a.get("name")
        if a:
            names.append(str(a))
    return _clean_text(", ".join(names[:3]), 80)


def _toml_fields(text: str) -> dict:
    data = None
    try:
        import tomllib
        data = tomllib.loads(text)
    except Exception:
        data = None
    if data is not None:
        mods = data.get("mods") or []
        m = mods[0] if mods and isinstance(mods[0], dict) else {}
        return {
            "name": m.get("displayName") or m.get("modId"),
            "version": m.get("version"),
            "description": m.get("description"),
            "icon": m.get("logoFile") or data.get("logoFile"),
            "authors": m.get("authors") or data.get("authors"),
        }

    def grab(key):
        rx = re.compile(r'^\s*' + key + r'\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"\n]*)"|\'([^\'\n]*)\')',
                        re.M | re.S)
        m = rx.search(text)
        return next((g for g in m.groups() if g is not None), None) if m else None
    return {"name": grab("displayName") or grab("modId"), "version": grab("version"),
            "description": grab("description"), "icon": grab("logoFile"), "authors": grab("authors")}


def _manifest_version(zf: zipfile.ZipFile):
    raw = _zip_read(zf, "META-INF/MANIFEST.MF")
    if raw:
        m = re.search(r"Implementation-Version:\s*(\S+)", raw.decode("utf-8", "ignore"))
        if m:
            return m.group(1)
    return None


def read_mod_jar(path: Path) -> dict:
    info = {"name": None, "version": None, "description": None, "authors": None,
            "loader": None, "_icon": None}
    try:
        with zipfile.ZipFile(path) as zf:
            raw = _zip_read(zf, "fabric.mod.json")
            if raw:
                d = json.loads(raw.decode("utf-8-sig"))
                icon = d.get("icon")
                if isinstance(icon, dict) and icon:
                    icon = icon[min(icon, key=lambda k: abs(int(k) - 128))] if all(
                        str(k).isdigit() for k in icon) else next(iter(icon.values()))
                info.update(name=d.get("name") or d.get("id"), version=d.get("version"),
                            description=d.get("description"), authors=d.get("authors"),
                            loader="fabric", _icon=icon if isinstance(icon, str) else None)
            else:
                raw = _zip_read(zf, "quilt.mod.json")
                if raw:
                    d = json.loads(raw.decode("utf-8-sig"))
                    ql = d.get("quilt_loader", {})
                    meta = ql.get("metadata", {})
                    contributors = meta.get("contributors")
                    info.update(name=meta.get("name") or ql.get("id"), version=ql.get("version"),
                                description=meta.get("description"),
                                authors=list(contributors) if isinstance(contributors, dict) else None,
                                loader="quilt", _icon=meta.get("icon") if isinstance(meta.get("icon"), str) else None)
                else:
                    toml_raw, loader = _zip_read(zf, "META-INF/neoforge.mods.toml"), "neoforge"
                    if not toml_raw:
                        toml_raw, loader = _zip_read(zf, "META-INF/mods.toml"), "forge"
                    if toml_raw:
                        f = _toml_fields(toml_raw.decode("utf-8-sig", "replace"))
                        info.update(name=f["name"], version=f["version"], description=f["description"],
                                    authors=f["authors"], loader=loader, _icon=f["icon"])
                    else:
                        raw = _zip_read(zf, "mcmod.info")
                        if raw:
                            d = json.loads(raw.decode("utf-8-sig"))
                            if isinstance(d, dict):
                                d = d.get("modList", [])
                            m = d[0] if d else {}
                            info.update(name=m.get("name") or m.get("modid"), version=m.get("version"),
                                        description=m.get("description"),
                                        authors=m.get("authorList") or m.get("authors"),
                                        loader="forge", _icon=m.get("logoFile"))
            version = info["version"]
            if isinstance(version, str) and "${" in version:
                version = _manifest_version(zf)
            info["version"] = _clean_text(version, 40)
    except Exception:
        logging.info("Couldn't read mod metadata from %s", path.name)
    info["name"] = _clean_text(info["name"], 80)
    info["description"] = _clean_text(info["description"])
    info["authors"] = _authors_text(info["authors"])
    return info


def read_resourcepack(path: Path) -> dict:
    info = {"name": None, "version": None, "description": None, "authors": None,
            "loader": None, "_icon": "pack.png"}
    try:
        raw = None
        if path.is_dir():
            f = path / "pack.mcmeta"
            raw = f.read_bytes() if f.is_file() else None
            if not (path / "pack.png").is_file():
                info["_icon"] = None
        else:
            with zipfile.ZipFile(path) as zf:
                raw = _zip_read(zf, "pack.mcmeta")
                if _zip_read(zf, "pack.png") is None:
                    info["_icon"] = None
        if raw:
            d = json.loads(raw.decode("utf-8-sig"))
            info["description"] = (d.get("pack") or {}).get("description")
    except Exception:
        logging.info("Couldn't read pack metadata from %s", path.name)
    info["description"] = _clean_text(info["description"])
    return info


def _content_folder(instance_name: str, kind: str) -> Path:
    game = instance_game_dir(instance_name)
    return game / {"mods": "mods", "resourcepacks": "resourcepacks", "shaderpacks": "shaderpacks"}[kind]


def list_instance_content(instance_name: str, kind: str) -> list:
    folder = _content_folder(instance_name, kind)
    items = []
    if not folder.is_dir():
        return items
    for p in folder.iterdir():
        lower = p.name.lower()
        enabled = True
        if kind == "mods":
            if lower.endswith(".jar.disabled"):
                enabled, stem = False, p.name[:-len(".jar.disabled")]
            elif lower.endswith(".jar") and p.is_file():
                stem = p.name[:-4]
            else:
                continue
            info = read_mod_jar(p)
        elif kind == "shaderpacks":
            if p.is_file() and lower.endswith(".zip"):
                stem = p.name[:-4]
            elif p.is_dir():
                stem = p.name
            else:
                continue
            info = {"name": stem, "version": None, "description": None,
                    "authors": None, "loader": None, "_icon": None}
        else:
            if p.is_file() and lower.endswith(".zip"):
                stem = p.name[:-4]
            elif p.is_dir():
                stem = p.name
            else:
                continue
            info = read_resourcepack(p)
        items.append({
            "file": p.name,
            "name": info["name"] or stem,
            "version": info["version"],
            "description": info["description"],
            "authors": info["authors"],
            "loader": info["loader"],
            "has_icon": bool(info["_icon"]),
            "enabled": enabled,
        })
    items.sort(key=lambda i: i["name"].lower())
    return items


def read_content_icon(instance_name: str, kind: str, filename: str):
    if kind == "shaderpacks":
        return None
    folder = _content_folder(instance_name, kind)
    path = folder / Path(filename).name
    if not path.exists() or not is_within(folder, path):
        return None
    info = read_mod_jar(path) if kind == "mods" else read_resourcepack(path)
    icon_path = info["_icon"]
    if not icon_path:
        return None
    data = None
    try:
        if path.is_dir():
            f = path / icon_path
            data = f.read_bytes() if f.is_file() and is_within(path, f) else None
        else:
            with zipfile.ZipFile(path) as zf:
                data = _zip_read(zf, icon_path)
    except Exception:
        return None
    if not data or len(data) > 320_000:
        return None
    if data.startswith(b"\x89PNG"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    else:
        return None
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
