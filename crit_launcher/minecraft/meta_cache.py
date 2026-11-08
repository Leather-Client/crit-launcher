"""Cached version manifests and loader metadata."""

import json
import re
import threading
import urllib.error
import urllib.parse

from ..config.paths import VERSIONS_DIR
from ..core.http import http_get_json, http_get_text
from ..core.logs import log_exception
from .constants import (
    FABRIC_GAME_LIST_URL,
    FABRIC_LOADER_LIST_URL,
    FABRIC_PROFILE_URL_TMPL,
    FORGE_METADATA_URL,
    NEOFORGE_LEGACY_MC,
    NEOFORGE_LEGACY_METADATA_URL,
    NEOFORGE_METADATA_URL,
    VERSION_MANIFEST_URL,
)
from .loaders import neoforge_version_to_mc


class MetaCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.manifest = None
        self.fabric_loaders = None
        self.fabric_loaders_by_mc = {}
        self.fabric_games = None
        self.forge_all = None
        self.neoforge_all = None

    def get_manifest(self, force=False):
        with self._lock:
            if self.manifest is not None and not force:
                return self.manifest
        manifest = None
        try:
            manifest = http_get_json(VERSION_MANIFEST_URL)
        except Exception:
            log_exception("Failed to download version manifest")
            cache_file = VERSIONS_DIR / "version_manifest_v2.json"
            if cache_file.exists():
                try:
                    manifest = json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:
                    log_exception("Failed to read cached manifest")
        if manifest is None:
            raise RuntimeError("Failed to load versions.")
        try:
            (VERSIONS_DIR / "version_manifest_v2.json").write_text(
                json.dumps(manifest), encoding="utf-8")
        except OSError:
            pass
        with self._lock:
            self.manifest = manifest
        return manifest


    def get_fabric_loaders(self, mc_version=None, force=False):
        with self._lock:
            if mc_version:
                if mc_version in self.fabric_loaders_by_mc and not force:
                    return self.fabric_loaders_by_mc[mc_version]
            elif self.fabric_loaders is not None and not force:
                return self.fabric_loaders
        try:
            if mc_version:
                try:
                    loaders = http_get_json(
                        f"{FABRIC_LOADER_LIST_URL}/{urllib.parse.quote(mc_version)}")
                except urllib.error.HTTPError:
                    loaders = []
            else:
                loaders = http_get_json(FABRIC_LOADER_LIST_URL)
        except Exception:
            log_exception("Failed to download fabric loader list")
            raise RuntimeError("Fabric loader could not be retrieved.")
        with self._lock:
            if mc_version:
                self.fabric_loaders_by_mc[mc_version] = loaders
            else:
                self.fabric_loaders = loaders
        return loaders

    def get_fabric_game_versions(self, force=False):
        with self._lock:
            if self.fabric_games is not None and not force:
                return self.fabric_games
        try:
            games = http_get_json(FABRIC_GAME_LIST_URL)
        except Exception:
            log_exception("Failed to download fabric game list")
            raise RuntimeError("Fabric versions could not be retrieved.")
        versions = []
        for g in games:
            v = g.get("version")
            if not v:
                continue
            base = v.split("+")[0]
            if base not in versions:
                versions.append(base)
        with self._lock:
            self.fabric_games = versions
        return versions

    def get_fabric_profile(self, mc_version: str, loader_version: str) -> dict:
        cache_path = VERSIONS_DIR / f"fabric-{loader_version}-{mc_version}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        url = FABRIC_PROFILE_URL_TMPL.format(
            mc=urllib.parse.quote(mc_version), loader=urllib.parse.quote(loader_version))
        data = http_get_json(url)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data


    def _forge_all_versions(self, force=False):
        with self._lock:
            if self.forge_all is not None and not force:
                return self.forge_all
        xml_text = http_get_text(FORGE_METADATA_URL)
        all_versions = re.findall(r"<version>([^<]+)</version>", xml_text)
        with self._lock:
            self.forge_all = all_versions
        return all_versions

    def get_forge_versions(self, mc_version: str, force=False):
        all_versions = self._forge_all_versions(force)
        prefix = mc_version + "-"
        matches = [v[len(prefix):] for v in all_versions if v.startswith(prefix)]
        matches.reverse()
        return matches

    def get_forge_mc_versions(self, force=False):
        seen, out = set(), []
        for v in self._forge_all_versions(force):
            mc = v.split("-", 1)[0]
            if mc and mc not in seen:
                seen.add(mc)
                out.append(mc)
        return out


    def _neoforge_all_versions(self, force=False):
        with self._lock:
            if self.neoforge_all is not None and not force:
                return self.neoforge_all
        entries = []
        try:
            legacy_xml = http_get_text(NEOFORGE_LEGACY_METADATA_URL)
            prefix = NEOFORGE_LEGACY_MC + "-"
            for v in re.findall(r"<version>([^<]+)</version>", legacy_xml):
                if v.startswith(prefix):
                    entries.append((NEOFORGE_LEGACY_MC, v[len(prefix):]))
        except Exception:
            log_exception("Failed to download legacy NeoForge metadata")
        xml_text = http_get_text(NEOFORGE_METADATA_URL)
        for v in re.findall(r"<version>([^<]+)</version>", xml_text):
            mc = neoforge_version_to_mc(v)
            if mc and mc != NEOFORGE_LEGACY_MC:
                entries.append((mc, v))
        with self._lock:
            self.neoforge_all = entries
        return entries

    def get_neoforge_versions(self, mc_version: str, force=False):
        matches = [lv for mc, lv in self._neoforge_all_versions(force) if mc == mc_version]
        matches.reverse()
        return matches

    def get_neoforge_mc_versions(self, force=False):
        seen, out = set(), []
        for mc, _lv in self._neoforge_all_versions(force):
            if mc not in seen:
                seen.add(mc)
                out.append(mc)
        return out

    def get_version_json(self, version_id: str, url: str) -> dict:
        cache_path = VERSIONS_DIR / version_id / f"{version_id}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        data = http_get_json(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data


meta_cache = MetaCache()
_meta_lock = threading.RLock()
