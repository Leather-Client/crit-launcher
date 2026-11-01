# version 1.7 dont steal my shi
import os
import sys
import base64
import binascii
import struct
import re
import json
import uuid
import time
import shutil
import hashlib
import logging
import zipfile
import platform
import subprocess
import tarfile
import threading
import traceback
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


RESOURCE_DIR = _resource_dir()
APP_DIR = _app_dir()

IS_LINUX = sys.platform.startswith("linux")


def _default_data_dir() -> Path:
    """Windows keeps data beside the exe. On Linux the app often lives in a read-only
    AppImage mount, so data goes to the XDG data directory instead."""
    if IS_LINUX:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / "crit-launcher"
    return APP_DIR / "launcher_data"


DATA_DIR = Path(os.environ.get("CRIT_LAUNCHER_DATA", str(_default_data_dir())))


def _clean_child_env(env=None) -> dict:
    """Environment for programs we start (Java, xdg-open, ...).

    A PyInstaller/AppImage build points LD_LIBRARY_PATH at its own bundled libraries,
    which can break system programs and Minecraft's natives. Restore the original value.
    """
    base = dict(os.environ if env is None else env)
    if "LD_LIBRARY_PATH_ORIG" in base:
        base["LD_LIBRARY_PATH"] = base["LD_LIBRARY_PATH_ORIG"]
    else:
        base.pop("LD_LIBRARY_PATH", None)
    base.pop("LD_LIBRARY_PATH_ORIG", None)
    meipass = str(getattr(sys, "_MEIPASS", ""))
    for key in list(base):
        value = base[key]
        if key in ("_MEIPASS2", "PYINSTALLER_RESET_ENVIRONMENT") or (
                key.startswith(("QT_", "QTWEBENGINE_", "QML")) and meipass and meipass in value):
            base.pop(key, None)
    return base


if IS_LINUX and getattr(sys, "frozen", False):
    _RealPopen = subprocess.Popen

    class _CleanEnvPopen(_RealPopen):
        # Qt WebEngine starts its own helper processes natively, so this only affects
        # programs launched from Python.
        def __init__(self, *args, env=None, **kwargs):
            super().__init__(*args, env=_clean_child_env(env), **kwargs)

    subprocess.Popen = _CleanEnvPopen
INSTANCES_DIR = DATA_DIR / "instances"
HORIZON_DATA_DIR = DATA_DIR / "horizon"
VERSIONS_DIR = DATA_DIR / "versions"
LIBRARIES_DIR = DATA_DIR / "libraries"
ASSETS_DIR = DATA_DIR / "assets"
ASSET_OBJECTS_DIR = ASSETS_DIR / "objects"
ASSET_INDEXES_DIR = ASSETS_DIR / "indexes"
RUNTIME_DIR = DATA_DIR / "runtime"
TMP_DIR = DATA_DIR / "tmp"
SKINS_DIR = DATA_DIR / "skins"
INSTANCE_ICONS_DIR = DATA_DIR / "instance_icons"


PAGE_DIR = DATA_DIR / "web" if getattr(sys, "frozen", False) else RESOURCE_DIR

ACCOUNTS_FILE = DATA_DIR / "accounts.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "launcher.log"

for _d in (DATA_DIR, INSTANCES_DIR, VERSIONS_DIR, LIBRARIES_DIR, ASSETS_DIR,
           ASSET_OBJECTS_DIR, ASSET_INDEXES_DIR, RUNTIME_DIR, TMP_DIR, SKINS_DIR,
           INSTANCE_ICONS_DIR, PAGE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def log_exception(context: str):
    logging.error("%s\n%s", context, traceback.format_exc())


VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

FABRIC_LOADER_LIST_URL = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_GAME_LIST_URL = "https://meta.fabricmc.net/v2/versions/game"
FABRIC_PROFILE_URL_TMPL = "https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/profile/json"

FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge/"
FORGE_METADATA_URL = FORGE_MAVEN + "maven-metadata.xml"

NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
NEOFORGE_METADATA_URL = NEOFORGE_MAVEN + "maven-metadata.xml"
NEOFORGE_LEGACY_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/forge/"
NEOFORGE_LEGACY_METADATA_URL = NEOFORGE_LEGACY_MAVEN + "maven-metadata.xml"
NEOFORGE_LEGACY_MC = "1.20.1"

CONSOLE_TAIL_BYTES = 400_000
CONSOLE_CHUNK_BYTES = 262_144

MOD_LOADERS = ("fabric", "forge", "neoforge")
LOADER_NICE = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "horizon": "Horizon"}




HORIZON_REPO = "horizon-rust/horizon"
HORIZON_API = "https://api.github.com/repos/" + HORIZON_REPO
HORIZON_MARKER = "launcher-meta.json"
HORIZON_SUMS = "SHA256SUMS"
HORIZON_SIG = "SHA256SUMS.sig"

HORIZON_TRUSTED_KEYS = ("MU4oiIQcCxkZvbwSjjXs7SySCuM6ywRQ3nGaJYZG0gw=",)
HORIZON_MC_VERSION = "26.3"
HORIZON_JAVA_MAJOR = 25


def neoforge_version_to_mc(version: str):
    if "+snapshot" in version:
        return None
    parts = version.split("-")[0].split(".")
    if len(parts) < 3 or not all(p.isdigit() for p in parts):
        return None
    major = int(parts[0])
    if major >= 26:
        return f"{parts[0]}.{parts[1]}" if parts[2] == "0" else f"{parts[0]}.{parts[1]}.{parts[2]}"
    if major < 20:
        return None
    return f"1.{major}" if parts[1] == "0" else f"1.{major}.{parts[1]}"


def neoforge_installer_info(mc_version: str, loader_version: str):
    if mc_version == NEOFORGE_LEGACY_MC:
        full = f"{mc_version}-{loader_version}"
        return full, f"{NEOFORGE_LEGACY_MAVEN}{full}/forge-{full}-installer.jar"
    return loader_version, f"{NEOFORGE_MAVEN}{loader_version}/neoforge-{loader_version}-installer.jar"

JAVA_RUNTIME_INDEX_URL = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                          "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")

MODRINTH_API = "https://api.modrinth.com/v2"



CURSEFORGE_API = "https://cf-api.skibidiscripts.workers.dev/v1"
CF_GAME_ID = 432                       
CF_CLASS_IDS = {"mod": 6, "resourcepack": 12, "shader": 6552, "modpack": 4471}
CF_LOADER_IDS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}

CF_SORT = {"relevance": 2, "downloads": 6, "follows": 2, "newest": 11, "updated": 3}
CF_REQUIRED_RELATION = 3

MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_SKINS_URL = "https://api.minecraftservices.com/minecraft/profile/skins"
MC_CAPE_URL = "https://api.minecraftservices.com/minecraft/profile/capes/active"
MOJANG_LOOKUP_URL = "https://api.mojang.com/users/profiles/minecraft/"
SESSION_PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/"

MS_CLIENT_ID = "00000000402b5328"
MS_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
MS_REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
MS_AUTHORIZE_URL = "https://login.live.com/oauth20_authorize.srf"
MS_TOKEN_URL = "https://login.live.com/oauth20_token.srf"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_XBOX_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"

RESOURCES_BASE_URL = "https://resources.download.minecraft.net"
LIBRARIES_BASE_URL = "https://libraries.minecraft.net/"

USER_AGENT = "CritLauncher/1.0 (+https://critlauncher.xyz)"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

INSTALL_SCHEMA = 2

DEFAULT_JAVA_INFO = {"component": "jre-legacy", "majorVersion": 8}

ADDON_TYPES = ("mod", "resourcepack", "modpack", "shader", "datapack")

INSTANCE_ICONS = {"box", "shirt", "cloth", "scroll", "flame", "hammer", "circle", "triangle", "star",
                  "hexagon", "diamond", "heart", "cat", "ghost", "zap", "gem", "critclient", "oneclient"}

_INVALID_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def http_get_json(url: str, timeout: int = 20, retries: int = 2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404):
                raise
            last = exc
        except Exception as exc:
            last = exc
        time.sleep(0.8 * (attempt + 1))
    raise last


def http_get_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _json_or_text(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", "replace")[:300]}


def http_get_auth(url: str, token: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _json_or_text(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json_or_text(exc.read())


def http_download(url: str, dest: Path, expected_size=None, expected_sha1=None,
                  progress_cb=None, chunk_size: int = 65536, user_agent=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": user_agent or USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            if progress_cb:
                progress_cb(len(chunk))
    if expected_sha1:
        if file_sha1(tmp) != expected_sha1:
            tmp.unlink(missing_ok=True)
            raise IOError(f"Checksum mismatch for {dest.name}")
    os.replace(tmp, dest)


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def file_is_valid(path: Path, expected_size=None, expected_sha1=None) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        if expected_size is not None and path.stat().st_size != expected_size:
            return False
        if expected_sha1 is not None:
            return file_sha1(path) == expected_sha1
        return path.stat().st_size > 0
    except OSError:
        return False


def is_within(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def sanitize_instance_name(name: str) -> str:
    name = (name or "").strip().rstrip(".")
    if not name:
        raise ValueError("Instance name cannot be empty.")
    if len(name) > 64:
        raise ValueError("Instance name is too long.")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError("Instance name contains invalid characters.")
    if os.path.isabs(name):
        raise ValueError("Instance name cannot be an absolute path.")
    if _INVALID_NAME_RE.search(name):
        raise ValueError('Instance name cannot contain: < > : " / \\ | ? *')
    return name


def offline_uuid(username: str) -> str:
    data = ("OfflinePlayer:" + username).encode("utf-8")
    digest = bytearray(hashlib.md5(data).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def current_os_name() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "osx"
    return "linux"


def current_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "64"
    if machine in ("x86", "i386", "i686"):
        return "32"
    if "arm64" in machine or "aarch64" in machine:
        return "arm64"
    return "64"


def classpath_sep() -> str:
    return ";" if current_os_name() == "windows" else ":"


def open_in_file_manager(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if current_os_name() == "windows":
        os.startfile(str(path))
    elif current_os_name() == "osx":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19   
DWMWA_USE_IMMERSIVE_DARK_MODE = 20       
DWMWA_BORDER_COLOR = 34                  
DWMWA_CAPTION_COLOR = 35                 
DWMWA_TEXT_COLOR = 36                    
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  


TITLEBAR_FALLBACKS = {
    ("dark", "default"): ("#0b0c0e", "#e8eaed"),
    ("dark", "modrinth"): ("#141518", "#ecf0f3"),
    ("dark", "fastclient"): ("#0b0c0f", "#f1f2f4"),   
    ("dark", "sklauncher"): ("#0a0d14", "#f4f4f5"),
    ("dark", "minecraft"): ("#0b0c0e", "#e8eaed"),
    ("oled", "default"): ("#000000", "#e8eaed"),
    ("oled", "modrinth"): ("#000000", "#ecf0f3"),
    ("light", "default"): ("#f2f4f7", "#1b1f24"),
    ("light", "modrinth"): ("#f2f4f7", "#1b1f24"),
}


import ctypes as _ct


class _RECT(_ct.Structure):
    _fields_ = [("left", _ct.c_long), ("top", _ct.c_long), ("right", _ct.c_long), ("bottom", _ct.c_long)]


class _NCCALCSIZE_PARAMS(_ct.Structure):
    _fields_ = [("rgrc", _RECT * 3), ("lppos", _ct.c_void_p)]


WM_NCCALCSIZE = 0x0083
WM_NCDESTROY = 0x0082
WM_NCLBUTTONDOWN = 0x00A1
WM_CLOSE = 0x0010
HTCAPTION = 2
GWLP_WNDPROC = -4
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9


def _colorref(hex_color: str) -> int:
    h = str(hex_color).strip().lstrip("#")
    if len(h) == 3:                      
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)      


def _dwm_set_attr(hwnd, attr, value):
    import ctypes
    v = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v))









UI_ASSETS_DIR = (DATA_DIR / "ui_assets") if IS_LINUX else (APP_DIR / "assets")
_ASSETS_RAW = "https://raw.githubusercontent.com/snigglaberry/crit-launcher/main/assets/"
HOME_BG_ASSETS = {                       
    "dark_bg.png": _ASSETS_RAW + "dark_Bg.png",
    "light_bg.png": _ASSETS_RAW + "light_bg.png",
}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_assets_lock = threading.Lock()






_ASSET_SEARCH_ROOTS = (UI_ASSETS_DIR, RESOURCE_DIR / "assets", APP_DIR, RESOURCE_DIR)
_ASSET_ALIASES = {"dark_bg.png": "dark_Bg.png"}   


def _find_asset(rel: str, min_size: int = 1):
    """First existing copy of rel in any known asset folder, or None."""
    rel = str(rel).replace("\\", "/")
    head, sep, tail = rel.rpartition("/")
    names = [rel]
    alt = _ASSET_ALIASES.get(tail)
    if alt:
        names.append(head + sep + alt)
    for root in _ASSET_SEARCH_ROOTS:
        for name in names:
            try:
                p = root / name
                if p.is_file() and p.stat().st_size >= min_size:
                    return p
            except OSError:
                continue
    return None


def _publish_to_page_dir(rel: str, src: Path) -> bool:
    """Copy an asset into <page>/assets so the page's relative assets/... URLs resolve in a build.

    Compiled builds unpack index.html into a temp folder, so without this the only way to show the
    art is a huge base64 data URI over the JS bridge, which large backgrounds don't survive.
    """
    try:
        if PAGE_DIR == UI_ASSETS_DIR.parent or not src.is_file():
            return False
        dest = PAGE_DIR / "assets" / rel
        try:
            if dest.is_file() and dest.stat().st_size == src.stat().st_size:
                return True
        except OSError:
            pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return True
    except OSError:
        log_exception("publish asset " + rel)
        return False


def _seed_local_asset(rel: str, dest: Path, min_size: int = 1) -> bool:
    """Copy a shipped asset (bundled/extra assets folder) into dest when it isn't there yet."""
    try:
        if dest.is_file() and dest.stat().st_size >= min_size:
            return True
    except OSError:
        pass
    found = _find_asset(rel, min_size)
    if not found:
        return False
    try:
        if found.resolve() == dest.resolve():
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(found, dest)
        return dest.is_file() and dest.stat().st_size >= min_size
    except OSError:
        log_exception("copy bundled asset " + rel)
        return False


def _download_asset(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if not data.startswith(_PNG_MAGIC):
            return False
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except Exception:
        logging.warning("Couldn't download %s", url)
        return False


def ensure_home_assets() -> dict:
    """Find/copy/download the home art, return {"success": bool, "files": {name: data-URI}}."""
    with _assets_lock:
        try:
            UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": str(e), "files": {}}
        files = {}
        ok = True
        for name, url in HOME_BG_ASSETS.items():
            path = UI_ASSETS_DIR / name
            
            if not _seed_local_asset(name, path):
                _download_asset(url, path)
            try:
                ready = path.is_file() and path.stat().st_size > 0
            except OSError:
                ready = False
            if not ready:
                ok = False
                continue
            published = _publish_to_page_dir(name, path)
            data = path.read_bytes()
            
            
            if not published or len(data) <= 1_500_000:
                files[name] = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        return {"success": ok, "files": files}


def prepare_page() -> Path:
    """Return the index.html to load. Compiled builds get a fresh copy in PAGE_DIR so assets/ sits beside it."""
    src = RESOURCE_DIR / "index.html"
    if PAGE_DIR == RESOURCE_DIR:
        return src
    dest = PAGE_DIR / "index.html"
    try:
        PAGE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest
    except OSError:
        log_exception("prepare_page")
        return src


def start_asset_download() -> None:
    """Create assets/ right away and fetch whatever is missing in the background."""
    try:
        UI_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    if any(not _seed_local_asset(n, UI_ASSETS_DIR / n) for n in HOME_BG_ASSETS):
        threading.Thread(target=ensure_home_assets, daemon=True).start()
    if any(not _seed_local_asset("panorama/" + n, UI_ASSETS_DIR / "panorama" / n, _MIN_PANO_BYTES)
           for n in MC_PANO_FILES):
        threading.Thread(target=ensure_mc_panorama, daemon=True).start()
    
    for name in HOME_BG_ASSETS:
        _publish_to_page_dir(name, UI_ASSETS_DIR / name)
    for name in MC_PANO_FILES:
        _publish_to_page_dir("panorama/" + name, UI_ASSETS_DIR / "panorama" / name)


MC_TEX_DIR = UI_ASSETS_DIR / "mc"
_MC_SPRITES = "assets/minecraft/textures/gui/sprites/widget/"
MC_TEX_MEMBERS = {
    "button.png": _MC_SPRITES + "button.png",
    "button_highlighted.png": _MC_SPRITES + "button_highlighted.png",
    "button_disabled.png": _MC_SPRITES + "button_disabled.png",
    "minecraft.png": "assets/minecraft/textures/gui/title/minecraft.png",
}
_MC_REQUIRED = ("button.png",)




MC_PANO_DIR = UI_ASSETS_DIR / "panorama"
MC_PANO_FILES = ["panorama_%d.png" % i for i in range(6)]
MC_PANO_ASSETS = {n: _ASSETS_RAW + n for n in MC_PANO_FILES}
_MIN_PANO_BYTES = 4096                    


def ensure_mc_panorama() -> dict:
    """Make sure all six panorama faces are in assets/panorama/, downloading any that are missing."""
    with _mc_tex_lock:
        try:
            MC_PANO_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": str(e)}
        ok = True
        files = {}
        for name, url in MC_PANO_ASSETS.items():
            path = MC_PANO_DIR / name
            if not _seed_local_asset("panorama/" + name, path, _MIN_PANO_BYTES):
                _download_asset(url, path)
            if path.exists() and path.stat().st_size >= _MIN_PANO_BYTES:
                _publish_to_page_dir("panorama/" + name, path)
                
                files[name] = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            else:
                ok = False
        return {"success": ok, "dir": str(MC_PANO_DIR), "files": files}
_mc_tex_lock = threading.Lock()


def _mc_missing() -> list:
    return [n for n in MC_TEX_MEMBERS if not (MC_TEX_DIR / n).exists() or (MC_TEX_DIR / n).stat().st_size == 0]


def _mc_extract(jar: Path, missing: list) -> None:
    try:
        with zipfile.ZipFile(jar) as zf:
            names = set(zf.namelist())
            for n in list(missing):
                member = MC_TEX_MEMBERS[n]
                if member not in names:
                    continue
                data = zf.read(member)
                if data.startswith(_PNG_MAGIC):
                    (MC_TEX_DIR / n).write_bytes(data)
                    missing.remove(n)
    except Exception:
        logging.warning("Couldn't read textures from %s", jar)


def _mc_download_client():
    try:
        manifest = http_get_json(VERSION_MANIFEST_URL)
        latest = manifest["latest"]["release"]
        entry = next(v for v in manifest["versions"] if v["id"] == latest)
        url = http_get_json(entry["url"])["downloads"]["client"]["url"]
        dest = MC_TEX_DIR / "_client.part"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return dest
    except Exception:
        log_exception("_mc_download_client")
        return None


def ensure_mc_textures() -> dict:
    with _mc_tex_lock:
        try:
            MC_TEX_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"success": False, "error": str(e), "files": {}}
        missing = _mc_missing()
        
        for name in list(missing):
            if _seed_local_asset("mc/" + name, MC_TEX_DIR / name):
                missing.remove(name)
        if missing:
            try:
                jars = sorted((p for p in VERSIONS_DIR.glob("*/*.jar") if p.parent.name == p.stem),
                              key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                jars = []
            for jar in jars:
                _mc_extract(jar, missing)
                if not missing:
                    break
        if missing:
            part = _mc_download_client()
            if part:
                try:
                    _mc_extract(part, missing)
                finally:
                    try:
                        part.unlink()
                    except OSError:
                        pass
        files = {}
        for n in MC_TEX_MEMBERS:
            p = MC_TEX_DIR / n
            if p.exists() and p.stat().st_size > 0:
                _publish_to_page_dir("mc/" + n, p)
                files[n] = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        return {"success": all(n in files for n in _MC_REQUIRED), "files": files}


class JsonStore:
    def __init__(self, path: Path, default):
        self.path = path
        self._lock = threading.RLock()
        self._default = default
        if not self.path.exists():
            self._write(default)

    def _write(self, data):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return json.loads(json.dumps(self._default))

    def read(self):
        with self._lock:
            return self._read()

    def write(self, data):
        with self._lock:
            self._write(data)

    def update(self, mutator):
        with self._lock:
            data = self._read()
            result = mutator(data)
            if result is not None:
                data = result
            self._write(data)
            return data


accounts_store = JsonStore(ACCOUNTS_FILE, [])
THEMES = ("green", "blue", "purple", "red", "orange", "pink", "custom")
UI_STYLES = ("default", "minecraft", "modrinth", "fastclient", "sklauncher")
ADDON_VIEWS = ("browse", "installed")
ADDON_SOURCES = ("modrinth", "curseforge")
COLOR_MODES = ("dark", "light", "oled")
CATALOG_SOURCES = ("labymod", "critical")

DEFAULT_SETTINGS = {
    "active_account": None,
    "active_instance": None,
    "memory_mb": 2048,
    "min_memory_mb": 0,
    "java_path": None,
    "jvm_args": "",
    "game_width": 0,
    "game_height": 0,
    "fullscreen": False,
    "custom_accent": "#4ade80",
    "recent_accents": [],
    "reduce_motion": False,
    "bg_pattern": True,
    "sidebar_collapsed": False,
    "theme": "green",
    "ui_style": "default",
    "addon_view": "browse",
    "addon_instance": None,
    "color_mode": "dark",
    "catalog_source": "labymod",
    "addon_source": "modrinth",
    "agreement_accepted": False,
    "agreement_version": 0,
    "server_beta_seen": False,
    "local_skin_warning_seen": [],
    "playtime_seconds": 0,
    "playtime_days": {},
}
settings_store = JsonStore(SETTINGS_FILE, DEFAULT_SETTINGS)


def _clean_hex(value, default="#4ade80"):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        return value.strip().lower()
    return default



def _arch_matches(rule_arch: str) -> bool:
    cur = current_arch()
    if rule_arch == "x86":
        return cur == "32"
    if rule_arch in ("arm64", "aarch64"):
        return cur == "arm64"
    if rule_arch in ("x86_64", "amd64", "x64"):
        return cur == "64"
    return True


def rule_matches(rule: dict, features: dict) -> bool:
    os_rule = rule.get("os")
    if os_rule:
        name = os_rule.get("name")
        if name and name != current_os_name():
            return False
        arch = os_rule.get("arch")
        if arch and not _arch_matches(arch):
            return False
    features_rule = rule.get("features")
    if features_rule:
        for key, expected in features_rule.items():
            if bool(features.get(key, False)) != bool(expected):
                return False
    return True


def rules_allow(rules, features=None) -> bool:
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        if rule_matches(rule, features):
            allowed = rule.get("action", "disallow") == "allow"
    return allowed


def substitute(template: str, replacements: dict) -> str:
    def repl(match):
        key = match.group(1)
        val = replacements.get(key)
        return str(val) if val is not None else match.group(0)
    return re.sub(r"\$\{([a-zA-Z0-9_.]+)\}", repl, template)


def resolve_argument_list(spec_list, features, replacements) -> list:
    out = []
    for item in spec_list:
        if isinstance(item, str):
            out.append(substitute(item, replacements))
        elif isinstance(item, dict):
            if rules_allow(item.get("rules", []), features):
                value = item.get("value", "")
                if isinstance(value, list):
                    out.extend(substitute(v, replacements) for v in value)
                else:
                    out.append(substitute(value, replacements))
    return out


def merge_arguments(parent, child):
    if not parent and not child:
        return None
    parent = parent or {}
    child = child or {}
    return {
        "jvm": list(parent.get("jvm", [])) + list(child.get("jvm", [])),
        "game": list(parent.get("game", [])) + list(child.get("game", [])),
    }


DEFAULT_MODERN_JVM_ARGS = [
    "-Djava.library.path=${natives_directory}",
    "-Dminecraft.launcher.brand=crit-launcher",
    "-Dminecraft.launcher.version=1.0",
    "-cp",
    "${classpath}",
]


_JAVA_PROBE_CACHE: dict = {}
_JAVA_PROBE_LOCK = threading.Lock()
_JAVA_UI_CACHE = {"at": 0.0, "value": (None, None)}


def console_java(path: str) -> str:
    p = Path(path)
    if p.name.lower() == "javaw.exe":
        sibling = p.with_name("java.exe")
        if sibling.exists():
            return str(sibling)
    return str(path)


def _parse_java_major(text: str):
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if m:
        major = int(m.group(1))
        if major == 1 and m.group(2):
            major = int(m.group(2))
        return major
    return None


def probe_java(path: str):
    exe = console_java(path)
    with _JAVA_PROBE_LOCK:
        if exe in _JAVA_PROBE_CACHE:
            return _JAVA_PROBE_CACHE[exe]
    result = (None, None)
    try:
        proc = subprocess.run(
            [exe, "-version"], capture_output=True, text=True, errors="replace",
            timeout=15, creationflags=CREATE_NO_WINDOW,
        )
        text = (proc.stderr or proc.stdout or "")
        lines = text.splitlines()
        result = (_parse_java_major(text), lines[0] if lines else None)
    except Exception:
        result = (None, None)
    with _JAVA_PROBE_LOCK:
        _JAVA_PROBE_CACHE[exe] = result
    return result


def java_compatible(major, required: int) -> bool:
    if major is None:
        return True
    if required <= 8:
        return major == 8
    return major >= required


def _java_exe_names():
    return ("java.exe",) if current_os_name() == "windows" else ("java",)


def find_system_javas() -> list:
    found = []
    seen = set()

    def add(p):
        try:
            p = Path(p)
            if not p.is_file():
                return
            key = str(p.resolve())
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        found.append(str(p))

    for exe in ("java", "java.exe"):
        w = shutil.which(exe)
        if w:
            add(w)

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        for n in _java_exe_names():
            add(Path(java_home) / "bin" / n)

    home = Path.home()
    system = current_os_name()
    globs = []
    if system == "windows":
        vendors = ["Java", "Eclipse Adoptium", "Eclipse Foundation", "Microsoft", "Zulu",
                   "BellSoft", "Amazon Corretto", "Semeru", "AdoptOpenJDK", "OpenJDK"]
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                for v in vendors:
                    globs.append((Path(base) / v, "*/bin"))
        appdata = os.environ.get("APPDATA")
        if appdata:
            globs.append((Path(appdata) / ".minecraft" / "runtime", "*/*/*/bin"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            globs.append((Path(local) / "Packages", "Microsoft.4297127D64EC6_*/LocalCache/Local/runtime/*/*/*/bin"))
    elif system == "osx":
        globs.append((Path("/Library/Java/JavaVirtualMachines"), "*/Contents/Home/bin"))
        globs.append((home / "Library/Application Support/minecraft/runtime", "*/*/*/jre.bundle/Contents/Home/bin"))
    else:
        globs.append((Path("/usr/lib/jvm"), "*/bin"))
        globs.append((home / ".sdkman/candidates/java", "*/bin"))
        globs.append((home / ".jdks", "*/bin"))
        globs.append((home / ".minecraft/runtime", "*/*/*/bin"))

    for base, pattern in globs:
        try:
            if not base.exists():
                continue
            for bin_dir in base.glob(pattern):
                for n in _java_exe_names():
                    add(bin_dir / n)
        except OSError:
            pass
    return found


def mojang_runtime_platform() -> str:
    system = current_os_name()
    arch = current_arch()
    if system == "windows":
        return {"64": "windows-x64", "32": "windows-x86", "arm64": "windows-arm64"}.get(arch, "windows-x64")
    if system == "osx":
        return "mac-os-arm64" if arch == "arm64" else "mac-os"
    return "linux-i386" if arch == "32" else "linux"


def managed_java_path(component: str):
    root = RUNTIME_DIR / component
    if not (root / ".crit_installed").exists():
        return None
    for rel in ("bin/java.exe", "bin/java", "jre.bundle/Contents/Home/bin/java"):
        p = root / rel
        if p.exists():
            return str(p)
    return None


def install_managed_java(component: str, tracker):
    tracker = tracker or ProgressTracker(lambda *_: None)
    tracker.set_task("Looking up Java runtime", force=True)
    plat = mojang_runtime_platform()
    index = http_get_json(JAVA_RUNTIME_INDEX_URL, timeout=30)
    entries = (index.get(plat) or {}).get(component) or []
    if not entries:
        raise RuntimeError(
            f"Mojang has no '{component}' Java runtime for {plat}. Install a matching Java "
            "yourself and set its path in Settings.")
    manifest = http_get_json(entries[0]["manifest"]["url"], timeout=30)
    files = manifest.get("files", {})

    root = RUNTIME_DIR / component
    root.mkdir(parents=True, exist_ok=True)

    items, executables, links = [], [], []
    total = 0
    for rel, info in files.items():
        target = root / rel
        if not is_within(root, target):
            continue
        kind = info.get("type")
        if kind == "directory":
            target.mkdir(parents=True, exist_ok=True)
        elif kind == "file":
            raw = (info.get("downloads") or {}).get("raw")
            if not raw:
                continue
            items.append(({"path": rel, "url": raw["url"], "size": raw.get("size"),
                           "sha1": raw.get("sha1")}, root))
            total += raw.get("size") or 0
            if info.get("executable"):
                executables.append(target)
        elif kind == "link":
            links.append((target, info.get("target")))

    tracker.reset(max(total, 1))
    tracker.set_task(f"Downloading Java runtime ({component})", force=True)
    download_all(items, tracker, workers=12)

    if current_os_name() != "windows":
        for target in executables:
            try:
                os.chmod(target, 0o755)
            except OSError:
                pass
        for target, link_to in links:
            try:
                if link_to and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(link_to, target)
            except OSError:
                pass

    (root / ".crit_installed").write_text(str(time.time()), encoding="utf-8")
    path = managed_java_path(component)
    if not path:
        raise RuntimeError("Java runtime was downloaded but no java executable was found in it.")
    return path


def java_info_from_meta(meta: dict) -> dict:
    return {
        "component": meta.get("java_component") or DEFAULT_JAVA_INFO["component"],
        "majorVersion": int(meta.get("java_major") or DEFAULT_JAVA_INFO["majorVersion"]),
    }


def resolve_java(java_info, allow_download=False, tracker=None):
    info = java_info or DEFAULT_JAVA_INFO
    required = int(info.get("majorVersion") or 8)
    component = info.get("component") or "jre-legacy"

    configured = (settings_store.read().get("java_path") or "").strip()
    if configured and Path(configured).exists():
        major, _ = probe_java(configured)
        if java_compatible(major, required):
            return configured
        logging.info("Configured Java (%s) is not compatible with Java %s", major, required)

    managed = managed_java_path(component)
    if managed:
        return managed

    best = None
    for cand in find_system_javas():
        major, _ = probe_java(cand)
        if major is None or not java_compatible(major, required):
            continue
        if best is None or major < best[0]:
            best = (major, cand)
    if best:
        return best[1]

    if allow_download:
        return install_managed_java(component, tracker)
    return None


def detect_java_for_ui():
    now = time.time()
    if now - _JAVA_UI_CACHE["at"] < 20 and _JAVA_UI_CACHE["at"] > 0:
        return _JAVA_UI_CACHE["value"]
    value = (None, None)
    configured = (settings_store.read().get("java_path") or "").strip()
    if configured and Path(configured).exists():
        value = (configured, probe_java(configured)[1])
    else:
        best = None
        for cand in find_system_javas():
            major, line = probe_java(cand)
            if major is not None and (best is None or major > best[0]):
                best = (major, cand, line)
        if best:
            value = (best[1], best[2])
    _JAVA_UI_CACHE["at"] = now
    _JAVA_UI_CACHE["value"] = value
    return value


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
        versions = [g.get("version") for g in games if g.get("version")]
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


def _dir_size(path: Path) -> int:
    total = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def instance_dir(name: str) -> Path:
    safe = sanitize_instance_name(name)
    return INSTANCES_DIR / safe


def sanitize_custom_icon_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip().rstrip(".")
    name = name[:100].rstrip(" .")
    if not name:
        name = "Image"
    if name.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name


def custom_instance_icon_path(icon_key: str) -> Path | None:
    match = re.fullmatch(r"custom:(.+)", str(icon_key or ""))
    if not match:
        return None
    name = match.group(1)
    if sanitize_custom_icon_name(name) != name:
        return None
    path = INSTANCE_ICONS_DIR / name
    if path.is_file():
        return path
    legacy_path = INSTANCE_ICONS_DIR / f"{name}.png"
    if legacy_path.is_file():
        return legacy_path
    return path


def list_custom_instance_icons() -> list:
    result = []
    paths = []
    for path in INSTANCE_ICONS_DIR.iterdir():
        try:
            paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            log_exception(f"inspect custom instance icon {path.name}")
    for _, path in sorted(paths, key=lambda item: (item[0], item[1].name.lower())):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() == ".tmp":
            continue
        try:
            data = path.read_bytes()
        except OSError:
            log_exception(f"read custom instance icon {path.name}")
            continue
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            logging.error("Invalid custom instance icon file: %s", path.name)
            continue
        name = path.stem if path.suffix.lower() == ".png" else path.name
        if sanitize_custom_icon_name(name) != name:
            logging.error("Invalid custom instance icon filename: %s", path.name)
            continue
        result.append({
            "key": f"custom:{name}",
            "name": name,
            "data": "data:image/png;base64," + base64.b64encode(data).decode("ascii"),
        })
    return result


def read_instance_meta(name: str) -> dict | None:
    meta_path = instance_dir(name) / "instance.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_instance_meta(name: str, meta: dict):
    path = instance_dir(name) / "instance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _meta_lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def update_instance_meta(name: str, mutator):
    with _meta_lock:
        meta = read_instance_meta(name)
        if meta is None:
            return None
        mutator(meta)
        write_instance_meta(name, meta)
        return meta


def is_installed(meta: dict) -> bool:
    return bool(meta.get("installed")) and meta.get("schema") == INSTALL_SCHEMA


def list_instances() -> list:
    result = []
    if not INSTANCES_DIR.exists():
        return result
    for child in sorted(INSTANCES_DIR.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir():
            try:
                meta = read_instance_meta(child.name)
            except ValueError:
                continue
            if meta:
                result.append(meta)
    result.sort(key=lambda m: m["name"].lower())
    return result


def instance_game_dir(name: str) -> Path:
    return instance_dir(name) / "game"


def instance_mods_dir(name: str) -> Path:
    return instance_game_dir(name) / "mods"


def instance_config_dir(name: str) -> Path:
    return instance_game_dir(name) / "config"


def ensure_game_layout(name: str, loader: str):
    game = instance_game_dir(name)
    folders = ["resourcepacks", "shaderpacks", "saves"]
    if loader in MOD_LOADERS:
        folders += ["mods", "config"]
    for f in folders:
        (game / f).mkdir(parents=True, exist_ok=True)


def unique_instance_name(base: str) -> str:
    cleaned = _INVALID_NAME_RE.sub("-", base or "").replace("..", "-").strip().rstrip(".")
    cleaned = (cleaned or "Modpack")[:56]
    candidate = cleaned
    n = 2
    while instance_dir(candidate).exists():
        candidate = f"{cleaned} ({n})"
        n += 1
    return candidate


def _home_dir() -> Path:
    return Path.home()


def _windows_appdata() -> Path:
    env = os.environ.get("APPDATA")
    return Path(env) if env else _home_dir() / "AppData" / "Roaming"


def _launcher_roots(launcher: str) -> list:
    system = current_os_name()
    home = _home_dir()
    if launcher == "prismlauncher":
        if system == "windows":
            return [_windows_appdata() / "PrismLauncher"]
        if system == "osx":
            return [home / "Library" / "Application Support" / "PrismLauncher"]
        return [home / ".local" / "share" / "PrismLauncher",
                home / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher"]
    if launcher == "polymc":
        if system == "windows":
            return [_windows_appdata() / "PolyMC"]
        if system == "osx":
            return [home / "Library" / "Application Support" / "PolyMC"]
        return [home / ".local" / "share" / "PolyMC",
                home / ".var" / "app" / "org.polymc.PolyMC" / "data" / "polymc"]
    if launcher == "sklauncher":
        if system == "windows":
            return [_windows_appdata() / ".sklauncher"]
        if system == "osx":
            return [home / "Library" / "Application Support" / ".sklauncher"]
        return [home / ".sklauncher"]
    if launcher == "fastclient":
        if system == "windows":
            return [_windows_appdata() / ".fastclient"]
        if system == "osx":
            return [home / "Library" / "Application Support" / ".fastclient"]
        return [home / ".fastclient"]
    if launcher == "modrinth":
        if system == "windows":
            return [_windows_appdata() / "ModrinthApp",
                    _windows_appdata() / "com.modrinth.theseus"]
        if system == "osx":
            return [home / "Library" / "Application Support" / "ModrinthApp",
                    home / "Library" / "Application Support" / "com.modrinth.theseus"]
        return [home / ".local" / "share" / "ModrinthApp",
                home / ".local" / "share" / "com.modrinth.theseus"]
    return []


LAUNCHER_LABELS = {
    "prismlauncher": "Prism Launcher",
    "polymc": "PolyMC",
    "sklauncher": "SKLauncher",
    "fastclient": "Fast Client",
    "modrinth": "Modrinth App",
}


def _read_json_safe(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _maven_version(name: str, prefix: str) -> str:
    return name[len(prefix):] if name.startswith(prefix) else ""


def _loader_from_version_json(vjson: dict, mc_version: str):
    for lib in vjson.get("libraries", []):
        lname = lib.get("name", "")
        if lname.startswith("net.fabricmc:fabric-loader:"):
            return "fabric", _maven_version(lname, "net.fabricmc:fabric-loader:")
        if lname.startswith("net.minecraftforge:forge:"):
            full = _maven_version(lname, "net.minecraftforge:forge:")
            prefix = mc_version + "-"
            return "forge", (full[len(prefix):] if full.startswith(prefix) else full)
    vid = vjson.get("id", "")
    m = re.match(r"^fabric-loader-([\w.]+)-", vid)
    if m:
        return "fabric", m.group(1)
    m = re.match(r"^(?:[\w.]+-)?forge-([\w.]+)$", vid)
    if m:
        return "forge", m.group(1)
    return "vanilla", None


def _loader_from_mmc_pack(components: list):
    mc_version = None
    loader, loader_version = "vanilla", None
    for c in components or []:
        uid = c.get("uid", "")
        ver = c.get("version") or c.get("cachedVersion")
        if uid == "net.minecraft":
            mc_version = ver
        elif uid == "net.fabricmc.fabric-loader":
            loader, loader_version = "fabric", ver
        elif uid == "net.minecraftforge":
            loader, loader_version = "forge", ver
        elif uid == "org.quiltmc.quilt-loader":
            loader, loader_version = "quilt", ver
        elif uid == "com.neoforged":
            loader, loader_version = "neoforge", ver
    return mc_version, loader, loader_version


def _count_images(path: Path) -> int:
    if not path.is_dir():
        return 0
    try:
        return sum(1 for p in path.iterdir()
                   if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    except OSError:
        return 0


def _count_dir(path: Path, suffix=None) -> int:
    if not path.is_dir():
        return 0
    try:
        if suffix:
            return sum(1 for p in path.iterdir() if p.is_file() and p.suffix.lower() == suffix)
        return sum(1 for p in path.iterdir())
    except OSError:
        return 0


def _prism_game_dir(instance_dir: Path) -> Path:
    plain = instance_dir / "minecraft"
    dotted = instance_dir / ".minecraft"
    if plain.is_dir():
        return plain
    if dotted.is_dir():
        return dotted
    return plain


def _scan_prismlauncher(root: Path) -> list:
    instances_dir = root / "instances"
    if not instances_dir.is_dir():
        return []
    found = []
    for sub in sorted(instances_dir.iterdir(), key=lambda p: p.name.lower()):
        pack = _read_json_safe(sub / "mmc-pack.json") if sub.is_dir() else None
        if not pack:
            continue
        mc_version, loader, loader_version = _loader_from_mmc_pack(pack.get("components"))
        game_dir = _prism_game_dir(sub)
        found.append({
            "source_id": sub.name,
            "name": sub.name,
            "version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "has_options": (game_dir / "options.txt").is_file(),
            "mods_count": _count_dir(game_dir / "mods", ".jar") if loader in MOD_LOADERS else 0,
            "resourcepacks_count": _count_dir(game_dir / "resourcepacks"),
            "saves_count": _count_dir(game_dir / "saves"),
            "shaderpacks_count": _count_dir(game_dir / "shaderpacks"),
            "schematics_count": _count_dir(game_dir / "schematics"),
            "screenshots_count": _count_images(game_dir / "screenshots"),
            "config_count": _count_dir(game_dir / "config") if loader in MOD_LOADERS else 0,
        })
    return found


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


_SK_LOG_VERSION_RES = (
    re.compile(r"Minecraft Version:\s*(\d[\w.\-]*)"),
    re.compile(r"Loading Minecraft (\d[\w.\-]*)"),
    re.compile(r"--version,?\s*(\d[\w.\-]*)"),
    re.compile(r"Client\s+(\d+\.\d+(?:\.\d+)?)\b"),
)


def _sk_vanilla_version_from_log(logs_dir: Path):
    log_path = logs_dir / "latest.log"
    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i > 80:
                    break
                for rx in _SK_LOG_VERSION_RES:
                    m = rx.search(line)
                    if m:
                        return m.group(1)
    except OSError:
        pass
    return None


def _sk_game_dir(sub: Path):
    for cand in (sub, sub / ".minecraft", sub / "minecraft"):
        if (cand / "logs" / "latest.log").is_file():
            return cand
    return None


def _scan_sklauncher(root: Path) -> list:
    instances_dir = root / "instances"
    if not instances_dir.is_dir():
        return []
    found = []
    for sub in sorted(instances_dir.iterdir(), key=lambda p: p.name.lower()):
        if not sub.is_dir():
            continue
        game_dir = _sk_game_dir(sub)
        if not game_dir:
            continue
        logs_dir = game_dir / "logs"
        log_info = _parse_fastclient_log_line(_read_first_log_line(logs_dir))
        if log_info:
            loader, mc_version, loader_version = log_info
            if not mc_version:
                mc_version = _sk_vanilla_version_from_log(logs_dir)
        else:
            loader, loader_version = "vanilla", None
            mc_version = _sk_vanilla_version_from_log(logs_dir)
        found.append({
            "source_id": sub.name,
            "name": sub.name,
            "version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "has_options": (game_dir / "options.txt").is_file(),
            "mods_count": _count_dir(game_dir / "mods", ".jar") if loader in MOD_LOADERS else 0,
            "resourcepacks_count": _count_dir(game_dir / "resourcepacks"),
            "saves_count": _count_dir(game_dir / "saves"),
            "shaderpacks_count": _count_dir(game_dir / "shaderpacks"),
            "schematics_count": _count_dir(game_dir / "schematics"),
            "screenshots_count": _count_images(game_dir / "screenshots"),
            "config_count": _count_dir(game_dir / "config") if loader in MOD_LOADERS else 0,
        })
    return found


def _scan_modrinth(root: Path) -> list:
    profiles_dir = root / "profiles"
    if not profiles_dir.is_dir():
        return []
    found = []
    for sub in sorted(profiles_dir.iterdir(), key=lambda p: p.name.lower()):
        if not sub.is_dir():
            continue
        logs_dir = sub / "logs"
        has_log = (logs_dir / "latest.log").is_file()
        log_info = _parse_fastclient_log_line(_read_first_log_line(logs_dir)) if has_log else None
        if log_info:
            loader, mc_version, loader_version = log_info
            if not mc_version:
                mc_version = _sk_vanilla_version_from_log(logs_dir)
        elif has_log:
            loader, loader_version = "vanilla", None
            mc_version = _sk_vanilla_version_from_log(logs_dir)
        else:
            loader, loader_version, mc_version = "unknown", None, None
        found.append({
            "source_id": sub.name,
            "name": sub.name,
            "version": mc_version,
            "loader": loader,
            "loader_version": loader_version,
            "has_options": (sub / "options.txt").is_file(),
            "mods_count": _count_dir(sub / "mods", ".jar") if loader in MOD_LOADERS else 0,
            "resourcepacks_count": _count_dir(sub / "resourcepacks"),
            "saves_count": _count_dir(sub / "saves"),
            "shaderpacks_count": _count_dir(sub / "shaderpacks"),
            "schematics_count": _count_dir(sub / "schematics"),
            "screenshots_count": _count_images(sub / "screenshots"),
            "config_count": _count_dir(sub / "config") if loader in MOD_LOADERS else 0,
        })
    return found


_LAUNCHER_SCANNERS = {
    "prismlauncher": _scan_prismlauncher,
    "polymc": _scan_prismlauncher,
    "sklauncher": _scan_sklauncher,
    "fastclient": _scan_fastclient,
    "modrinth": _scan_modrinth,
}


def scan_all_launchers() -> list:
    sources = []
    for launcher, scanner in _LAUNCHER_SCANNERS.items():
        for root in _launcher_roots(launcher):
            if not root.is_dir():
                continue
            try:
                instances = scanner(root)
            except Exception:
                log_exception(f"scanning {launcher} at {root}")
                continue
            if instances:
                sources.append({
                    "launcher": launcher, "label": LAUNCHER_LABELS[launcher],
                    "root": str(root), "instances": instances,
                })
            break
    return sources


def _guess_launcher_for_dir(path: Path):
    name = path.name.lower()
    for key, hints in (("modrinth", ("modrinth", "theseus")), ("sklauncher", ("sklauncher",)),
                       ("fastclient", ("fastclient", "fast client")), ("polymc", ("polymc",)),
                       ("prismlauncher", ("prism",))):
        if any(h in name for h in hints):
            return key
    for key, scanner in _LAUNCHER_SCANNERS.items():
        try:
            if scanner(path):
                return key
        except Exception:
            continue
    return None


def scan_custom_launcher_dir(launcher: str, root: Path) -> dict:
    scanner = _LAUNCHER_SCANNERS.get(launcher)
    if not scanner:
        raise ValueError("Unknown launcher type.")
    return {"launcher": launcher, "label": LAUNCHER_LABELS[launcher], "root": str(root),
            "instances": scanner(root), "custom": True}


def _find_scanned_instance(launcher: str, source_id: str, custom_root=None):
    if custom_root:
        root = Path(custom_root)
        scanner = _LAUNCHER_SCANNERS.get(launcher)
        if not scanner or not root.is_dir():
            return None, None
        for inst in scanner(root):
            if inst["source_id"] == source_id:
                return inst, root
        return None, None
    for root in _launcher_roots(launcher):
        if not root.is_dir():
            continue
        scanner = _LAUNCHER_SCANNERS.get(launcher)
        if not scanner:
            return None, None
        for inst in scanner(root):
            if inst["source_id"] == source_id:
                return inst, root
        return None, None
    return None, None


def _source_paths(launcher: str, source_id: str, root: Path):
    if launcher in ("prismlauncher", "polymc"):
        game_dir = _prism_game_dir(root / "instances" / source_id)
        return (game_dir / "options.txt", game_dir / "mods",
                game_dir / "resourcepacks", game_dir / "saves", game_dir / "config",
                game_dir / "shaderpacks", game_dir / "schematics", game_dir / "screenshots")
    if launcher == "sklauncher":
        game_dir = _sk_game_dir(root / "instances" / source_id) or (root / "instances" / source_id)
        return (game_dir / "options.txt", game_dir / "mods",
                game_dir / "resourcepacks", game_dir / "saves", game_dir / "config",
                game_dir / "shaderpacks", game_dir / "schematics", game_dir / "screenshots")
    if launcher == "modrinth":
        profile_dir = root / "profiles" / source_id
        return (profile_dir / "options.txt", profile_dir / "mods",
                profile_dir / "resourcepacks", profile_dir / "saves", profile_dir / "config",
                profile_dir / "shaderpacks", profile_dir / "schematics", profile_dir / "screenshots")
    if launcher == "fastclient":
        profile_dir = root if source_id == FASTCLIENT_ROOT_SOURCE_ID else root / "profiles" / source_id
        return (profile_dir / "options.txt", profile_dir / "mods",
                profile_dir / "resourcepacks", profile_dir / "saves", profile_dir / "config",
                profile_dir / "shaderpacks", profile_dir / "schematics", profile_dir / "screenshots")
    return None, None, None, None, None, None, None, None


def _copy_tree_contents(src: Path, dst: Path):
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.iterdir():
        target = dst / item.name
        try:
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
            count += 1
        except OSError:
            log_exception(f"copying {item} -> {target}")
    return count


class ProgressTracker:
    def __init__(self, emit_fn, total_estimate: int = 1):
        self.emit_fn = emit_fn
        self.event = "installProgress"
        self.lock = threading.Lock()
        self._emit_lock = threading.Lock()
        self.total = max(total_estimate, 1)
        self.downloaded = 0
        self.unit = "bytes"
        self.current_task = "Preparing"
        self._last_emit = 0.0

    def reset(self, total: int, unit: str = "bytes"):
        with self.lock:
            self.total = max(int(total), 1)
            self.downloaded = 0
            self.unit = unit
        self._emit(force=True)

    def set_total(self, total: int):
        with self.lock:
            self.total = max(total, 1)

    def set_task(self, task: str, force=False):
        with self.lock:
            self.current_task = task
        self._emit(force=True)

    def add(self, n: int):
        with self.lock:
            self.downloaded += n
        self._emit()

    def _emit(self, force=False):
        if force:
            self._emit_lock.acquire()
        elif not self._emit_lock.acquire(False):
            return
        try:
            now = time.time()
            if not force and now - self._last_emit < 0.08:
                return
            self._last_emit = now
            with self.lock:
                percent = min(100, int(self.downloaded * 100 / self.total))
                payload = {
                    "percent": percent,
                    "current": self.current_task,
                    "downloaded": self.downloaded,
                    "total": self.total,
                    "unit": self.unit,
                }
            self.emit_fn(self.event, payload)
        finally:
            self._emit_lock.release()


_MEM_TOTAL_MB = None
_MEM_LOCK = threading.Lock()


def total_system_memory_mb() -> int:
    """Physical RAM in MB, or 0 when it can't be determined."""
    global _MEM_TOTAL_MB
    with _MEM_LOCK:
        if _MEM_TOTAL_MB is not None:
            return _MEM_TOTAL_MB
    total = 0
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total = int(stat.ullTotalPhys // (1024 * 1024))
        elif current_os_name() == "osx":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 timeout=5, creationflags=CREATE_NO_WINDOW)
            total = int(out.stdout.decode().strip()) // (1024 * 1024)
        else:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                    break
    except Exception:
        total = 0
    if total > 0:
        with _MEM_LOCK:
            _MEM_TOTAL_MB = total
    return total


def memory_ceiling_mb() -> int:
    """Most a Minecraft instance should be given: about half of physical RAM."""
    total = total_system_memory_mb()
    if total <= 0:
        return 32768
    return max(1024, int(total * 0.5))


def clamp_memory_mb(value) -> int:
    mb = int(value)
    return max(512, min(mb, memory_ceiling_mb()))


def maven_to_path(name: str) -> str:
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    ext = "jar"
    if "@" in version:
        version, ext = version.split("@", 1)
    if classifier and "@" in classifier:
        classifier, ext = classifier.split("@", 1)
    group_path = group.replace(".", "/")
    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += f".{ext}"
    return f"{group_path}/{artifact}/{version}/{filename}"


def lib_key(name: str) -> str:
    parts = name.split(":")
    if len(parts) < 3:
        return name
    key = f"{parts[0]}:{parts[1]}"
    if len(parts) > 3:
        key += ":" + parts[3].split("@", 1)[0]
    return key


def fix_maven_url(url: str) -> str:
    url = url.replace("http://files.minecraftforge.net/maven/", "https://maven.minecraftforge.net/")
    if url.startswith("http://") and not url.startswith(("http://127.0.0.1", "http://localhost")):
        url = "https://" + url[len("http://"):]
    if not url.endswith("/"):
        url += "/"
    return url


def collect_library_entries(libraries: list, features=None):
    classpath_entries = []
    native_entries = []
    features = features or {}
    seen_keys = set()
    seen_native_paths = set()

    for lib in libraries:
        if lib.get("clientreq") is False:
            continue
        if "rules" in lib and not rules_allow(lib["rules"], features):
            continue

        name = lib.get("name", "")
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact")

        if artifact and artifact.get("path"):
            key = lib_key(name) if name else artifact["path"]
            if key not in seen_keys:
                seen_keys.add(key)
                classpath_entries.append({
                    "path": artifact["path"],
                    "url": artifact.get("url", ""),
                    "size": artifact.get("size"),
                    "sha1": artifact.get("sha1"),
                })
        elif name and not downloads.get("classifiers"):
            key = lib_key(name)
            if key not in seen_keys:
                seen_keys.add(key)
                path = maven_to_path(name)
                base_url = fix_maven_url(lib.get("url") or LIBRARIES_BASE_URL)
                classpath_entries.append({
                    "path": path,
                    "url": base_url + path,
                    "size": lib.get("size"),
                    "sha1": lib.get("sha1"),
                })

        natives_map = lib.get("natives")
        if natives_map:
            classifier_key = natives_map.get(current_os_name())
            if classifier_key:
                classifier_key = classifier_key.replace("${arch}", current_arch())
                classifiers = downloads.get("classifiers", {})
                native_artifact = classifiers.get(classifier_key)
                if native_artifact and native_artifact.get("path"):
                    if native_artifact["path"] in seen_native_paths:
                        continue
                    seen_native_paths.add(native_artifact["path"])
                    exclude = lib.get("extract", {}).get("exclude", [])
                    native_entries.append({
                        "path": native_artifact["path"],
                        "url": native_artifact.get("url", ""),
                        "size": native_artifact.get("size"),
                        "sha1": native_artifact.get("sha1"),
                        "extract_exclude": exclude,
                    })

    return classpath_entries, native_entries


def entry_weight(entry: dict) -> int:
    return int(entry.get("size") or 300_000)


def ensure_file(entry: dict, dest_root: Path, tracker, retries: int = 3):
    dest = dest_root / entry["path"]
    quick_sha = None if entry.get("quick") else entry.get("sha1")
    if file_is_valid(dest, entry.get("size"), quick_sha):
        return dest
    if not entry.get("url"):
        raise RuntimeError(f"Failed to download library: {entry['path']} (no URL)")

    def cb(n):
        if tracker:
            tracker.add(n)

    last = None
    for attempt in range(retries):
        try:
            http_download(entry["url"], dest, entry.get("size"), entry.get("sha1"), cb)
            return dest
        except Exception as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Failed to download {entry['path']} ({last})") from last


def download_all(items, tracker, workers: int = 10):
    todo, missing, seen = [], [], set()
    for entry, root in items:
        dest = root / entry["path"]
        key = str(dest)
        if key in seen:
            continue
        seen.add(key)
        quick_sha = None if entry.get("quick") else entry.get("sha1")
        if file_is_valid(dest, entry.get("size"), quick_sha):
            tracker.add(entry_weight(entry))
            continue
        if not entry.get("url"):
            missing.append(entry)
            continue
        todo.append((entry, root))

    if not todo:
        return missing
    if len(todo) == 1 or workers <= 1:
        for entry, root in todo:
            ensure_file(entry, root, tracker)
        return missing

    with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
        futures = [pool.submit(ensure_file, e, r, tracker) for e, r in todo]
        try:
            for fut in as_completed(futures):
                fut.result()
        except Exception:
            for fut in futures:
                fut.cancel()
            raise
    return missing


def extract_natives(native_paths_and_excludes, natives_dir: Path):
    shutil.rmtree(natives_dir, ignore_errors=True)
    natives_dir.mkdir(parents=True, exist_ok=True)
    for jar_path, exclude in native_paths_and_excludes:
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                for member in zf.namelist():
                    if member.startswith("META-INF/") or member.endswith("/"):
                        continue
                    if any(member.startswith(ex.rstrip("/") + "/") or member == ex
                           for ex in exclude):
                        continue
                    if not is_within(natives_dir, natives_dir / member):
                        continue
                    zf.extract(member, natives_dir)
        except zipfile.BadZipFile:
            log_exception(f"Bad native jar: {jar_path}")


def _jar_main_class(jar_path: Path) -> str | None:
    try:
        with zipfile.ZipFile(jar_path) as zf:
            manifest = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "ignore")
            manifest = manifest.replace("\r\n ", "").replace("\n ", "")
            m = re.search(r"Main-Class:\s*(\S+)", manifest)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


class ForgeInstallContext:

    def __init__(self, profile: dict, installer_jar: Path, tmp_dir: Path,
                 mc_version: str, client_path: Path):
        self.profile = profile
        self.installer_jar = installer_jar
        self.tmp_dir = tmp_dir
        self.tokens = {
            "SIDE": "client",
            "MINECRAFT_JAR": str(client_path),
            "MINECRAFT_VERSION": mc_version,
            "ROOT": str(DATA_DIR),
            "INSTALLER": str(installer_jar),
            "LIBRARY_DIR": str(LIBRARIES_DIR),
        }
        for key, val in (profile.get("data") or {}).items():
            client_val = val.get("client") if isinstance(val, dict) else val
            if client_val is not None:
                self.tokens[key] = self._data_value(str(client_val))

    def _data_value(self, value: str) -> str:
        if value.startswith("[") and value.endswith("]"):
            return str(LIBRARIES_DIR / maven_to_path(value[1:-1]))
        if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
            return value[1:-1]
        if value.startswith("/"):
            member = value.lstrip("/")
            dest = self.tmp_dir / member
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(self.installer_jar) as zf:
                    dest.write_bytes(zf.read(member))
            return str(dest)
        return value

    def resolve(self, arg: str) -> str:
        if arg.startswith("[") and arg.endswith("]"):
            return str(LIBRARIES_DIR / maven_to_path(arg[1:-1]))
        out = re.sub(r"\{([A-Za-z0-9_]+)\}",
                     lambda m: self.tokens.get(m.group(1), m.group(0)), arg)
        if len(out) >= 2 and out.startswith("'") and out.endswith("'"):
            out = out[1:-1]
        return out

    def outputs_valid(self, proc: dict) -> bool:
        outputs = proc.get("outputs") or {}
        if not outputs:
            return False
        for key, expected in outputs.items():
            path = Path(self.resolve(key))
            want = self.resolve(expected)
            if not path.is_file():
                return False
            try:
                if file_sha1(path) != want:
                    return False
            except OSError:
                return False
        return True



def http_get_bytes(url: str, limit=None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit) if limit else resp.read()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def horizon_asset_name():
    system, arch = current_os_name(), current_arch()
    if system == "windows" and arch == "64":
        return "horizon-windows-x64.zip"
    if system == "osx" and arch == "arm64":
        return "horizon-macos-arm64.zip"
    if system == "linux" and arch == "64":
        return "horizon-linux-x86_64.tar.gz"
    return None


def horizon_exe(base: str) -> str:
    return base + (".exe" if current_os_name() == "windows" else "")


def horizon_versions_dir() -> Path:
    return HORIZON_DATA_DIR / "versions"


def horizon_version_dir(version: str) -> Path:
    safe = _INVALID_NAME_RE.sub("_", str(version or ""))[:120] or "build"
    return horizon_versions_dir() / safe


def horizon_minecraft_dir() -> Path:
    return HORIZON_DATA_DIR / "minecraft"


def horizon_minecraft_version(vdir: Path) -> str:
    """The Minecraft version a Horizon build was made for (research-lock.json in its folder)."""
    try:
        lock = json.loads((Path(vdir) / "research-lock.json").read_text(encoding="utf-8"))
        return str(((lock.get("minecraft") or {}).get("version")) or HORIZON_MC_VERSION)
    except (OSError, ValueError):
        return HORIZON_MC_VERSION


def horizon_resources_dir(vdir: Path) -> Path:
    return Path(vdir) / ".local" / "horizon-client" / horizon_minecraft_version(vdir)



_ED_P = 2 ** 255 - 19
_ED_Q = 2 ** 252 + 27742317777372353535851937790883648493
_ED_D = -121665 * pow(121666, _ED_P - 2, _ED_P) % _ED_P
_ED_SQRT_M1 = pow(2, (_ED_P - 1) // 4, _ED_P)


def _ed_recover_x(y, sign):
    if y >= _ED_P:
        return None
    x2 = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_P - 2, _ED_P) % _ED_P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_ED_P + 3) // 8, _ED_P)
    if (x * x - x2) % _ED_P:
        x = x * _ED_SQRT_M1 % _ED_P
    if (x * x - x2) % _ED_P:
        return None
    return _ED_P - x if (x & 1) != sign else x


def _ed_add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _ED_P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _ED_P
    C = 2 * a[3] * b[3] * _ED_D % _ED_P
    D = 2 * a[2] * b[2] % _ED_P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _ED_P, G * H % _ED_P, F * G % _ED_P, E * H % _ED_P)


def _ed_mul(s, point):
    result = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            result = _ed_add(result, point)
        point = _ed_add(point, point)
        s >>= 1
    return result


def _ed_equal(a, b):
    return ((a[0] * b[2] - b[0] * a[2]) % _ED_P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _ED_P == 0)


def _ed_decompress(data):
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _ed_recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _ED_P)


_ED_GY = 4 * pow(5, _ED_P - 2, _ED_P) % _ED_P
_ED_G = (_ed_recover_x(_ED_GY, 0), _ED_GY, 1, _ed_recover_x(_ED_GY, 0) * _ED_GY % _ED_P)


def horizon_ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A, R = _ed_decompress(public), _ed_decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if A is None or R is None or s >= _ED_Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _ED_Q
    return _ed_equal(_ed_mul(s, _ED_G), _ed_add(R, _ed_mul(h, A)))


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
    code, output = horizon_run_logged(base + ["sounds", "--minecraft-dir", str(mc_root)],
                                      vdir, tracker, "Copying Horizon's sounds", java_path)
    if code != 0:
        logging.warning("horizon-prepare sounds failed (the game will be silent):\n%s", output[-2000:])


def horizon_setup(name: str, tracker) -> dict:
    """Install and prepare everything one Horizon instance needs. Returns the meta to save."""
    meta = read_instance_meta(name) or {}
    build_version, vdir = horizon_install_build(tracker)
    mc_version = horizon_minecraft_version(vdir)
    resources = horizon_resources_dir(vdir)

    java_path = meta.get("horizon_java")
    if not (java_path and Path(java_path).is_file()):
        java_path = horizon_find_java(tracker)

    if not (resources / "horizon-resources.json").is_file():
        mc_root, vjson = horizon_ensure_minecraft(mc_version, tracker)
        
        fallback = Path(vdir) / ".local" / "minecraft" / mc_version / "manifest.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(vjson, fallback)
        server_jar = horizon_ensure_server_jar(mc_version, vjson, tracker)
        horizon_prepare_build(vdir, mc_version, mc_root, server_jar, tracker, java_path)

    if not (resources / "horizon-resources.json").is_file():
        raise RuntimeError("Horizon finished preparing but its resources are missing.")

    return {
        "version": mc_version,
        "loader": "horizon",
        "loader_version": None,
        "horizon_version": build_version,
        "horizon_dir": str(vdir),
        "horizon_resources": str(resources),
        "horizon_minecraft": mc_version,
        "horizon_java": java_path,
        "horizon_prepared": True,
        "installed": True,
        "schema": INSTALL_SCHEMA,
        "java_component": "horizon",
        "java_major": HORIZON_JAVA_MAJOR,
    }


class Installer:
    def __init__(self, api: "API", meta: dict):
        self.api = api
        self.name = sanitize_instance_name(meta["name"])
        self.version_id = meta["version"]
        self.loader = meta.get("loader") or "vanilla"
        self.loader_version = meta.get("loader_version")

    def run(self) -> bool:
        emit = self.api.emit
        forge_tmp = None
        try:
            emit("installStarted", {"instance": self.name,
                                    "title": f"Installing {self.name}..."})

            target_dir = instance_dir(self.name)
            target_dir.mkdir(parents=True, exist_ok=True)

            tracker = ProgressTracker(emit)
            tracker.set_task("Fetching version metadata", force=True)

            manifest = meta_cache.get_manifest()
            version_entry = next(
                (v for v in manifest["versions"] if v["id"] == self.version_id), None)
            if not version_entry:
                raise RuntimeError(f"Minecraft {self.version_id} was not found in Mojang's version list.")
            vanilla_json = meta_cache.get_version_json(self.version_id, version_entry["url"])

            java_info = vanilla_json.get("javaVersion") or dict(DEFAULT_JAVA_INFO)
            java_path = resolve_java(java_info, allow_download=True, tracker=tracker)
            if not java_path:
                raise RuntimeError("Could not find or download a suitable Java runtime.")

            asset_index_info = vanilla_json.get("assetIndex", {})
            asset_index_id = asset_index_info.get("id", self.version_id)
            asset_index_path = ASSET_INDEXES_DIR / f"{asset_index_id}.json"
            tracker.set_task("Fetching asset index", force=True)
            if asset_index_path.exists():
                asset_index_data = json.loads(asset_index_path.read_text(encoding="utf-8"))
            elif asset_index_info.get("url"):
                asset_index_data = http_get_json(asset_index_info["url"])
                asset_index_path.write_text(json.dumps(asset_index_data), encoding="utf-8")
            else:
                asset_index_data = {"objects": {}}
            asset_objects = asset_index_data.get("objects", {})

            if self.loader == "fabric":
                plan = self._plan_fabric(vanilla_json, tracker)
            elif self.loader in ("forge", "neoforge"):
                plan, forge_tmp = self._plan_forge(vanilla_json, tracker)
            else:
                plan = self._plan_vanilla(vanilla_json)

            cp_entries, native_entries = collect_library_entries(plan["libraries"])

            client_dl = vanilla_json.get("downloads", {}).get("client", {})
            client_entry = {
                "path": f"{self.version_id}/{self.version_id}.jar",
                "url": client_dl.get("url", ""),
                "size": client_dl.get("size"),
                "sha1": client_dl.get("sha1"),
            }

            asset_items, seen_hashes = [], set()
            for _name, obj in asset_objects.items():
                obj_hash = obj.get("hash")
                if not obj_hash or obj_hash in seen_hashes:
                    continue
                seen_hashes.add(obj_hash)
                prefix = obj_hash[:2]
                asset_items.append(({
                    "path": f"{prefix}/{obj_hash}",
                    "url": f"{RESOURCES_BASE_URL}/{prefix}/{obj_hash}",
                    "size": obj.get("size"),
                    "sha1": obj_hash,
                    "quick": True,
                }, ASSET_OBJECTS_DIR))

            lib_items = [(e, LIBRARIES_DIR)
                         for e in cp_entries + native_entries + plan.get("installer_entries", [])]

            total = entry_weight(client_entry)
            total += sum(entry_weight(e) for e, _ in lib_items)
            total += sum(int(e.get("size") or 0) for e, _ in asset_items)
            tracker.reset(total)

            tracker.set_task("Downloading client", force=True)
            download_all([(client_entry, VERSIONS_DIR)], tracker)
            client_path = VERSIONS_DIR / client_entry["path"]

            tracker.set_task("Downloading libraries", force=True)
            download_all(lib_items, tracker)

            tracker.set_task("Downloading assets", force=True)
            download_all(asset_items, tracker, workers=16)

            post = plan.get("post")
            if post:
                post(client_path, java_path, tracker)

            missing = [e["path"] for e in cp_entries
                       if not (LIBRARIES_DIR / e["path"]).is_file()]
            missing += [e["path"] for e in native_entries
                        if not (LIBRARIES_DIR / e["path"]).is_file()]
            if missing:
                raise RuntimeError(
                    "Some files are still missing after install: " + ", ".join(missing[:3]))

            tracker.set_task("Extracting natives", force=True)
            natives_dir = target_dir / "natives"
            extract_natives(
                [(LIBRARIES_DIR / e["path"], e.get("extract_exclude", [])) for e in native_entries],
                natives_dir)

            tracker.set_task("Finalizing instance", force=True)
            ensure_game_layout(self.name, self.loader)

            classpath = ["libraries/" + e["path"] for e in cp_entries]
            classpath.append(f"versions/{client_entry['path']}")

            meta = read_instance_meta(self.name) or {}
            meta.update({
                "name": self.name,
                "version": self.version_id,
                "loader": self.loader,
                "loader_version": self.loader_version,
                "main_class": plan["main_class"],
                "asset_index": asset_index_id,
                "arguments": plan.get("arguments"),
                "minecraft_arguments": plan.get("minecraft_arguments"),
                "classpath": classpath,
                "java_component": java_info.get("component", DEFAULT_JAVA_INFO["component"]),
                "java_major": int(java_info.get("majorVersion", DEFAULT_JAVA_INFO["majorVersion"])),
                "installed": True,
                "schema": INSTALL_SCHEMA,
                "created": meta.get("created", time.time()),
            })
            write_instance_meta(self.name, meta)

            def _set_active(s):
                if not s.get("active_instance"):
                    s["active_instance"] = self.name
            settings_store.update(_set_active)

            emit("installFinished", {"instance": self.name})
            self.api.push_state()
            return True

        except Exception as exc:
            log_exception(f"Install failed for {self.name}")
            emit("installError", {"message": str(exc) or "Installation failed."})
            return False
        finally:
            if forge_tmp:
                shutil.rmtree(forge_tmp, ignore_errors=True)


    def _plan_vanilla(self, vanilla_json):
        return {
            "main_class": vanilla_json["mainClass"],
            "libraries": list(vanilla_json.get("libraries", [])),
            "arguments": merge_arguments(vanilla_json.get("arguments"), None),
            "minecraft_arguments": vanilla_json.get("minecraftArguments"),
        }


    def _plan_fabric(self, vanilla_json, tracker):
        if not self.loader_version:
            raise RuntimeError("No Fabric loader version selected.")
        tracker.set_task("Fetching Fabric metadata", force=True)
        try:
            profile = meta_cache.get_fabric_profile(self.version_id, self.loader_version)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Fabric loader {self.loader_version} isn't available for Minecraft "
                f"{self.version_id} (HTTP {exc.code}).") from exc

        main_class = profile.get("mainClass") or vanilla_json["mainClass"]
        if isinstance(main_class, dict):
            main_class = main_class.get("client") or vanilla_json["mainClass"]

        return {
            "main_class": main_class,
            "libraries": list(profile.get("libraries", [])) + list(vanilla_json.get("libraries", [])),
            "arguments": merge_arguments(vanilla_json.get("arguments"), profile.get("arguments")),
            "minecraft_arguments": profile.get("minecraftArguments") or vanilla_json.get("minecraftArguments"),
        }


    def _plan_forge(self, vanilla_json, tracker):
        is_neo = self.loader == "neoforge"
        label = "NeoForge" if is_neo else "Forge"
        if not self.loader_version:
            raise RuntimeError(f"No {label} version selected.")
        mc_version = self.version_id
        if is_neo:
            full_version, installer_url = neoforge_installer_info(mc_version, self.loader_version)
        else:
            full_version = f"{mc_version}-{self.loader_version}"
            installer_url = f"{FORGE_MAVEN}{full_version}/forge-{full_version}-installer.jar"
        installer_jar = VERSIONS_DIR / f"{label.lower()}-installer-{full_version}.jar"

        tracker.set_task(f"Downloading {label} installer", force=True)
        tracker.reset(8_000_000)
        if installer_jar.exists() and not zipfile.is_zipfile(installer_jar):
            installer_jar.unlink(missing_ok=True)
        if not installer_jar.exists():
            try:
                http_download(installer_url, installer_jar, progress_cb=tracker.add)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    f"{label} {self.loader_version} for Minecraft {mc_version} could not be "
                    f"downloaded (HTTP {exc.code}).") from exc

        tmp_dir = TMP_DIR / f"{label.lower()}-{full_version}"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(installer_jar) as zf:
            profile = json.loads(zf.read("install_profile.json").decode("utf-8"))
            legacy = "versionInfo" in profile
            if legacy:
                version_json = profile["versionInfo"]
            else:
                json_member = (profile.get("json") or "/version.json").lstrip("/")
                version_json = json.loads(zf.read(json_member).decode("utf-8"))

            for member in zf.namelist():
                if member.endswith("/") or not member.startswith("maven/"):
                    continue
                dest = LIBRARIES_DIR / member[len("maven/"):]
                if not is_within(LIBRARIES_DIR, dest):
                    continue
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))

            if legacy:
                install = profile.get("install", {})
                file_path, art = install.get("filePath"), install.get("path")
                if file_path and art:
                    dest = LIBRARIES_DIR / maven_to_path(art)
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(file_path))

        installer_entries = []
        if not legacy:
            installer_entries, _ = collect_library_entries(list(profile.get("libraries", [])))

        plan = {
            "main_class": version_json.get("mainClass", vanilla_json["mainClass"]),
            "libraries": list(version_json.get("libraries", [])) + list(vanilla_json.get("libraries", [])),
            "arguments": merge_arguments(vanilla_json.get("arguments"), version_json.get("arguments")),
            "minecraft_arguments": version_json.get("minecraftArguments") or vanilla_json.get("minecraftArguments"),
            "installer_entries": installer_entries,
        }

        if not legacy and profile.get("processors"):
            def post(client_path, java_path, trk):
                self._run_forge_processors(profile, installer_jar, tmp_dir, mc_version,
                                           client_path, java_path, trk, label)
            plan["post"] = post

        return plan, tmp_dir

    def _run_forge_processors(self, profile, installer_jar, tmp_dir, mc_version,
                              client_path, java_path, tracker, label="Forge"):
        ctx = ForgeInstallContext(profile, installer_jar, tmp_dir, mc_version, client_path)
        procs = [p for p in profile.get("processors", [])
                 if not p.get("sides") or "client" in p["sides"]]
        if not procs:
            return

        java_exe = console_java(java_path)
        tracker.reset(len(procs), unit="steps")

        for index, proc in enumerate(procs, 1):
            tracker.set_task(f"Setting up {label} ({index}/{len(procs)})", force=True)

            if ctx.outputs_valid(proc):
                tracker.add(1)
                continue

            jar_rel = maven_to_path(proc["jar"])
            jar_path = LIBRARIES_DIR / jar_rel
            if not jar_path.is_file():
                raise RuntimeError(f"Missing {label} tool: {proc['jar']}")

            cp_parts = [str(jar_path)]
            for coord in proc.get("classpath", []):
                cp = LIBRARIES_DIR / maven_to_path(coord)
                if not cp.is_file():
                    raise RuntimeError(f"Missing {label} tool library: {coord}")
                cp_parts.append(str(cp))

            main_class = _jar_main_class(jar_path)
            if not main_class:
                raise RuntimeError(f"{label} tool has no main class: {proc['jar']}")

            args = [ctx.resolve(a) for a in proc.get("args", [])]
            cmd = [java_exe, "-cp", classpath_sep().join(cp_parts), main_class] + args
            logging.info("Forge processor %s/%s: %s", index, len(procs), " ".join(cmd)[:1500])

            result = subprocess.run(
                cmd, cwd=str(tmp_dir), capture_output=True, text=True, errors="replace",
                timeout=1200, creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                tail = ((result.stderr or "") + (result.stdout or ""))[-800:].strip()
                logging.error("Forge processor failed (%s): %s", main_class, tail)
                raise RuntimeError(f"{label} setup step failed ({main_class.split('.')[-1]}): {tail}")
            tracker.add(1)


def build_launch_command(meta: dict, account: dict, memory_mb: int, java_exe: str, options: dict = None) -> list:
    options = options or {}
    name = meta["name"]
    game_dir = instance_game_dir(name)
    natives_dir = instance_dir(name) / "natives"
    game_dir.mkdir(parents=True, exist_ok=True)

    classpath_list = [str(DATA_DIR / rel) for rel in meta["classpath"]]

    replacements = {
        "auth_player_name": account["name"],
        "auth_uuid": account["uuid"].replace("-", "") if account.get("type") == "microsoft" else account["uuid"],
        "auth_access_token": account.get("mc_token") or "0" if account.get("type") == "microsoft" else "0",
        "auth_session": "0",
        "auth_xuid": "0",
        "clientid": "0",
        "user_type": "msa" if account.get("type") == "microsoft" else "legacy",
        "user_properties": "{}",
        "version_name": meta["version"],
        "version_type": "release",
        "game_directory": str(game_dir),
        "assets_root": str(ASSETS_DIR),
        "assets_index_name": meta.get("asset_index") or meta["version"],
        "natives_directory": str(natives_dir),
        "library_directory": str(LIBRARIES_DIR),
        "classpath_separator": classpath_sep(),
        "classpath": classpath_sep().join(classpath_list),
        "launcher_name": "CritLauncher",
        "launcher_version": "1.0",
    }

    features = {}
    args_spec = meta.get("arguments") or {}

    if args_spec.get("jvm"):
        jvm_args = resolve_argument_list(args_spec["jvm"], features, replacements)
    else:
        jvm_args = [substitute(a, replacements) for a in DEFAULT_MODERN_JVM_ARGS]

    if not any(a in ("-cp", "-classpath") for a in jvm_args):
        jvm_args = ["-cp", replacements["classpath"]] + jvm_args
    if not any(a.startswith("-Djava.library.path=") for a in jvm_args):
        jvm_args = [f"-Djava.library.path={natives_dir}"] + jvm_args

    if args_spec.get("game"):
        game_args = resolve_argument_list(args_spec["game"], features, replacements)
    elif meta.get("minecraft_arguments"):
        game_args = [substitute(tok, replacements)
                     for tok in meta["minecraft_arguments"].split(" ") if tok]
    else:
        game_args = [
            "--username", replacements["auth_player_name"],
            "--version", replacements["version_name"],
            "--gameDir", replacements["game_directory"],
            "--assetsDir", replacements["assets_root"],
            "--assetIndex", replacements["assets_index_name"],
            "--uuid", replacements["auth_uuid"],
            "--accessToken", replacements["auth_access_token"],
            "--userType", replacements["user_type"],
            "--versionType", replacements["version_type"],
        ]

    try:
        min_mb = int(options.get("min_memory_mb") or 0)
    except (TypeError, ValueError):
        min_mb = 0
    xms = min(min_mb, memory_mb) if min_mb >= 256 else min(memory_mb, 1024)
    command = [java_exe, f"-Xmx{memory_mb}M", f"-Xms{xms}M"]
    command.extend(jvm_args)
    extra_jvm = str(options.get("jvm_args") or "").strip()
    if extra_jvm:
        try:
            import shlex
            tokens = shlex.split(extra_jvm, posix=(os.name != "nt"))
        except ValueError:
            tokens = extra_jvm.split()
        command.extend(t.strip('"') for t in tokens if t.strip('"'))
    command.append(meta["main_class"])
    command.extend(game_args)
    if options.get("fullscreen"):
        command.append("--fullscreen")
    else:
        try:
            gw, gh = int(options.get("game_width") or 0), int(options.get("game_height") or 0)
        except (TypeError, ValueError):
            gw = gh = 0
        if gw >= 320 and gh >= 240:
            command.extend(["--width", str(gw), "--height", str(gh)])
    return command


def _log_tail(path: Path, max_bytes: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _explain_crash(tail: str) -> str:
    if "UnsupportedClassVersionError" in tail or "class file version" in tail:
        return "This Minecraft version needs a newer Java. Clear the custom Java path in Settings so Crit Launcher can pick one."
    if "Could not create the Java Virtual Machine" in tail or "Invalid maximum heap size" in tail:
        return "Java couldn't start with that memory setting. Try lowering the allocated memory in Settings."
    if "ClassNotFoundException" in tail or "NoClassDefFoundError" in tail:
        return "A library is missing. Delete the instance's files by re-installing it, then try again."
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    return lines[-1][:220] if lines else "Check launch_output.log in the instance folder."


def pick_best_version(versions: list):
    if not versions:
        return None
    for wanted in ("release", "beta", "alpha"):
        for v in versions:
            if v.get("version_type") == wanted:
                return v
    return versions[0]


def primary_file(version: dict):
    files = version.get("files", [])
    return next((f for f in files if f.get("primary")), files[0] if files else None)


class CurseForgeError(Exception):
    """Raised with a message that is safe to show in the launcher."""


def _cf_get(path: str, params=None, timeout: int = 20, retries: int = 2, unwrap: bool = True):
    """GET a CurseForge endpoint through the proxy, unwrapping its {"data": ...} envelope."""
    url = f"{CURSEFORGE_API}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    last = None
    for attempt in range(retries + 1):
        try:
            data = http_get_json(url, timeout=timeout, retries=0)
            if not unwrap:
                return data
            return data.get("data") if isinstance(data, dict) and "data" in data else data
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt < retries:
                    wait = 2.0 * (attempt + 1)
                    try:
                        ra = exc.headers.get("Retry-After") if exc.headers else None
                        wait = min(10.0, max(1.0, float(ra)))
                    except (TypeError, ValueError):
                        pass
                    time.sleep(wait)
                    last = exc
                    continue
                raise CurseForgeError("CurseForge is rate limiting requests. Try again in a minute.") from exc
            if exc.code in (400, 401, 403, 404):
                raise CurseForgeError("CurseForge couldn't handle that request.") from exc
            last = exc
        except Exception as exc:
            last = exc
        time.sleep(0.8 * (attempt + 1))
    raise last if last is not None else CurseForgeError("Couldn't reach CurseForge.")


def _cf_number(project_id) -> int:
    raw = str(project_id or "")
    if raw.startswith("cf:"):
        raw = raw[3:]
    if not raw.isdigit():
        raise CurseForgeError("That isn't a CurseForge project.")
    return int(raw)


def _cf_hit(mod: dict) -> dict:
    cats = [c.get("name") for c in (mod.get("categories") or []) if isinstance(c, dict) and c.get("name")]
    authors = [a.get("name") for a in (mod.get("authors") or []) if isinstance(a, dict) and a.get("name")]
    logo = mod.get("logo") if isinstance(mod.get("logo"), dict) else {}
    
    shots = []
    for s in (mod.get("screenshots") or []):
        if not isinstance(s, dict):
            continue
        url = s.get("url") or s.get("thumbnailUrl")
        if isinstance(url, str) and url.startswith("https://"):
            shots.append(url)
    return {
        "gallery": shots,
        "featured_gallery": shots[0] if shots else None,
        "color": None,
        "project_id": f"cf:{mod.get('id')}",
        "slug": None,
        "title": mod.get("name") or "Unknown",
        "description": mod.get("summary") or "",
        "author": ", ".join(authors) or "unknown",
        "downloads": int(mod.get("downloadCount") or 0),
        "follows": None,
        "icon_url": logo.get("thumbnailUrl") or logo.get("url"),
        "categories": cats,
        "display_categories": cats,
        "client_side": "optional",
        "server_side": "optional",
        "date_modified": mod.get("dateModified") or "",
        "source": "curseforge",
    }


def _cf_search(query: str, project_type: str, sort: str, offset: int, limit: int, filters=None):
    """Search CurseForge. Returns Modrinth-shaped hits so the Addons UI renders them as-is."""
    class_id = CF_CLASS_IDS.get(project_type)
    if not class_id:
        return {"hits": [], "total": 0}
    params = {"gameId": CF_GAME_ID, "classId": class_id,
              "sortField": CF_SORT.get(sort, 2), "sortOrder": "desc",
              "index": max(0, int(offset or 0)),           
              "pageSize": max(1, min(50, limit))}
    if query:
        params["searchFilter"] = query
    filters = filters if isinstance(filters, dict) else {}
    ver = filters.get("version")
    if isinstance(ver, str) and re.match(r"^[0-9][0-9A-Za-z_.+\-]{0,19}$", ver):
        params["gameVersion"] = ver
    loaders = [x for x in (filters.get("loaders") or []) if x in CF_LOADER_IDS]
    if project_type == "mod" and loaders:
        params["modLoaderType"] = CF_LOADER_IDS[loaders[0]]
    data = _cf_get("/mods/search", params, unwrap=False) or {}
    mods, pagination = [], {}
    if isinstance(data, list):
        mods = data
    elif isinstance(data, dict):
        mods = data.get("data") or []
        pagination = data.get("pagination") or {}
    hits = [_cf_hit(m) for m in mods if isinstance(m, dict) and m.get("id") is not None]
    return {"hits": hits, "total": int(pagination.get("totalCount") or len(hits))}


def _cf_file_sha1(file_info: dict):
    for h in (file_info.get("hashes") or []):
        if isinstance(h, dict) and int(h.get("algo") or 0) == 1 and h.get("value"):
            return h["value"]
    return None


def _cf_pick_file(files, mc_version=None, loader=None):
    """Newest release file that matches the instance's Minecraft version + loader."""
    usable = [f for f in (files or []) if isinstance(f, dict) and f.get("id") is not None]
    if mc_version:
        usable = [f for f in usable if mc_version in (f.get("gameVersions") or [])]
    if loader and loader in CF_LOADER_IDS:
        want = loader.lower()
        usable = [f for f in usable
                  if want in {str(g).lower() for g in (f.get("gameVersions") or [])}] or usable
    releases = [f for f in usable if int(f.get("releaseType") or 1) == 1]
    pool = releases or usable
    pool.sort(key=lambda f: str(f.get("fileDate") or ""), reverse=True)
    return pool[0] if pool else None


def _cf_download_file(mod_id: int, file_info: dict, dest_dir: Path):
    name = Path(str(file_info.get("fileName") or "")).name
    if not name:
        raise CurseForgeError("That CurseForge file has no name.")
    dest = dest_dir / name
    if not is_within(dest_dir, dest):
        raise CurseForgeError("Unsafe file name in addon.")
    url = file_info.get("downloadUrl")
    if not url:
        
        detail = _cf_get(f"/mods/{mod_id}/files/{file_info['id']}") or {}
        url = detail.get("downloadUrl") if isinstance(detail, dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CurseForgeError("CurseForge didn't give a download link for that file.")
    http_download(url, dest, file_info.get("fileLength"), _cf_file_sha1(file_info))
    return name


def _cf_dependencies(mod_id: int, file_id) -> list:
    if file_id is None:
        return []
    try:
        deps = _cf_get(f"/mods/{mod_id}/dependencies", {"fileId": file_id}) or []
    except CurseForgeError:
        return []
    return [d for d in deps if isinstance(d, dict) and d.get("modId")
            and int(d.get("relationType") or 0) == CF_REQUIRED_RELATION]


def curseforge_install_to(mod_id: int, dest_dir: Path, mc_version=None, loader=None,
                          visited=None, depth: int = 0, with_deps: bool = True) -> list:
    """Install a CurseForge project (plus its required dependencies) into dest_dir."""
    visited = visited if visited is not None else set()
    visited.add(mod_id)
    files = _cf_get(f"/mods/{mod_id}/files", {"gameVersion": mc_version})
    if isinstance(files, dict):
        files = files.get("data") or []
    chosen = _cf_pick_file(files, mc_version, loader)
    if not chosen:
        raise CurseForgeError(f"No CurseForge file of this project supports {mc_version or 'this setup'}.")
    out = [_cf_download_file(mod_id, chosen, dest_dir)]
    if not with_deps or depth >= 3:
        return out
    for dep in _cf_dependencies(mod_id, chosen.get("id")):
        dep_id = int(dep["modId"])
        if dep_id in visited:
            continue
        try:
            out += curseforge_install_to(dep_id, dest_dir, mc_version, loader, visited, depth + 1)
        except CurseForgeError:
            logging.warning("CurseForge dependency %s failed", dep_id)
    return out


CRIT_CLIENT_BASE = "https://raw.githubusercontent.com/snigglaberry/critsite/refs/heads/main/assets/mods/"
CRIT_CLIENT_VERSIONS_URL = CRIT_CLIENT_BASE + "versions.json"
CRIT_CLIENT_MODS = ["sodium", "fabric-api", "entityculling", "lithium",
                    "immediatelyfast", "zfastnoise", "moreculling"]
CHANGELOG_REPO = "snigglaberry/crit-launcher"

# Releases are signed with a detached ed25519 signature over SHA256SUMS.
# Empty means the check is skipped (a warning is logged). Fill it in to enforce;
# see the release notes for how to generate the pair.
UPDATE_TRUSTED_KEYS = ()
UPDATE_SUMS = "SHA256SUMS"
UPDATE_SIG = "SHA256SUMS.sig"


def _bump_playtime(s: dict, elapsed: int):
    s["playtime_seconds"] = int(s.get("playtime_seconds", 0)) + elapsed
    days = s.get("playtime_days")
    days = dict(days) if isinstance(days, dict) else {}
    key = time.strftime("%Y-%m-%d")
    days[key] = int(days.get(key, 0)) + elapsed
    s["playtime_days"] = {k: days[k] for k in sorted(days)[-30:]}


def _playtime_days() -> dict:
    raw = settings_store.read().get("playtime_days")
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}


MC_NEWS_HOST = "https://launchercontent.mojang.com"
_addon_cats_mem = {"at": 0.0, "value": None}
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
APP_VERSION = "1.7"


def _version_tuple(tag) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", str(tag or ""))]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums) or (0,)


CHANGELOG_CACHE = DATA_DIR / "changelog_cache.json"
_changelog_mem = {"at": 0.0, "value": None}


def update_signed_sha256(release: dict, asset_name: str) -> str:
    """sha256 for asset_name, but only from a signature-verified SHA256SUMS.

    This is the check that stops a hijacked release from running code: the sums
    list is only trusted when it carries a valid signature from a baked-in key.
    """
    assets = {a.get("name"): a for a in release.get("assets") or []}
    if UPDATE_SUMS not in assets or UPDATE_SIG not in assets:
        raise RuntimeError("This release isn't signed, so it wasn't installed.")
    sums = http_get_bytes(assets[UPDATE_SUMS]["browser_download_url"], limit=1 << 20)
    signature = http_get_bytes(assets[UPDATE_SIG]["browser_download_url"], limit=1 << 20)

    message = ("crit launcher release %s\n" % release.get("tag_name")).encode() + sums
    keys = [base64.b64decode(k) for k in UPDATE_TRUSTED_KEYS]
    signed = False
    for line in signature.decode("utf-8", "replace").splitlines():
        try:
            raw = base64.b64decode(line.strip(), validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 64 and any(horizon_ed25519_verify(k, message, raw) for k in keys):
            signed = True
            break
    if not signed:
        raise RuntimeError("The update's signature didn't match, so it wasn't installed.")

    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(None, 1)
        if (len(parts) == 2 and parts[1].lstrip("*") == asset_name
                and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0])):
            return parts[0].lower()
    raise RuntimeError("The updater isn't in the signed checksums, so it wasn't installed.")


class AuthError(Exception):
    pass


def ensure_fresh_account(account: dict) -> dict:
    if account.get("type") != "microsoft":
        return account
    if account.get("mc_token") and float(account.get("mc_expires", 0)) - time.time() > 60:
        return account
    return refresh_ms_account(account)


def public_account(a: dict) -> dict:
    out = {"name": a.get("name"), "uuid": a.get("uuid"), "type": a.get("type", "offline")}
    return out


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


def find_account(accounts: list, key):
    if not key:
        return None
    return (next((a for a in accounts if a.get("uuid") == key), None)
            or next((a for a in accounts if a.get("name") == key), None))


PNG_SIG = b"\x89PNG\r\n\x1a\n"


def http_send(method: str, url: str, token=None, data=None, content_type=None, timeout: int = 30, headers=None):
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if content_type:
        hdrs["Content-Type"] = content_type
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _json_or_text(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json_or_text(exc.read())


def decode_png_uri(uri: str) -> bytes:
    m = re.match(r"^data:image/png;base64,([A-Za-z0-9+/=\s]+)$", uri or "")
    if not m:
        raise ValueError("That isn't a PNG image.")
    try:
        data = base64.b64decode(m.group(1))
    except Exception:
        raise ValueError("That image couldn't be read.")
    if not data.startswith(PNG_SIG) or len(data) < 33:
        raise ValueError("That isn't a PNG image.")
    if len(data) > 200_000:
        raise ValueError("That file is too big to be a skin.")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) not in ((64, 64), (64, 32)):
        raise ValueError("Skins must be 64x64 (or 64x32) pixels.")
    return data


def png_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _texture_host_ok(host: str) -> bool:
    return host == "minecraft.net" or host.endswith(".minecraft.net")


def fetch_texture_uri(url):
    if not url:
        return None
    if url.startswith("http://textures.minecraft.net"):
        url = "https://" + url[len("http://"):]
    if not _texture_host_ok(urllib.parse.urlparse(url).hostname or ""):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(400_001)
        if len(data) > 400_000 or not data.startswith(PNG_SIG):
            return None
        return png_uri(data)
    except Exception:
        return None


def multipart_skin(variant: str, png: bytes):
    boundary = "----CritLauncher" + uuid.uuid4().hex
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="variant"\r\n\r\n{variant}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="skin.png"\r\n'
            f'Content-Type: image/png\r\n\r\n').encode("utf-8")
    return head + png + f"\r\n--{boundary}--\r\n".encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _skin_error(status: int) -> str:
    if status in (401, 403):
        return "Your session has expired. Sign in again."
    if status == 400:
        return "Minecraft rejected that request."
    if status == 429:
        return "Too many changes in a short time. Wait a bit and try again."
    return f"Minecraft answered with an error (HTTP {status})."


def _fetch_named_skin(name: str):
    try:
        found = http_get_json(MOJANG_LOOKUP_URL + urllib.parse.quote(name), retries=1)
    except Exception:
        return None
    if not found or "id" not in found:
        return None
    try:
        prof = http_get_json(SESSION_PROFILE_URL + found["id"], retries=1)
    except Exception:
        return None
    skin = None
    for prop in prof.get("properties", []):
        if prop.get("name") == "textures":
            try:
                tex = json.loads(base64.b64decode(prop["value"]).decode("utf-8"))
            except Exception:
                continue
            skin = (tex.get("textures") or {}).get("SKIN")
    if not skin or not skin.get("url"):
        return None
    uri = fetch_texture_uri(skin["url"])
    if not uri:
        return None
    try:
        decode_png_uri(uri)
    except ValueError:
        return None
    return {"name": found.get("name", name), "image": uri,
            "variant": "slim" if (skin.get("metadata") or {}).get("model") == "slim" else "classic"}


skins_index = JsonStore(SKINS_DIR / "index.json", [])


def list_saved_skins() -> list:
    out = []
    for entry in skins_index.read():
        path = SKINS_DIR / f"{entry.get('id', '')}.png"
        if path.is_file():
            out.append({**entry, "image": png_uri(path.read_bytes())})
    return out


CSL_PROJECT = "customskinloader"
CAPE_TEXTURE_URL = "https://textures.minecraft.net/texture/"
MOJANG_CAPES = [
    {"id": "minecon2011", "name": "MINECON 2011", "hash": "953cac8b779fe41383e675ee2b86071a71658f2180f56fbce8aa315ea70e2ed6"},
    {"id": "minecon2012", "name": "MINECON 2012", "hash": "a2e8d97ec79100e90a75d369d1b3ba81273c4f82bc1b737e934eed4a854be1b6"},
    {"id": "minecon2013", "name": "MINECON 2013", "hash": "153b1a0dfcbae953cdeb6f2c2bf6bf79943239b1372780da44bcbb29273131da"},
    {"id": "minecon2015", "name": "MINECON 2015", "hash": "b0cc08840700447322d953a02b965f1d65a13a603bf64b17c803c21446fe1635"},
    {"id": "minecon2016", "name": "MINECON 2016", "hash": "e7dfea16dc83c97df01a12fabbd1216359c0cd0ea42f9999b6e97c584963e980"},
    {"id": "mojang", "name": "Mojang", "hash": "5786fe99be377dfb6858859f926c4dbc995751e91cee373468c5fbf4865e7151"},
    {"id": "mojang-classic", "name": "Mojang (Classic)", "hash": "8f120319222a9f4a104e2f5cb97b2cda93199a2ee9e1585cb8d09d6f687cb761"},
    {"id": "mojang-studios", "name": "Mojang Studios", "hash": "9e507afc56359978a3eb3e32367042b853cddd0995d17d0da995662913fb00f7"},
    {"id": "migrator", "name": "Migrator", "hash": "2340c0e03dd24a11b15a8b33c2a7e9e32abb2051b2481d0ba7defd635ca7a933"},
    {"id": "vanilla", "name": "Vanilla", "hash": "f9a76537647989f9a0b6d001e320dac591c359e9e61a31f4ce11c88f207f0ad4"},
    {"id": "cherry-blossom", "name": "Cherry Blossom", "hash": "afd553b39358a24edfe3b8a9a939fa5fa4faa4d9a9c3d6af8eafb377fa05c2bb"},
    {"id": "turtle", "name": "Turtle", "hash": "5048ea61566353397247d2b7d946034de926b997d5e66c86483dfb1e031aee95"},
    {"id": "common", "name": "Common", "hash": "5ec930cdd2629c8771655c60eebeb867b4b6559b0e6d3bc71c40c96347fa03f0"},
    {"id": "birthday", "name": "Birthday", "hash": "2056f2eebd759cce93460907186ef44e9192954ae12b227d817eb4b55627a7fc"},
    {"id": "copper", "name": "Copper", "hash": "5e6f3193e74cd16cdd6637d9bae5484e3a37ff2a14c2d157c659a07810b1bdca"},
    {"id": "hero", "name": "Hero", "hash": "bb384a1079b9a6f2811520c7991b6e8150d02e5a96457e44509e30822f72f38e"},
    {"id": "home", "name": "Home", "hash": "1de21419009db483900da6298a1e6cbf9f1bc1523a0dcdc16263fab150693edd"},
    {"id": "realms-mapmaker", "name": "Realms MapMaker", "hash": "17912790ff164b93196f08ba71d0e62129304776d0f347334f8a6eae509f8a56"},
    {"id": "mojira-moderator", "name": "Mojira Moderator", "hash": "ae677f7d98ac70a533713518416df4452fe5700365c09cf45d0d156ea9396551"},
    {"id": "translator", "name": "Translator", "hash": "1bf91499701404e21bd46b0191d63239a4ef76ebde88d27e4d430ac211df681e"},
    {"id": "translator-chinese", "name": "Chinese Translator", "hash": "2262fb1d24912209490586ecae98aca8500df3eff91f2a07da37ee524e7e3cb6"},
    {"id": "millionth-customer", "name": "Millionth Customer", "hash": "70efffaf86fe5bc089608d3cb297d3e276b9eb7a8f9f2fe6659c23a2d8b18edf"},
    {"id": "menace", "name": "Menace", "hash": "dbc21e222528e30dc88445314f7be6ff12d3aeebc3c192054fba7e3b3f8c77b1"},
    {"id": "prismarine", "name": "Prismarine", "hash": "d8f8d13a1adf9636a16c31d47f3ecc9bb8d8533108aa5ad2a01b13b1a0c55eac"},
    {"id": "pan", "name": "Pan", "hash": "28de4a81688ad18b49e735a273e086c18f1e3966956123ccb574034c06f5d336"},
    {"id": "cobalt", "name": "Cobalt", "hash": "ca35c56efe71ed290385f4ab5346a1826b546a54d519e6a3ff01efa01acce81"},
    {"id": "yearn", "name": "Yearn", "hash": "308b32a9e303155a0b4262f9e5483ad4a22e3412e84fe8385a0bdd73dc41fa89"},
    {"id": "15th-anniversary", "name": "15th Anniversary", "hash": "cd9d82ab17fd92022dbd4a86cde4c382a7540e117fae7b9a2853658505a80625"},
    {"id": "mcc-15th-year", "name": "MCC 15th Year", "hash": "56c35628fe1c4d59dd52561a3d03bfa4e1a76d397c8b9c476c2f77cb6aebb1df"},
    {"id": "founders", "name": "Founder's", "hash": "99aba02ef05ec6aa4d42db8ee43796d6cd50e4b2954ab29f0caeb85f96bf52a1"},
    {"id": "purple-heart", "name": "Purple Heart", "hash": "cb40a92e32b57fd732a00fc325e7afb00a7ca74936ad50d8e860152e482cfbde"},
    {"id": "zombie-horse", "name": "Zombie Horse", "hash": "a3f6e4f14801f3ea55e3d95b9b4ef3b5e8802d947f669de93d6ec4b9354a436b"},
    {"id": "scrolls-champion", "name": "Scrolls Champion", "hash": "3efadf6510961830f9fcc077f19b4daf286d502b5f5aafbd807c7bbffcaca245"},
]
_CAPE_BY_ID = {c["id"]: c for c in MOJANG_CAPES}


def _cape_url(cape: dict) -> str:
    return CAPE_TEXTURE_URL + cape["hash"]
def _csl_find_config(instance_name: str):
    game = instance_game_dir(instance_name)
    primary = game / "CustomSkinLoader" / "CustomSkinLoader.json"
    if primary.is_file():
        return primary
    skip = {"saves", "mods", "resourcepacks", "shaderpacks", "screenshots", "logs",
            "crash-reports", "schematics"}
    stack = [(game, 0)]
    try:
        while stack:
            cur, depth = stack.pop()
            with os.scandir(cur) as it:
                for e in it:
                    low = e.name.lower()
                    if e.is_file(follow_symlinks=False):
                        if low == "customskinloader.json" and cur.name.lower() == "customskinloader":
                            return Path(e.path)
                    elif e.is_dir(follow_symlinks=False) and depth < 2 and low not in skip:
                        stack.append((Path(e.path), depth + 1))
    except OSError:
        pass
    return None


def _csl_config_path(instance_name: str) -> Path:
    return _csl_find_config(instance_name) or (
        instance_game_dir(instance_name) / "CustomSkinLoader" / "CustomSkinLoader.json")


def _csl_local_dir(instance_name: str) -> Path:
    return _csl_config_path(instance_name).parent / "LocalSkin"


CSL_CONFIG_VERSION = "15.0.1"
CSL_CONFIG_BUILD = 40
CSL_RUN_ONCE_MSG = "You must run the game at least once to use this feature."


def _csl_write_config(instance_name: str):
    path = _csl_config_path(instance_name)
    version, build = CSL_CONFIG_VERSION, CSL_CONFIG_BUILD
    try:
        old = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(old, dict):
            version = old.get("version", version)
            build = old.get("buildNumber", build)
    except (OSError, ValueError):
        pass
    cfg = {
        "version": version,
        "buildNumber": build,
        "loadlist": [
            {"name": "GameProfile", "type": "GameProfile"},
            {"name": "LocalSkin", "type": "Legacy", "checkPNG": False,
             "skin": "LocalSkin/skins/{USERNAME}.png", "model": "auto",
             "cape": "LocalSkin/capes/{USERNAME}.png", "elytra": "LocalSkin/elytras/{USERNAME}.png"},
        ],
        "enableTransparentSkin": True,
        "forceLoadAllTextures": True,
        "enableCape": True,
        "threadPoolSize": 8,
        "enableLogStdOut": False,
        "cacheExpiry": 30,
        "forceUpdateSkull": False,
        "enableLocalProfileCache": False,
        "enableCacheAutoClean": False,
        "forceDisableCache": False,
    }
    _write_bytes_atomic(path, (json.dumps(cfg, indent=2) + "\n").encode("utf-8"))


def _norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _mod_present(instance_name: str, needle: str) -> bool:
    mods = instance_mods_dir(instance_name)
    if not mods.is_dir():
        return False
    n = _norm_name(needle)
    return any(p.is_file() and p.suffix.lower() == ".jar" and n in _norm_name(p.name) for p in mods.iterdir())


def _csl_installed(instance_name: str) -> bool:
    return _mod_present(instance_name, "customskinloader")


def _write_bytes_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


CATALOG_REPO = "snigglaberry/critical-skin-api"
CATALOG_API = f"https://api.github.com/repos/{CATALOG_REPO}/contents/skin"
CATALOG_RAW_PREFIX = f"/{CATALOG_REPO}/"
CATALOG_TTL = 900
_catalog_cache = {}
_catalog_lock = threading.Lock()


def _catalog_cached(key, loader, force=False):
    now = time.time()
    if not force:
        with _catalog_lock:
            hit = _catalog_cache.get(key)
        if hit and now - hit[0] < CATALOG_TTL:
            return hit[1]
    data = loader()
    with _catalog_lock:
        _catalog_cache[key] = (now, data)
    return data


def _catalog_listing(sub=""):
    url = CATALOG_API + (("/" + urllib.parse.quote(sub)) if sub else "")
    data = http_get_json(url, timeout=20, retries=1)
    if not isinstance(data, list):
        raise ValueError("Unexpected response from GitHub.")
    return data


def _catalog_error(exc) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return "GitHub is limiting requests right now. Try again in a little while."
        if exc.code == 404:
            return "The skin catalog couldn't be found."
    return "Couldn't reach the skin catalog. Check your connection."


def _catalog_raw_url(letter: str, fname: str) -> str:
    return (f"https://raw.githubusercontent.com/{CATALOG_REPO}/main/skin/"
            f"{urllib.parse.quote(letter)}/{urllib.parse.quote(fname)}")


def _catalog_tree():
    try:
        data = http_get_json(f"https://api.github.com/repos/{CATALOG_REPO}/git/trees/main?recursive=1",
                             timeout=30, retries=1)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise
        return None
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("truncated"):
        return None
    out = []
    for node in data.get("tree") or []:
        parts = (node.get("path") or "").split("/")
        if (node.get("type") == "blob" and len(parts) == 3 and parts[0] == "skin"
                and parts[2].lower().endswith(".png")):
            out.append({"name": parts[2][:-4], "url": _catalog_raw_url(parts[1], parts[2])})
    return out


def _catalog_walk():
    letters = [i["name"] for i in _catalog_listing() if i.get("type") == "dir" and i.get("name")]

    def one(letter):
        out = []
        for i in _catalog_listing(letter):
            fname = i.get("name") or ""
            if i.get("type") != "file" or not fname.lower().endswith(".png"):
                continue
            url = i.get("download_url") or _catalog_raw_url(letter, fname)
            if _catalog_url_ok(url):
                out.append({"name": fname[:-4], "url": url})
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, letters))
    return [s for part in results for s in part]


def _catalog_url_ok(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url or "")
    except Exception:
        return False
    path = urllib.parse.unquote(p.path)
    return (p.scheme == "https" and p.hostname == "raw.githubusercontent.com"
            and path.startswith(CATALOG_RAW_PREFIX) and ".." not in path)


LABY_SEARCH_URL = "https://laby.net/api/v3/search/textures/skin"
LABY_TAGS_URL = "https://laby.net/api/v3/tags"
LABY_TAG_URL = "https://laby.net/api/v3/tag/{tag_id}"
LABY_ORDERS = ("most_used", "latest", "trending_24h", "trending_7d", "trending_30d")
LABY_PAGE_SIZE = 40
LABY_TAG_PAGE_SIZE = 40
LABY_TAG_LOOKUP_PAGES = 20
LABY_SEARCH_SCAN_PAGES = 20
LABY_HASH_RE = re.compile(r"^[0-9a-f]{16,64}$")
LABY_TEXTURE_URLS = (
    "https://texture.laby.net/{h}.png",
)
_laby_tpl = {"value": None}
_laby_tpl_lock = threading.Lock()


def _laby_host_ok(host: str) -> bool:
    return host == "laby.net" or host.endswith(".laby.net") or host == "textures.minecraft.net"


def _laby_url_ok(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url or "")
    except Exception:
        return False
    return (p.scheme == "https" and _laby_host_ok(p.hostname or "")
            and "/render/" not in p.path and ".." not in urllib.parse.unquote(p.path))


def _skin_url_ok(url: str) -> bool:
    return _catalog_url_ok(url) or _laby_url_ok(url)


def _fetch_skin_png(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/png,*/*"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(200_001)
    except Exception:
        return None
    if len(data) > 200_000 or not data.startswith(PNG_SIG) or len(data) < 33:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return data if (width, height) in ((64, 64), (64, 32)) else None


def _laby_template(sample_hash: str):
    with _laby_tpl_lock:
        if _laby_tpl["value"]:
            return _laby_tpl["value"]
    for tpl in LABY_TEXTURE_URLS:
        if _fetch_skin_png(tpl.format(h=sample_hash)):
            with _laby_tpl_lock:
                _laby_tpl["value"] = tpl
            return tpl
    return None


def _laby_name(tags: str) -> str:
    seen, words = set(), []
    for w in (tags or "").split():
        if w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    return " ".join(words[:3]) or "Skin"


def _laby_tag_for_query(query: str, force: bool = False):
    query = (query or "").strip().casefold()
    if not query:
        return None
    tags, matches = [], []
    for page in range(LABY_TAG_LOOKUP_PAGES):
        offset = page * LABY_TAG_PAGE_SIZE
        page_tags = _catalog_cached(
            f"laby:tags:{offset}:{LABY_TAG_PAGE_SIZE}",
            lambda offset=offset: http_get_json(
                f"{LABY_TAGS_URL}?{urllib.parse.urlencode({'offset': offset, 'size': LABY_TAG_PAGE_SIZE})}",
                timeout=20, retries=1),
            force,
        )
        if not isinstance(page_tags, list):
            raise ValueError("Unexpected tag list from laby.net.")
        tags.extend(page_tags)
        matches = [tag for tag in tags if isinstance(tag, dict)
                   and query == str(tag.get("name") or "").casefold()
                   and str(tag.get("id") or "").isdigit()]
        if matches or len(page_tags) < LABY_TAG_PAGE_SIZE:
            break
    if not matches:
        return None
    matches.sort(key=lambda tag: -int(tag.get("use_count") or 0))
    return matches[0]


def _laby_fetch_page(offset: int, size: int, order: str, search: str = "", force: bool = False,
                     model: str = "all"):
    terms = [term.strip().casefold() for term in re.split(r"[,;]", search or "") if term.strip()]
    if len(terms) > 5:
        raise ValueError("Search for up to five tags at a time.")
    if model not in ("all", "slim", "classic"):
        model = "all"
    tag = _laby_tag_for_query(terms[0], force) if len(terms) == 1 else None
    tag_name = str(tag.get("name") or terms[0]) if tag else ""
    matches, has_more = [], False

    def matches_filters(item, check_tags=True):
        if not isinstance(item, dict):
            return False
        item_tags = str((item or {}).get("tags") or "").casefold()
        if check_tags and terms and not all(term in item_tags for term in terms):
            return False
        is_slim = item.get("slim") is True
        if model == "slim" and not is_slim:
            return False
        if model == "classic" and is_slim:
            return False
        return True

    if tag:
        scan_offset = 0
        exhausted = False
        for _ in range(LABY_SEARCH_SCAN_PAGES):
            query = urllib.parse.urlencode({"size": LABY_PAGE_SIZE, "offset": scan_offset})
            data = _catalog_cached(
                f"laby:tag-feed:{tag['id']}:{scan_offset}",
                lambda query=query, tag_id=int(tag["id"]): http_get_json(
                    f"{LABY_TAG_URL.format(tag_id=tag_id)}?{query}", timeout=20, retries=1),
                force,
            )
            if not isinstance(data, list):
                raise ValueError("Unexpected response from laby.net.")
            matches.extend(item for item in data if matches_filters(item, check_tags=False))
            if len(matches) > offset + size:
                break
            if len(data) < LABY_PAGE_SIZE:
                exhausted = True
                break
            scan_offset += len(data)
        has_more = len(matches) > offset + size or not exhausted and len(matches) >= offset + size
    elif not terms and model == "all":
        query = urllib.parse.urlencode({"size": size, "offset": offset, "order": order})
        data = http_get_json(f"{LABY_SEARCH_URL}?{query}", timeout=20, retries=1)
        feed_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(feed_results, list):
            raise ValueError("Unexpected response from laby.net.")
        matches = feed_results
        has_more = len(feed_results) >= size
    else:
        scan_offset = 0
        exhausted = False
        for _ in range(LABY_SEARCH_SCAN_PAGES):
            query = urllib.parse.urlencode({"size": LABY_PAGE_SIZE, "offset": scan_offset, "order": order})
            feed = _catalog_cached(
                f"laby:search-feed:{order}:{scan_offset}",
                lambda query=query: http_get_json(f"{LABY_SEARCH_URL}?{query}", timeout=20, retries=1),
                force,
            )
            feed_results = feed.get("results") if isinstance(feed, dict) else None
            if not isinstance(feed_results, list):
                raise ValueError("Unexpected response from laby.net.")
            matches.extend(item for item in feed_results if matches_filters(item))
            if len(matches) > offset + size:
                break
            if len(feed_results) < LABY_PAGE_SIZE:
                exhausted = True
                break
            scan_offset += len(feed_results)
        has_more = len(matches) > offset + size or not exhausted and len(matches) >= offset + size

    results = matches[offset:offset + size]
    skins, seen = [], set()
    for r in results:
        if not isinstance(r, dict):
            continue
        h = str(r.get("image_hash") or "").lower()
        if not LABY_HASH_RE.match(h) or h in seen:
            continue
        seen.add(h)
        tags = tag_name or str(r.get("tags") or "")
        try:
            uses = max(0, int(r.get("use_count") or 0))
        except (TypeError, ValueError):
            uses = 0
        skins.append({"hash": h, "name": _laby_name(tags), "tags": tags,
                      "uses": uses, "slim": r.get("slim") is True})
    tpl = _laby_template(skins[0]["hash"]) if skins else None
    if skins and not tpl:
        raise LookupError("laby-texture")
    for sk in skins:
        sk["url"] = tpl.format(h=sk["hash"])
    return skins, has_more


def _laby_error(exc) -> str:
    if isinstance(exc, ValueError) and str(exc).startswith("Search for up to"):
        return str(exc)
    if isinstance(exc, LookupError):
        return "Couldn't find laby.net's skin download address. Try the Critical Skin API source for now."
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "laby.net is limiting requests right now. Try again in a little while."
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
        return "laby.net requires you to sign in to view more skin results."
    return "Couldn't reach laby.net. Check your connection."


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


SERVERS_DIR = DATA_DIR / "servers"
SERVERS_DIR.mkdir(parents=True, exist_ok=True)
SERVER_META_FILE = "crit_server.json"
SERVER_SOFTWARE = ("vanilla", "paper", "fabric")
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
PAPER_USER_AGENT = "CritLauncher/1.6 (https://github.com/snigglaberry/crit-launcher)"
FABRIC_INSTALLER_LIST_URL = "https://meta.fabricmc.net/v2/versions/installer"
EULA_URL = "https://aka.ms/MinecraftEULA"
PLUGIN_LOADERS = ["paper", "spigot", "bukkit"]
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,16}$")

GAMERULES = [
    ("keepInventory", "Keep inventory", "bool", False, ["keep_inventory"]),
    ("mobGriefing", "Mob griefing", "bool", True, ["mob_griefing"]),
    ("doDaylightCycle", "Daylight cycle", "bool", True, ["advance_time"]),
    ("doWeatherCycle", "Weather cycle", "bool", True, ["advance_weather"]),
    ("doMobSpawning", "Mob spawning", "bool", True, ["spawn_mobs"]),
    ("doFireTick", "Fire spreads", "bool", True, []),
    ("naturalRegeneration", "Natural regeneration", "bool", True, ["natural_health_regeneration"]),
    ("showDeathMessages", "Death messages", "bool", True, ["show_death_messages"]),
    ("announceAdvancements", "Announce advancements", "bool", True, ["show_advancement_messages"]),
    ("commandBlockOutput", "Command block output", "bool", True, ["command_block_output"]),
    ("doImmediateRespawn", "Immediate respawn", "bool", False, ["immediate_respawn"]),
    ("fallDamage", "Fall damage", "bool", True, ["fall_damage"]),
    ("fireDamage", "Fire damage", "bool", True, ["fire_damage"]),
    ("drowningDamage", "Drowning damage", "bool", True, ["drowning_damage"]),
    ("doInsomnia", "Phantoms spawn", "bool", True, ["spawn_phantoms"]),
    ("doPatrolSpawning", "Pillager patrols", "bool", True, ["spawn_patrols"]),
    ("doTraderSpawning", "Wandering traders", "bool", True, ["spawn_wandering_traders"]),
    ("disableRaids", "Disable raids", "bool", False, ["raids"]),
    ("playersSleepingPercentage", "Sleeping percentage", "int", 100, ["players_sleeping_percentage"]),
    ("randomTickSpeed", "Random tick speed", "int", 3, ["random_tick_speed"]),
    ("spawnRadius", "Spawn radius", "int", 10, ["respawn_radius"]),
]
_GAMERULE_IDS = {g[0]: g for g in GAMERULES}

WORLD_TYPES = ["minecraft:normal", "minecraft:flat", "minecraft:large_biomes", "minecraft:amplified"]
LEGACY_WORLD_TYPES = {"minecraft:normal": "default", "minecraft:flat": "flat",
                      "minecraft:large_biomes": "largeBiomes", "minecraft:amplified": "amplified"}


def server_dir(name: str) -> Path:
    return SERVERS_DIR / name


def read_server_meta(name: str):
    try:
        return json.loads((server_dir(name) / SERVER_META_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_server_meta(name: str, meta: dict):
    d = server_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (SERVER_META_FILE + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp, d / SERVER_META_FILE)


def mc_tuple(version: str):
    nums = [int(n) for n in re.findall(r"\d+", (version or "").split("-")[0])[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _prop_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).replace("\r", " ").replace("\n", " ")
    return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in s)


def read_properties(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v
    except OSError:
        pass
    return out


def write_properties(path: Path, updates: dict):
    lines, seen = [], set()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line and not line.startswith("#") else None
            if key in updates:
                lines.append(f"{key}={_prop_value(updates[key])}")
                seen.add(key)
            else:
                lines.append(line)
    else:
        lines.append("#Minecraft server properties (written by Crit Launcher)")
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={_prop_value(v)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clamp_int(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def build_server_properties(cfg: dict, version: str) -> dict:
    wt = cfg.get("level_type") if cfg.get("level_type") in WORLD_TYPES else "minecraft:normal"
    if mc_tuple(version) < (1, 16, 0):
        wt = LEGACY_WORLD_TYPES[wt]
    gm = cfg.get("gamemode") if cfg.get("gamemode") in ("survival", "creative", "adventure", "spectator") else "survival"
    diff = cfg.get("difficulty") if cfg.get("difficulty") in ("peaceful", "easy", "normal", "hard") else "easy"
    hardcore = bool(cfg.get("hardcore"))
    props = {
        "motd": (cfg.get("motd") or "A Crit Launcher server")[:120],
        "server-port": _clamp_int(cfg.get("port"), 1024, 65535, 25565),
        "max-players": _clamp_int(cfg.get("max_players"), 1, 1000, 20),
        "gamemode": gm,
        "difficulty": "hard" if hardcore else diff,
        "hardcore": hardcore,
        "pvp": bool(cfg.get("pvp", True)),
        "online-mode": bool(cfg.get("online_mode", False)),
        "allow-flight": bool(cfg.get("allow_flight", False)),
        "allow-nether": bool(cfg.get("allow_nether", True)),
        "spawn-monsters": bool(cfg.get("spawn_monsters", True)),
        "spawn-animals": bool(cfg.get("spawn_animals", True)),
        "spawn-npcs": bool(cfg.get("spawn_npcs", True)),
        "enable-command-block": bool(cfg.get("enable_command_block", False)),
        "generate-structures": bool(cfg.get("generate_structures", True)),
        "white-list": bool(cfg.get("white_list", False)),
        "view-distance": _clamp_int(cfg.get("view_distance"), 2, 32, 10),
        "simulation-distance": _clamp_int(cfg.get("simulation_distance"), 2, 32, 10),
        "spawn-protection": _clamp_int(cfg.get("spawn_protection"), 0, 1000, 16),
        "level-name": re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (cfg.get("level_name") or "world")).strip() or "world",
        "level-seed": (cfg.get("level_seed") or "").strip()[:64],
        "level-type": wt,
    }
    return props


def gamerule_candidates(camel: str, version: str) -> list:
    g = _GAMERULE_IDS.get(camel)
    if not g:
        return []
    names = [camel] + list(g[4])
    if mc_tuple(version) >= (1, 21, 11):
        names = list(g[4]) + [camel]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _has_java_error(line: str) -> bool:
    return "Incorrect argument" in line or "Unknown or incomplete" in line or "<--[HERE]" in line


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


def read_plugin_jar(path: Path) -> dict:
    info = {"name": None, "version": None, "description": None, "authors": None,
            "loader": "paper", "_icon": None}
    try:
        with zipfile.ZipFile(path) as zf:
            raw = _zip_read(zf, "paper-plugin.yml") or _zip_read(zf, "plugin.yml")
            if raw:
                text = raw.decode("utf-8-sig", "replace")

                def grab(key):
                    m = re.search(rf"^{key}\s*:\s*['\"]?(.+?)['\"]?\s*$", text, re.M)
                    return m.group(1) if m else None
                info.update(name=grab("name"), version=grab("version"),
                            description=grab("description"), authors=grab("author"))
    except Exception:
        logging.info("Couldn't read plugin metadata from %s", path.name)
    info["name"] = _clean_text(info["name"], 80)
    info["description"] = _clean_text(info["description"])
    info["authors"] = _authors_text(info["authors"])
    return info


class ProcSampler:

    def __init__(self, proc):
        self.proc = proc
        self.pid = proc.pid
        self._ps = None
        self._last = None
        try:
            import psutil
            self._ps = psutil.Process(self.pid)
            self._ps.cpu_percent(None)
        except Exception:
            self._ps = None

    def sample(self):
        ncpu = os.cpu_count() or 1
        try:
            if self._ps is not None:
                return self._ps.cpu_percent(None) / ncpu, self._ps.memory_info().rss
            if os.name == "nt":
                return self._sample_windows(ncpu)
            if os.path.exists(f"/proc/{self.pid}/stat"):
                return self._sample_linux(ncpu)
        except Exception:
            pass
        return None, None

    def _sample_windows(self, ncpu):
        import ctypes
        from ctypes import wintypes
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        handle = wintypes.HANDLE(int(self.proc._handle))

        class FT(ctypes.Structure):
            _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c, e, kt, ut = FT(), FT(), FT(), FT()
        k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
        cpu_100ns = ((kt.hi << 32) | kt.lo) + ((ut.hi << 32) | ut.lo)
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
        now = time.time()
        cpu = None
        if self._last:
            dt = now - self._last[0]
            if dt > 0:
                cpu = max(0.0, (cpu_100ns - self._last[1]) / 1e7 / dt / ncpu * 100)
        self._last = (now, cpu_100ns)
        return cpu, int(pmc.WorkingSetSize)

    def _sample_linux(self, ncpu):
        parts = Path(f"/proc/{self.pid}/stat").read_text().rsplit(")", 1)[1].split()
        ticks = int(parts[11]) + int(parts[12])
        hz = os.sysconf("SC_CLK_TCK")
        rss = int(Path(f"/proc/{self.pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        now = time.time()
        cpu = None
        if self._last:
            dt = now - self._last[0]
            if dt > 0:
                cpu = max(0.0, (ticks - self._last[1]) / hz / dt / ncpu * 100)
        self._last = (now, ticks)
        return cpu, rss


class ServerRuntime:
    def __init__(self, name):
        self.name = name
        self.proc = None
        self.status = "starting"
        self.started = time.time()
        self.log = []
        self.seq = 0
        self.pending = []
        self.players = {}
        self.exited = threading.Event()
        self.lock = threading.Lock()
        self.waiters = []
        self.swallow_until = 0.0
        self.eula_needed = False
        self.tick_ok = True
        self.tps = None
        self.mspt = None
        self.cpu = None
        self.ram = None
        self.stopping = False
        self.restart_after = False


TICK_NOISE_RE = re.compile(r"(The game is running|Target tick rate|Average time per tick|Percentiles|"
                           r"Unknown or incomplete command|<--\[HERE\]|Incorrect argument)")
LOG_JOIN_RE = re.compile(r"\]: ([A-Za-z0-9_.]{1,16}) joined the game\s*$")
LOG_LEAVE_RE = re.compile(r"\]: ([A-Za-z0-9_.]{1,16}) left the game\s*$")


class ServerMixin:
    def _srv_init(self):
        self._srv_rt: dict = {}
        self._srv_installing: set = set()
        self._srv_lock = threading.RLock()

    def _srv_status(self, name):
        with self._srv_lock:
            if name in self._srv_installing:
                return "installing"
            rt = self._srv_rt.get(name)
        return rt.status if rt and rt.status != "offline" else "offline"

    def _srv_summary(self, meta) -> dict:
        name = meta["name"]
        with self._srv_lock:
            rt = self._srv_rt.get(name)
        live = rt is not None and rt.status != "offline"
        props = read_properties(server_dir(name) / "server.properties")
        return {
            "name": name, "software": meta.get("software"), "version": meta.get("version"),
            "loader_version": meta.get("loader_version"), "installed": bool(meta.get("installed")),
            "port": _clamp_int(props.get("server-port"), 1, 65535, meta.get("port", 25565)),
            "max_players": _clamp_int(props.get("max-players"), 1, 1000, 20),
            "memory_mb": meta.get("memory_mb", 2048), "status": self._srv_status(name),
            "players": len(rt.players) if live else 0,
            "uptime": int(time.time() - rt.started) if live and rt.status == "online" else 0,
            "created": meta.get("created"),
            "motd": str(props.get("motd", "A Crit Launcher server"))[:120],
        }

    def _srv_payload(self) -> list:
        items = []
        if SERVERS_DIR.is_dir():
            for d in SERVERS_DIR.iterdir():
                meta = read_server_meta(d.name) if d.is_dir() else None
                if meta:
                    meta["name"] = d.name
                    items.append(self._srv_summary(meta))
        items.sort(key=lambda s: (s.get("created") or 0))
        return items

    def _srv_push(self):
        self.emit("serversChanged", {"servers": self._srv_payload()})

    def get_servers(self):
        try:
            return {"success": True, "servers": self._srv_payload(),
                    "gamerules": [{"id": g[0], "label": g[1], "type": g[2], "default": g[3]} for g in GAMERULES]}
        except Exception:
            log_exception("get_servers")
            return {"success": False, "error": "Couldn't read your servers."}

    def get_server_versions(self, software):
        try:
            releases = [v["id"] for v in meta_cache.get_manifest()["versions"] if v["type"] == "release"]
            if software == "paper":
                versions = fetch_paper_versions()
            elif software == "fabric":
                allowed = set(meta_cache.get_fabric_game_versions())
                versions = [v for v in releases if v in allowed]
            else:
                versions = releases
            return {"success": True, "versions": versions}
        except Exception:
            log_exception("get_server_versions")
            return {"success": False, "error": "Couldn't load versions for that server type."}

    def create_server(self, config):
        try:
            cfg = dict(config or {})
            try:
                name = sanitize_instance_name(cfg.get("name"))
            except ValueError as exc:
                return {"success": False, "error": str(exc).replace("Instance", "Server")}
            software = cfg.get("software")
            if software not in SERVER_SOFTWARE:
                return {"success": False, "error": "Pick Vanilla, Paper or Fabric."}
            version = cfg.get("version")
            if not version:
                return {"success": False, "error": "Select a Minecraft version."}
            if software == "fabric" and not cfg.get("loader_version"):
                return {"success": False, "error": "Select a Fabric loader version."}
            if server_dir(name).exists():
                return {"success": False, "error": "A server with that name already exists."}

            gamerules = {}
            for k, v in (cfg.get("gamerules") or {}).items():
                g = _GAMERULE_IDS.get(k)
                if not g:
                    continue
                gamerules[k] = bool(v) if g[2] == "bool" else _clamp_int(v, 0, 100000, g[3])
            meta = {
                "name": name, "software": software, "version": version,
                "loader_version": cfg.get("loader_version") if software == "fabric" else None,
                "installed": False, "memory_mb": _clamp_int(cfg.get("memory_mb"), 512, 65536, 2048),
                "port": _clamp_int(cfg.get("port"), 1024, 65535, 25565),
                "gamerules": gamerules, "gamerules_applied": False,
                "java_component": None, "java_major": None, "created": time.time(),
                "properties": build_server_properties(cfg, version),
            }
            write_server_meta(name, meta)
            with self._srv_lock:
                self._srv_installing.add(name)
            self._srv_push()
            threading.Thread(target=self._srv_install_worker, args=(name,), daemon=True).start()
            return {"success": True}
        except Exception as exc:
            log_exception("create_server")
            return {"success": False, "error": f"Failed to create server: {exc}"}

    def _srv_install_worker(self, name):
        meta = read_server_meta(name)
        try:
            emit = lambda ev, data: self.emit(ev, dict(data, server=name))
            tracker = ProgressTracker(emit)
            tracker.event = "serverInstallProgress"
            tracker.set_task("Fetching version metadata", force=True)
            manifest = meta_cache.get_manifest()
            entry = next((v for v in manifest["versions"] if v["id"] == meta["version"]), None)
            if not entry:
                raise RuntimeError(f"Minecraft {meta['version']} was not found.")
            vjson = meta_cache.get_version_json(meta["version"], entry["url"])
            java_info = vjson.get("javaVersion") or dict(DEFAULT_JAVA_INFO)
            if not resolve_java(java_info, allow_download=True, tracker=tracker):
                raise RuntimeError("Could not find or download a suitable Java runtime.")
            meta["java_component"] = java_info.get("component")
            meta["java_major"] = java_info.get("majorVersion")

            jar = server_dir(name) / "server.jar"
            tracker.set_task(f"Downloading {meta['software'].capitalize()} {meta['version']}", force=True)
            if meta["software"] == "vanilla":
                dl = vjson.get("downloads", {}).get("server")
                if not dl:
                    raise RuntimeError("Mojang has no server download for this version.")
                tracker.reset(dl.get("size") or 1)
                http_download(dl["url"], jar, dl.get("size"), dl.get("sha1"), tracker.add)
            elif meta["software"] == "paper":
                dl = paper_latest_build(meta["version"])
                tracker.reset(dl.get("size") or 1)
                http_download(dl["url"], jar, dl.get("size"), None, tracker.add, user_agent=PAPER_USER_AGENT)
                sha = (dl.get("checksums") or {}).get("sha256")
                if sha:
                    h = hashlib.sha256(jar.read_bytes()).hexdigest()
                    if h != sha:
                        jar.unlink(missing_ok=True)
                        raise RuntimeError("Paper download failed its checksum.")
            else:
                installers = http_get_json(FABRIC_INSTALLER_LIST_URL)
                inst = next((i for i in installers if i.get("stable")), installers[0])["version"]
                url = (f"https://meta.fabricmc.net/v2/versions/loader/{urllib.parse.quote(meta['version'])}/"
                       f"{urllib.parse.quote(meta['loader_version'])}/{urllib.parse.quote(inst)}/server/jar")
                tracker.reset(1)
                http_download(url, jar, None, None, tracker.add)
            (server_dir(name) / "mods").mkdir(exist_ok=True)
            if meta["software"] == "paper":
                (server_dir(name) / "plugins").mkdir(exist_ok=True)
            write_properties(server_dir(name) / "server.properties", meta.pop("properties", {}))
            meta["installed"] = True
            write_server_meta(name, meta)
            self.emit("serverInstallFinished", {"server": name})
        except Exception as exc:
            log_exception("server install")
            self.emit("serverInstallError", {"server": name, "message": f"Couldn't install {name}: {exc}"})
            shutil.rmtree(server_dir(name), ignore_errors=True)
        finally:
            with self._srv_lock:
                self._srv_installing.discard(name)
            self._srv_push()

    def delete_server(self, name):
        try:
            if self._srv_status(name) != "offline":
                return {"success": False, "error": "Stop the server before deleting it."}
            target = server_dir(name)
            if not target.exists() or not is_within(SERVERS_DIR, target):
                return {"success": False, "error": "Server not found."}
            shutil.rmtree(target, ignore_errors=True)
            with self._srv_lock:
                self._srv_rt.pop(name, None)
            self._srv_push()
            return {"success": True}
        except Exception:
            log_exception("delete_server")
            return {"success": False, "error": "Failed to delete the server."}

    def open_server_folder(self, name):
        try:
            d = server_dir(name)
            if not d.is_dir() or not is_within(SERVERS_DIR, d):
                return {"success": False, "error": "Server not found."}
            open_in_file_manager(d)
            return {"success": True}
        except Exception:
            log_exception("open_server_folder")
            return {"success": False, "error": "Couldn't open the folder."}

    def _srv_eula_accepted(self, name) -> bool:
        try:
            text = (server_dir(name) / "eula.txt").read_text(encoding="utf-8", errors="replace")
            return bool(re.search(r"^eula\s*=\s*true\s*$", text, re.M | re.I))
        except OSError:
            return False

    def accept_eula(self, name):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            stamp = time.strftime("%a %b %d %H:%M:%S %Z %Y")
            (server_dir(name) / "eula.txt").write_text(
                "#By changing the setting below to TRUE you are indicating your agreement to our EULA "
                f"({EULA_URL}).\n#{stamp}\neula=true\n", encoding="utf-8")
            return self.start_server(name)
        except Exception:
            log_exception("accept_eula")
            return {"success": False, "error": "Couldn't update eula.txt."}

    def start_server(self, name):
        try:
            meta = read_server_meta(name)
            if not meta or not meta.get("installed"):
                return {"success": False, "error": "This server isn't installed yet."}
            with self._srv_lock:
                rt = self._srv_rt.get(name)
                if (rt and rt.status != "offline") or name in self._srv_installing:
                    return {"success": False, "error": "Server is already running."}
            java = resolve_java(java_info_from_meta(meta), allow_download=False)
            if not java:
                return {"success": False, "error": f"Java {meta.get('java_major') or ''} was not found. Recreate the server."}
            mem = _clamp_int(meta.get("memory_mb"), 512, 65536, 2048)
            cmd = [console_java(java), f"-Xmx{mem}M", f"-Xms{min(mem, 1024)}M", "-jar", "server.jar", "nogui"]
            rt = ServerRuntime(name)
            rt.proc = subprocess.Popen(cmd, cwd=str(server_dir(name)), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       creationflags=CREATE_NO_WINDOW, shell=False)
            with self._srv_lock:
                self._srv_rt[name] = rt
            self._srv_line(rt, f"[Crit Launcher] Starting {meta['software']} {meta['version']} with {mem} MB...")
            threading.Thread(target=self._srv_reader, args=(rt, meta), daemon=True).start()
            threading.Thread(target=self._srv_flusher, args=(rt,), daemon=True).start()
            threading.Thread(target=self._srv_monitor, args=(rt,), daemon=True).start()
            self._srv_push()
            return {"success": True}
        except Exception as exc:
            log_exception("start_server")
            return {"success": False, "error": f"Couldn't start the server: {exc}"}

    def _srv_line(self, rt, text):
        with rt.lock:
            rt.seq += 1
            rt.log.append((rt.seq, text))
            if len(rt.log) > 3000:
                del rt.log[:500]
            rt.pending.append((rt.seq, text))

    def _srv_flusher(self, rt):
        while True:
            time.sleep(0.15)
            with rt.lock:
                batch, rt.pending = rt.pending, []
            if batch:
                self.emit("serverLog", {"server": rt.name, "lines": batch})
            if rt.exited.is_set() and not batch:
                return

    def _srv_reader(self, rt, meta):
        name = rt.name
        try:
            for raw in iter(rt.proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    continue
                now = time.time()
                with rt.lock:
                    waiters = list(rt.waiters)
                consumed = False
                for w in waiters:
                    m = w["rx"].search(line)
                    if m:
                        w["match"] = m
                        w["event"].set()
                        consumed = True
                        break
                if not consumed and now < rt.swallow_until and TICK_NOISE_RE.search(line):
                    if "Unknown or incomplete" in line:
                        rt.tick_ok = False
                    consumed = True
                if consumed:
                    continue
                self._srv_line(rt, line)
                if "You need to agree to the EULA" in line:
                    rt.eula_needed = True
                if rt.status == "starting" and "Done (" in line and "For help" in line:
                    rt.status = "online"
                    rt.started = time.time()
                    self._srv_push()
                    threading.Thread(target=self._srv_apply_gamerules, args=(rt, meta), daemon=True).start()
                m = LOG_JOIN_RE.search(line)
                if m:
                    rt.players[m.group(1)] = time.time()
                    self.emit("serverPlayers", {"server": name, "players": list(rt.players)})
                    self._srv_push()
                m = LOG_LEAVE_RE.search(line)
                if m:
                    rt.players.pop(m.group(1), None)
                    self.emit("serverPlayers", {"server": name, "players": list(rt.players)})
                    self._srv_push()
        except Exception:
            log_exception("server reader")
        finally:
            code = rt.proc.wait()
            rt.status = "offline"
            rt.players.clear()
            rt.exited.set()
            self._srv_line(rt, f"[Crit Launcher] Server stopped (exit code {code}).")
            self.emit("serverPlayers", {"server": name, "players": []})
            self._srv_push()
            if rt.eula_needed and not self._srv_eula_accepted(name):
                self.emit("serverEula", {"server": name})
            elif code != 0 and not rt.stopping:
                self.emit("serverCrashed", {"server": name,
                                            "message": f"{name} stopped unexpectedly (exit code {code}). Check the console."})
            if rt.restart_after:
                self.start_server(name)

    def _srv_send(self, rt, command: str) -> bool:
        command = (command or "").replace("\r", " ").replace("\n", " ").strip()
        if not command or rt.proc is None or rt.proc.poll() is not None:
            return False
        try:
            rt.proc.stdin.write((command + "\n").encode("utf-8"))
            rt.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _srv_ask(self, rt, command, pattern, timeout=3.0):
        w = {"rx": re.compile(pattern), "event": threading.Event(), "match": None}
        with rt.lock:
            rt.waiters.append(w)
        try:
            if not self._srv_send(rt, command):
                return None
            w["event"].wait(timeout)
            return w["match"]
        finally:
            with rt.lock:
                if w in rt.waiters:
                    rt.waiters.remove(w)

    def _srv_apply_gamerules(self, rt, meta):
        try:
            if meta.get("gamerules_applied") or not meta.get("gamerules"):
                return
            time.sleep(1.0)
            version = meta.get("version", "")
            for camel, value in meta["gamerules"].items():
                for cand in gamerule_candidates(camel, version):
                    v = (not value) if (camel == "disableRaids" and cand == "raids") else value
                    val = ("true" if v else "false") if isinstance(v, bool) else str(v)
                    w = {"rx": re.compile(r"(Incorrect argument|Unknown or incomplete|<--\[HERE\]|Game ?rule .* (is now|has been updated))",
                                          re.I), "event": threading.Event(), "match": None}
                    with rt.lock:
                        rt.waiters.append(w)
                    self._srv_send(rt, f"gamerule {cand} {val}")
                    w["event"].wait(1.5)
                    with rt.lock:
                        if w in rt.waiters:
                            rt.waiters.remove(w)
                    bad = w["match"] is not None and _has_java_error(w["match"].group(0))
                    if not bad:
                        break
            meta = read_server_meta(rt.name) or meta
            meta["gamerules_applied"] = True
            write_server_meta(rt.name, meta)
            self._srv_line(rt, "[Crit Launcher] Game rules applied.")
        except Exception:
            log_exception("apply gamerules")

    def _srv_monitor(self, rt):
        sampler = ProcSampler(rt.proc)
        last_tick = 0.0
        while not rt.exited.is_set():
            time.sleep(2.0)
            if rt.exited.is_set():
                break
            rt.cpu, rt.ram = sampler.sample()
            if rt.status == "online" and rt.tick_ok and time.time() - last_tick > 8:
                last_tick = time.time()
                rt.swallow_until = time.time() + 2.5
                m = self._srv_ask(rt, "tick query", r"Average time per tick:\s*([\d.]+)\s*ms", 2.0)
                if m:
                    rt.mspt = float(m.group(1))
                    rt.tps = min(20.0, 1000.0 / max(rt.mspt, 0.001))
            self.emit("serverStats", {
                "server": rt.name, "status": rt.status, "cpu": rt.cpu, "ram": rt.ram,
                "uptime": int(time.time() - rt.started) if rt.status == "online" else 0,
                "players": len(rt.players), "tps": rt.tps, "mspt": rt.mspt,
            })

    def stop_server(self, name, force=False):
        try:
            with self._srv_lock:
                rt = self._srv_rt.get(name)
            if not rt or rt.status == "offline":
                return {"success": False, "error": "Server is not running."}
            rt.stopping = True
            rt.status = "stopping"
            self._srv_push()
            if force:
                rt.proc.kill()
            else:
                self._srv_send(rt, "stop")
                threading.Thread(target=self._srv_kill_later, args=(rt,), daemon=True).start()
            return {"success": True}
        except Exception:
            log_exception("stop_server")
            return {"success": False, "error": "Couldn't stop the server."}

    def _srv_kill_later(self, rt):
        if not rt.exited.wait(45):
            try:
                rt.proc.kill()
            except OSError:
                pass

    def restart_server(self, name):
        with self._srv_lock:
            rt = self._srv_rt.get(name)
        if not rt or rt.status == "offline":
            return self.start_server(name)
        rt.restart_after = True
        return self.stop_server(name)

    def stop_all_servers(self):
        with self._srv_lock:
            runtimes = list(self._srv_rt.values())
        for rt in runtimes:
            if rt.status != "offline":
                rt.stopping = True
                self._srv_send(rt, "stop")
        for rt in runtimes:
            rt.exited.wait(20)

    def get_server_log(self, name):
        with self._srv_lock:
            rt = self._srv_rt.get(name)
        if not rt:
            return {"success": True, "lines": [], "status": "offline", "players": []}
        with rt.lock:
            lines = list(rt.log)
        return {"success": True, "lines": lines, "status": rt.status, "players": list(rt.players)}

    def send_server_command(self, name, command):
        with self._srv_lock:
            rt = self._srv_rt.get(name)
        if not rt or rt.status in ("offline",):
            return {"success": False, "error": "Server is offline."}
        command = (command or "").strip().lstrip("/")
        if not command:
            return {"success": False, "error": "Type a command first."}
        self._srv_line(rt, "> " + command)
        if not self._srv_send(rt, command):
            return {"success": False, "error": "Couldn't reach the server."}
        return {"success": True}

    def _srv_online_rt(self, name):
        with self._srv_lock:
            rt = self._srv_rt.get(name)
        return rt if rt and rt.status == "online" else None

    def get_server_players(self, name):
        try:
            rt = self._srv_online_rt(name)
            ops = set()
            try:
                ops = {o.get("name") for o in json.loads((server_dir(name) / "ops.json").read_text(encoding="utf-8"))}
            except Exception:
                pass
            return {"success": True, "online": rt is not None,
                    "players": [{"name": p, "op": p in ops} for p in (rt.players if rt else [])]}
        except Exception:
            log_exception("get_server_players")
            return {"success": False, "error": "Couldn't read players."}

    _SRV_BOOL_SETTINGS = {
        "offline_mode": None, "white_list": ("white-list", False), "pvp": ("pvp", True),
        "allow_flight": ("allow-flight", False), "allow_nether": ("allow-nether", True),
        "enable_command_block": ("enable-command-block", False),
        "spawn_monsters": ("spawn-monsters", True), "spawn_animals": ("spawn-animals", True),
        "spawn_npcs": ("spawn-npcs", True),
    }
    _SRV_INT_SETTINGS = {
        "port": ("server-port", 1024, 65535, 25565), "max_players": ("max-players", 1, 1000, 20),
        "view_distance": ("view-distance", 2, 32, 10), "simulation_distance": ("simulation-distance", 2, 32, 10),
        "spawn_protection": ("spawn-protection", 0, 1000, 16),
    }

    def get_server_settings(self, name):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            props = read_properties(server_dir(name) / "server.properties")
            s = {"motd": props.get("motd", "A Crit Launcher server"),
                 "memory_mb": _clamp_int(meta.get("memory_mb"), 512, 65536, 2048),
                 "gamemode": props.get("gamemode", "survival"), "difficulty": props.get("difficulty", "easy"),
                 "offline_mode": props.get("online-mode", "false").strip().lower() != "true"}
            for key, spec in self._SRV_BOOL_SETTINGS.items():
                if spec:
                    s[key] = props.get(spec[0], "true" if spec[1] else "false").strip().lower() == "true"
            for key, (prop, lo, hi, default) in self._SRV_INT_SETTINGS.items():
                s[key] = _clamp_int(props.get(prop), lo, hi, default)
            return {"success": True, "settings": s, "status": self._srv_status(name)}
        except Exception:
            log_exception("get_server_settings")
            return {"success": False, "error": "Couldn't read the server settings."}

    def update_server_settings(self, name, settings):
        try:
            meta = read_server_meta(name)
            if not meta or not meta.get("installed"):
                return {"success": False, "error": "This server isn't installed yet."}
            st = dict(settings or {})
            props = {}
            if "motd" in st:
                props["motd"] = str(st["motd"] or "A Crit Launcher server")[:120]
            if st.get("gamemode") in ("survival", "creative", "adventure", "spectator"):
                props["gamemode"] = st["gamemode"]
            if st.get("difficulty") in ("peaceful", "easy", "normal", "hard"):
                props["difficulty"] = st["difficulty"]
            if "offline_mode" in st:
                props["online-mode"] = not bool(st["offline_mode"])
            for key, spec in self._SRV_BOOL_SETTINGS.items():
                if spec and key in st:
                    props[spec[0]] = bool(st[key])
            for key, (prop, lo, hi, default) in self._SRV_INT_SETTINGS.items():
                if key in st:
                    props[prop] = _clamp_int(st[key], lo, hi, default)
            write_properties(server_dir(name) / "server.properties", props)
            if "memory_mb" in st:
                meta["memory_mb"] = _clamp_int(st["memory_mb"], 512, 65536, 2048)
            if "server-port" in props:
                meta["port"] = props["server-port"]
            write_server_meta(name, meta)
            self._srv_push()
            return {"success": True}
        except Exception as exc:
            log_exception("update_server_settings")
            return {"success": False, "error": f"Couldn't save the settings: {exc}"}

    def get_player_info(self, name, player):
        rt = self._srv_online_rt(name)
        if not rt or not PLAYER_NAME_RE.match(player or ""):
            return {"success": False, "error": "Player isn't online."}
        pat = rf"{re.escape(player)} has the following entity data: (.+?)\s*$"
        hp = self._srv_ask(rt, f"data get entity {player} Health", pat)
        gm = self._srv_ask(rt, f"data get entity {player} playerGameType", pat)
        out = {"success": True, "health": None, "gamemode": None}
        try:
            if hp:
                out["health"] = float(re.sub(r"[^\d.\-]", "", hp.group(1)))
            if gm:
                out["gamemode"] = ["survival", "creative", "adventure", "spectator"][int(re.sub(r"\D", "", gm.group(1)))]
        except (ValueError, IndexError):
            pass
        return out

    def player_action(self, name, player, action, value=None):
        try:
            rt = self._srv_online_rt(name)
            if not rt:
                return {"success": False, "error": "Server isn't running."}
            if not PLAYER_NAME_RE.match(player or ""):
                return {"success": False, "error": "Invalid player name."}
            meta = read_server_meta(name) or {}
            version = meta.get("version", "")
            cmds = []
            if action == "gamemode":
                if value not in ("survival", "creative", "adventure", "spectator"):
                    return {"success": False, "error": "Unknown game mode."}
                cmds = [f"gamemode {value} {player}"]
            elif action in ("op", "deop", "kill"):
                cmds = [f"{action} {player}"]
            elif action == "heal":
                cmds = [f"effect give {player} minecraft:instant_health 1 30 true",
                        f"effect give {player} minecraft:saturation 1 30 true"]
            elif action == "feed":
                cmds = [f"effect give {player} minecraft:saturation 1 30 true"]
            elif action == "kick":
                cmds = [f"kick {player} Kicked from Crit Launcher"]
            elif action == "ban":
                cmds = [f"ban {player} Banned from Crit Launcher"]
            elif action == "set_health":
                hp = _clamp_int(value, 1, 1024, 20)
                cmds = [f"effect give {player} minecraft:instant_health 1 30 true"]
                if hp < 20:
                    cmds.append(("DELAY", 0.7))
                    cmds.append(f"damage {player} {20 - hp} minecraft:generic_kill")
            elif action == "max_health":
                mh = _clamp_int(value, 1, 1024, 20)
                attr = "minecraft:max_health" if mc_tuple(version) >= (1, 21, 2) else "minecraft:generic.max_health"
                cmds = [f"attribute {player} {attr} base set {mh}"]
            else:
                return {"success": False, "error": "Unknown action."}
            for c in cmds:
                if isinstance(c, tuple):
                    time.sleep(c[1])
                    continue
                self._srv_line(rt, "> " + c)
                if not self._srv_send(rt, c):
                    return {"success": False, "error": "Couldn't reach the server."}
            return {"success": True}
        except Exception:
            log_exception("player_action")
            return {"success": False, "error": "That action failed."}

    def _srv_content_dir(self, meta):
        if meta.get("software") == "fabric":
            return server_dir(meta["name"]) / "mods"
        if meta.get("software") == "paper":
            return server_dir(meta["name"]) / "plugins"
        return None

    def get_server_content(self, name):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            meta["name"] = name
            folder = self._srv_content_dir(meta)
            if folder is None:
                return {"success": True, "items": [], "kind": None, "software": "vanilla"}
            folder.mkdir(exist_ok=True)
            reader = read_mod_jar if meta["software"] == "fabric" else read_plugin_jar
            items = []
            for p in folder.iterdir():
                low = p.name.lower()
                if low.endswith(".jar.disabled"):
                    enabled, stem = False, p.name[:-13]
                elif low.endswith(".jar") and p.is_file():
                    enabled, stem = True, p.name[:-4]
                else:
                    continue
                info = reader(p)
                items.append({"file": p.name, "name": info["name"] or stem, "version": info["version"],
                              "description": info["description"], "authors": info["authors"], "enabled": enabled})
            items.sort(key=lambda i: i["name"].lower())
            return {"success": True, "items": items, "software": meta["software"],
                    "kind": "mods" if meta["software"] == "fabric" else "plugins"}
        except Exception:
            log_exception("get_server_content")
            return {"success": False, "error": "Couldn't read the mods folder."}

    def _srv_content_file(self, name, filename):
        meta = read_server_meta(name)
        if not meta:
            raise RuntimeError("Server not found.")
        meta["name"] = name
        folder = self._srv_content_dir(meta)
        path = folder / Path(filename).name
        if not path.exists() or not is_within(folder, path):
            raise RuntimeError("File not found.")
        return path

    def toggle_server_content(self, name, filename, enabled):
        try:
            if self._srv_status(name) not in ("offline",):
                return {"success": False, "error": "Stop the server before enabling or disabling mods."}
            p = self._srv_content_file(name, filename)
            if enabled and p.name.lower().endswith(".jar.disabled"):
                p.rename(p.with_name(p.name[:-9]))
            elif not enabled and p.name.lower().endswith(".jar"):
                p.rename(p.with_name(p.name + ".disabled"))
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Couldn't change that file."}

    def delete_server_content(self, name, filename):
        try:
            if self._srv_status(name) not in ("offline",):
                return {"success": False, "error": "Stop the server before removing mods."}
            self._srv_content_file(name, filename).unlink()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Couldn't delete that file."}

    def search_server_mods(self, name, query="", offset=0):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            sw = meta["software"]
            if sw == "vanilla":
                return {"success": True, "hits": [], "total": 0, "unsupported": True}
            facets = [["project_type:mod"], [f"versions:{meta['version']}"]]
            if sw == "fabric":
                facets += [["categories:fabric"], ["server_side:required", "server_side:optional"]]
            else:
                facets += [[f"categories:{c}" for c in PLUGIN_LOADERS]]
            params = {"query": query or "", "limit": "24", "offset": str(max(0, int(offset or 0))),
                      "facets": json.dumps(facets), "index": "relevance" if query else "downloads"}
            data = http_get_json(f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params))
            return {"success": True, "hits": data.get("hits", []), "total": data.get("total_hits", 0)}
        except Exception:
            log_exception("search_server_mods")
            return {"success": False, "error": "Failed to search Modrinth."}

    def _srv_find_version(self, project_id, loaders, mc):
        params = {"loaders": json.dumps(loaders), "game_versions": json.dumps([mc])}
        url = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version?" + urllib.parse.urlencode(params)
        return pick_best_version(http_get_json(url))

    def _srv_install_deps(self, version, dest, loaders, mc, visited, files, depth=0):
        files.append(self._download_version_file(version, dest))
        if depth >= 3:
            return
        for dep in version.get("dependencies", []):
            pid = dep.get("project_id")
            if dep.get("dependency_type") != "required" or not pid or pid in visited:
                continue
            visited.add(pid)
            try:
                dv = (http_get_json(f"{MODRINTH_API}/version/{urllib.parse.quote(dep['version_id'])}")
                      if dep.get("version_id") else self._srv_find_version(pid, loaders, mc))
                if dv:
                    self._srv_install_deps(dv, dest, loaders, mc, visited, files, depth + 1)
            except Exception:
                log_exception(f"server dependency {pid} failed")

    def install_server_mod(self, name, project_id):
        try:
            meta = read_server_meta(name)
            if not meta:
                return {"success": False, "error": "Server not found."}
            meta["name"] = name
            folder = self._srv_content_dir(meta)
            if folder is None:
                return {"success": False, "error": "Vanilla servers can't run mods. Make a Fabric or Paper server."}
            loaders = ["fabric"] if meta["software"] == "fabric" else PLUGIN_LOADERS
            version = self._srv_find_version(project_id, loaders, meta["version"])
            if not version:
                return {"success": False,
                        "error": f"No version of this supports {meta['software'].capitalize()} {meta['version']}."}
            folder.mkdir(exist_ok=True)
            files: list = []
            self._srv_install_deps(version, folder, loaders, meta["version"], {project_id}, files)
            return {"success": True, "files": files}
        except Exception as exc:
            log_exception("install_server_mod")
            return {"success": False, "error": f"Failed to install: {exc}"}

    def _fabric_has_iris(self, name):
        mods = instance_mods_dir(name)
        if not mods.is_dir():
            return False
        for p in mods.iterdir():
            low = p.name.lower()
            if not low.endswith(".jar"):
                continue
            if re.match(r"^iris[-_+ ]", low) and "flywheel" not in low:
                return True
            n = (read_mod_jar(p).get("name") or "").lower()
            if n in ("iris", "iris shaders"):
                return True
        return False

    def install_iris(self, instance_name):
        try:
            meta = read_instance_meta(instance_name)
            if not meta or meta.get("loader") != "fabric":
                return {"success": False, "error": "Iris needs a Fabric instance."}
            version = self._find_version("iris", "fabric", meta["version"])
            if not version:
                return {"success": False, "error": f"Iris doesn't support Minecraft {meta['version']} yet."}
            mods_dir = instance_mods_dir(instance_name)
            mods_dir.mkdir(parents=True, exist_ok=True)
            files: list = []
            self._install_mod_with_deps(version, mods_dir, "fabric", meta["version"], {"YL57xq9U"}, files)
            return {"success": True, "files": files}
        except Exception as exc:
            log_exception("install_iris")
            return {"success": False, "error": f"Failed to install Iris: {exc}"}

    def _install_shader(self, project_id, instance_name, meta):
        mc = meta["version"]
        note = None
        base = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version"
        versions = http_get_json(base + "?" + urllib.parse.urlencode({"game_versions": json.dumps([mc])}))
        if not versions:
            versions = http_get_json(base)
            note = f"No exact match for {mc}, installed the newest version instead."
        iris_first = [v for v in versions if "iris" in (v.get("loaders") or [])] or versions
        version = pick_best_version(iris_first)
        if not version:
            raise RuntimeError("That shader pack has no downloadable version.")
        dest = instance_game_dir(instance_name) / "shaderpacks"
        dest.mkdir(parents=True, exist_ok=True)
        return {"success": True, "files": [self._download_version_file(version, dest)], "note": note}


class API(ServerMixin):
    def __init__(self):
        self._window = None
        self._processes: dict = {}
        self._sessions: dict = {}
        self._stopping: set = set()
        self._installing: set = set()
        self._addon_busy = False
        self._importing: set = set()
        self._web_login = None
        self._proc_lock = threading.Lock()
        self._launcher_started = time.time()
        self._srv_init()

    def set_window(self, window):
        self._window = window

    def emit(self, name: str, data=None):
        if self._window is None:
            return
        try:
            payload = json.dumps(data if data is not None else {})
            name_json = json.dumps(name)
            code = f"window.__critEvent && window.__critEvent({name_json}, {payload});"
            self._window.evaluate_js(code)
        except Exception:
            log_exception(f"emit({name}) failed")

    def push_state(self):
        self.emit("stateChanged", self._state_dict())


    def _runtime_dict(self) -> dict:
        with self._proc_lock:
            running = list(self._processes.keys())
            sessions = dict(self._sessions)
            installing = list(self._installing)
        return {
            "running": running,
            "sessions": sessions,
            "installing": installing,
            "playtime_total": int(settings_store.read().get("playtime_seconds", 0)),
            "playtime_days": _playtime_days(),
        }

    def _state_dict(self) -> dict:
        settings = settings_store.read()
        accounts = accounts_store.read()
        active_account = find_account(accounts, settings.get("active_account"))
        if not active_account and accounts:
            active_account = accounts[0]

        instances = []
        for meta in list_instances():
            icon_key = meta.get("icon")
            custom_icon_path = custom_instance_icon_path(icon_key)
            if icon_key in INSTANCE_ICONS:
                instance_icon = "critclient" if icon_key == "oneclient" else icon_key
            elif custom_icon_path and custom_icon_path.is_file():
                instance_icon = icon_key
            else:
                instance_icon = None
            instances.append({
                "name": meta.get("name"),
                "version": meta.get("version"),
                "loader": meta.get("loader", "vanilla"),
                "loader_version": meta.get("loader_version"),
                "installed": is_installed(meta),
                "playtime": int(meta.get("playtime", 0)),
                "icon": instance_icon,
                "created": meta.get("created"),
                "critclient": bool(meta.get("critclient") or meta.get("oneclient")),
            })
        active_instance_name = settings.get("active_instance")
        active_instance = next(
            (i for i in instances if i["name"] == active_instance_name), None)
        if not active_instance and instances:
            active_instance = instances[0]

        java_path, java_line = detect_java_for_ui()
        runtime = self._runtime_dict()

        return {
            "version": APP_VERSION,
            "accounts": [public_account(a) for a in accounts],
            "active_account": public_account(active_account) if active_account else None,
            "instances": instances,
            "custom_icons": list_custom_instance_icons(),
            "active_instance": active_instance,
            "running": runtime["running"],
            "sessions": runtime["sessions"],
            "installing": runtime["installing"],
            "playtime_total": runtime["playtime_total"],
            "playtime_days": runtime["playtime_days"],
            "memory_total_mb": total_system_memory_mb(),
            "memory_ceiling_mb": memory_ceiling_mb(),
            "settings": {
                "memory_mb": int(settings.get("memory_mb", 2048)),
                "min_memory_mb": int(settings.get("min_memory_mb") or 0),
                "java_path": settings.get("java_path"),
                "jvm_args": settings.get("jvm_args") if isinstance(settings.get("jvm_args"), str) else "",
                "game_width": int(settings.get("game_width") or 0),
                "game_height": int(settings.get("game_height") or 0),
                "fullscreen": bool(settings.get("fullscreen", False)),
                "custom_accent": _clean_hex(settings.get("custom_accent")),
                "recent_accents": [_clean_hex(x) for x in (settings.get("recent_accents") or []) if _clean_hex(x, None)][:10],
                "reduce_motion": bool(settings.get("reduce_motion", False)),
                "bg_pattern": bool(settings.get("bg_pattern", True)),
                "sidebar_collapsed": bool(settings.get("sidebar_collapsed", False)),
                "theme": settings.get("theme", "green") if settings.get("theme") in THEMES else "green",
                "ui_style": settings.get("ui_style", "default") if settings.get("ui_style") in UI_STYLES else "default",
                "addon_view": settings.get("addon_view") if settings.get("addon_view") in ADDON_VIEWS else "browse",
                "addon_instance": settings.get("addon_instance") if isinstance(settings.get("addon_instance"), str) else None,
                "color_mode": settings.get("color_mode", "dark") if settings.get("color_mode") in COLOR_MODES else "dark",
                "catalog_source": settings.get("catalog_source", "labymod") if settings.get("catalog_source") in CATALOG_SOURCES else "labymod",
                "addon_source": settings.get("addon_source") if settings.get("addon_source") in ADDON_SOURCES else "modrinth",
                "agreement_accepted": bool(settings.get("agreement_accepted", False)),
                "agreement_version": settings.get("agreement_version", 0)
                if isinstance(settings.get("agreement_version", 0), int) else 0,
                "server_beta_seen": bool(settings.get("server_beta_seen", False)),
                "local_skin_warning_seen": [x for x in (settings.get("local_skin_warning_seen") or [])
                                            if isinstance(x, str)],
            },
            "java_ok": java_path is not None,
            "java_version": java_line,
        }

    def get_state(self):
        try:
            return {"success": True, "state": self._state_dict()}
        except Exception as exc:
            log_exception("get_state")
            return {"success": False, "error": str(exc)}

    def get_runtime(self):
        try:
            data = self._runtime_dict()
            data["success"] = True
            return data
        except Exception as exc:
            log_exception("get_runtime")
            return {"success": False, "error": str(exc)}


    def get_versions(self):
        try:
            manifest = meta_cache.get_manifest()
            releases = [v for v in manifest["versions"] if v["type"] == "release"]
            return {"success": True, "versions": releases,
                    "latest_release": manifest.get("latest", {}).get("release")}
        except Exception:
            log_exception("get_versions")
            return {"success": False, "error": "Failed to load versions"}

    def get_fabric_loaders(self, minecraft_version=None):
        try:
            loaders = meta_cache.get_fabric_loaders(minecraft_version)
            return {"success": True, "loaders": loaders}
        except Exception as exc:
            log_exception("get_fabric_loaders")
            return {"success": False, "error": str(exc) or "Fabric loader could not be retrieved."}

    def get_fabric_game_versions(self):
        try:
            return {"success": True, "versions": meta_cache.get_fabric_game_versions()}
        except Exception as exc:
            log_exception("get_fabric_game_versions")
            return {"success": False, "error": str(exc) or "Fabric versions could not be retrieved."}

    def get_forge_versions(self, minecraft_version):
        try:
            versions = meta_cache.get_forge_versions(minecraft_version)
            return {"success": True, "versions": versions}
        except Exception as exc:
            log_exception("get_forge_versions")
            return {"success": False, "error": str(exc) or "Forge versions could not be retrieved."}

    def get_neoforge_versions(self, minecraft_version):
        try:
            versions = meta_cache.get_neoforge_versions(minecraft_version)
            return {"success": True, "versions": versions}
        except Exception as exc:
            log_exception("get_neoforge_versions")
            return {"success": False, "error": str(exc) or "NeoForge versions could not be retrieved."}

    def get_neoforge_mc_versions(self):
        try:
            return {"success": True, "versions": meta_cache.get_neoforge_mc_versions()}
        except Exception as exc:
            log_exception("get_neoforge_mc_versions")
            return {"success": False, "error": str(exc) or "NeoForge versions could not be retrieved."}

    def get_forge_mc_versions(self):
        try:
            return {"success": True, "versions": meta_cache.get_forge_mc_versions()}
        except Exception as exc:
            log_exception("get_forge_mc_versions")
            return {"success": False, "error": str(exc) or "Forge versions could not be retrieved."}


    def add_account(self, username):
        try:
            username = (username or "").strip()
            if not username or len(username) > 16 or not re.match(r"^[A-Za-z0-9_]+$", username):
                return {"success": False, "error": "Enter a valid username (letters, numbers, underscore)."}
            accounts = accounts_store.read()
            if any((a.get("name") or "").lower() == username.lower() for a in accounts):
                return {"success": False, "error": "An account with that name already exists."}
            account = {"name": username, "uuid": offline_uuid(username), "type": "offline"}
            accounts.append(account)
            accounts_store.write(accounts)

            def _set_active(s):
                if not s.get("active_account"):
                    s["active_account"] = account["uuid"]
            settings_store.update(_set_active)

            self.push_state()
            return {"success": True, "account": public_account(account)}
        except Exception as exc:
            log_exception("add_account")
            return {"success": False, "error": f"Failed to add account: {exc}"}

    def remove_account(self, key):
        try:
            accounts = accounts_store.read()
            target = find_account(accounts, key)
            if not target:
                return {"success": False, "error": "Account not found."}
            accounts = [a for a in accounts if a is not target and a.get("uuid") != target.get("uuid")]
            accounts_store.write(accounts)

            def _fix_active(s):
                cur = find_account(accounts_store.read(), s.get("active_account"))
                if not cur:
                    s["active_account"] = accounts[0]["uuid"] if accounts else None
            settings_store.update(_fix_active)

            self.push_state()
            return {"success": True}
        except Exception as exc:
            log_exception("remove_account")
            return {"success": False, "error": f"Failed to remove account: {exc}"}

    def switch_account(self, key):
        try:
            target = find_account(accounts_store.read(), key)
            if not target:
                return {"success": False, "error": "Account not found."}
            settings_store.update(lambda s: s.__setitem__("active_account", target["uuid"]))
            self.push_state()
            return {"success": True}
        except Exception:
            log_exception("switch_account")
            return {"success": False, "error": "Failed to switch account."}


    def start_minecraft_web_login(self):
        try:
            import webview
        except ImportError:
            return {"success": False, "error": "pywebview isn't available."}
        try:
            with self._proc_lock:
                old = self._web_login
                self._web_login = None
            if old:
                old["cancel"] = True
                try:
                    old["window"].destroy()
                except Exception:
                    pass

            state = {"cancel": False, "closed": False}
            win = webview.create_window(
                "Sign in with Microsoft", ms_authorize_url(), width=520, height=720,
                min_size=(420, 560), resizable=True, background_color="#0b0c0e")
            state["window"] = win
            try:
                win.events.closed += lambda *a: state.__setitem__("closed", True)
            except Exception:
                pass
            with self._proc_lock:
                self._web_login = state
            threading.Thread(target=self._web_login_poll, args=(state,), daemon=True).start()
            return {"success": True}
        except Exception as exc:
            log_exception("start_minecraft_web_login")
            return {"success": False, "error": f"Couldn't open the sign-in window: {exc}"}

    def cancel_minecraft_web_login(self):
        with self._proc_lock:
            state = self._web_login
            self._web_login = None
        if state:
            state["cancel"] = True
            try:
                state["window"].destroy()
            except Exception:
                pass
        return {"success": True}

    def _web_login_poll(self, state):
        win = state["window"]
        try:
            deadline = time.time() + 900
            code = None
            while time.time() < deadline:
                time.sleep(0.4)
                if state["cancel"]:
                    return
                if state["closed"]:
                    self.emit("mcLoginError", {"message": "The sign-in window was closed before it finished."})
                    return
                try:
                    url = win.get_current_url() or ""
                except Exception:
                    continue
                if not url.startswith(MS_REDIRECT_URI):
                    continue
                query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                if query.get("code"):
                    code = query["code"][0]
                    break
                if query.get("error"):
                    desc = (query.get("error_description") or query["error"])[0]
                    raise AuthError(f"Microsoft sign-in failed: {desc}")
            if not code:
                raise AuthError("Sign-in took too long. Try again.")

            state["cancel"] = True
            try:
                win.destroy()
            except Exception:
                pass
            self.emit("mcLoginStatus", {"status": "Signing in to Xbox Live and Minecraft..."})
            tokens = ms_token_request({"grant_type": "authorization_code", "code": code})
            account = ms_sign_in(tokens)
            self.push_state()
            self.emit("mcLoginFinished", {"name": account["name"]})
        except AuthError as exc:
            self.emit("mcLoginError", {"message": str(exc)})
            try:
                win.destroy()
            except Exception:
                pass
        except Exception as exc:
            log_exception("_web_login_poll")
            self.emit("mcLoginError", {"message": f"Sign-in failed: {exc}"})
            try:
                win.destroy()
            except Exception:
                pass
        finally:
            with self._proc_lock:
                if self._web_login is state:
                    self._web_login = None

    def _skin_account(self):
        accounts = accounts_store.read()
        acc = find_account(accounts, settings_store.read().get("active_account")) or (accounts[0] if accounts else None)
        if not acc or acc.get("type") != "microsoft":
            raise AuthError("Skins and capes need a Microsoft account. Add one under Accounts.")
        return ensure_fresh_account(acc)

    def get_skin_profile(self):
        try:
            acc = self._skin_account()
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
            return {"success": True, "name": username, "variant": "classic", "skin": skin,
                    "cape_id": cape["id"] if cape else None,
                    "capes": [{"alias": cape["name"], "active": True}] if cape else []}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("local_skin_state")
            return {"success": False, "error": f"Couldn't read the local skin: {exc}"}

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

    def lookup_player_skin(self, name):
        try:
            name = (name or "").strip()
            if not re.match(r"^[A-Za-z0-9_]{1,16}$", name):
                return {"success": False, "error": "Enter a valid player name."}
            result = _fetch_named_skin(name)
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

    def catalog_all(self, force=False):
        try:
            def load():
                skins = _catalog_tree()
                if skins is None:
                    skins = _catalog_walk()
                skins.sort(key=lambda s: s["name"].lower())
                return skins
            return {"success": True, "skins": _catalog_cached("all", load, bool(force))}
        except Exception as exc:
            log_exception("catalog_all")
            return {"success": False, "error": _catalog_error(exc)}

    def catalog_laby(self, offset=0, order="most_used", force=False, size=10, search="", model="all"):
        try:
            offset = max(0, int(offset or 0))
            order = order if order in LABY_ORDERS else "most_used"
            size = min(LABY_PAGE_SIZE, max(1, int(size or 10)))
            search = str(search or "").strip()[:80]
            model = model if model in ("all", "slim", "classic") else "all"
            key = f"laby:{order}:{model}:{size}:{offset}:{search.casefold()}"
            skins, more = _catalog_cached(
                key, lambda: _laby_fetch_page(offset, size, order, search, bool(force), model), bool(force))
            return {"success": True, "skins": skins, "has_more": more, "next_offset": offset + size}
        except Exception as exc:
            log_exception("catalog_laby")
            return {"success": False, "error": _laby_error(exc)}

    def catalog_fetch_skin(self, url):
        try:
            if not _skin_url_ok(url):
                return {"success": False, "error": "That isn't a catalog skin."}
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read(200_001)
            if not data.startswith(PNG_SIG):
                return {"success": False, "error": "That isn't a PNG image."}
            uri = png_uri(data)
            decode_png_uri(uri)
            return {"success": True, "image": uri}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("catalog_fetch_skin")
            return {"success": False, "error": "Couldn't download that skin. Check your connection."}

    def delete_saved_skin(self, skin_id):
        try:
            skin_id = re.sub(r"[^A-Za-z0-9]", "", str(skin_id))
            (SKINS_DIR / f"{skin_id}.png").unlink(missing_ok=True)
            skins_index.update(lambda idx: idx.__setitem__(slice(None), [e for e in idx if e.get("id") != skin_id]))
            return {"success": True, "skins": list_saved_skins()}
        except Exception:
            log_exception("delete_saved_skin")
            return {"success": False, "error": "Couldn't delete the skin."}


    def get_instance_content(self, instance_name, kind="mods"):
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            if kind not in ("mods", "resourcepacks", "shaderpacks"):
                return {"success": False, "error": "Unknown content type."}
            if kind == "mods" and meta.get("loader", "vanilla") == "vanilla":
                return {"success": True, "items": [], "loader": "vanilla"}
            return {"success": True, "items": list_instance_content(instance_name, kind),
                    "loader": meta.get("loader", "vanilla")}
        except Exception:
            log_exception("get_instance_content")
            return {"success": False, "error": "Couldn't read the folder."}

    def delete_instance_content(self, instance_name, kind, filename):
        try:
            if kind not in ("mods", "resourcepacks", "shaderpacks"):
                return {"success": False, "error": "Unknown content type."}
            if not read_instance_meta(instance_name):
                return {"success": False, "error": "Instance not found."}
            with self._proc_lock:
                running = instance_name in self._processes
            if running:
                return {"success": False, "error": "Close the game first - its files are in use."}
            folder = _content_folder(instance_name, kind)
            name = Path(str(filename or "")).name
            path = folder / name
            if not name or not (path.exists() or path.is_symlink()) or not is_within(folder, path) or path == folder:
                return {"success": False, "error": "That file no longer exists."}
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            return {"success": True}
        except PermissionError:
            return {"success": False, "error": "Couldn't delete it - another program is using the file."}
        except Exception:
            log_exception("delete_instance_content")
            return {"success": False, "error": "Couldn't delete that file."}

    def get_content_icon(self, instance_name, kind, filename):
        try:
            return {"success": True, "icon": read_content_icon(instance_name, kind, filename)}
        except Exception:
            return {"success": True, "icon": None}


    def _make_instance(self, name, version, loader, loader_version):
        instance_dir(name).mkdir(parents=True, exist_ok=True)
        meta = {
            "name": name,
            "version": version,
            "loader": loader,
            "loader_version": loader_version if loader in MOD_LOADERS else None,
            "installed": False,
            "schema": INSTALL_SCHEMA,
            "main_class": None,
            "asset_index": None,
            "arguments": None,
            "minecraft_arguments": None,
            "classpath": [],
            "playtime": 0,
            "icon": None,
            "created": time.time(),
        }
        write_instance_meta(name, meta)
        ensure_game_layout(name, loader)
        return meta

    def _critclient_versions(self) -> dict:
        data = http_get_json(CRIT_CLIENT_VERSIONS_URL, timeout=15, retries=1)
        out = {}
        if not isinstance(data, dict):
            return out
        for loader in ("fabric", "forge"):
            entries = data.get(loader)
            if not isinstance(entries, dict):
                continue
            clean = {}
            for mc, jar in entries.items():
                if (isinstance(mc, str) and isinstance(jar, str) and jar.lower().endswith(".jar")
                        and Path(jar).name == jar and "/" not in jar and "\\" not in jar):
                    clean[mc] = jar
            if clean:
                out[loader] = clean
        return out

    def get_critclient_info(self):
        try:
            versions = self._critclient_versions()
            if not versions:
                return {"success": False, "error": "Crit Client has no versions available right now."}
            return {"success": True, "versions": versions}
        except Exception:
            log_exception("get_critclient_info")
            return {"success": False, "error": "Couldn't reach the Crit Client server to check supported versions."}

    def get_horizon_mc_versions(self):
        """The Minecraft versions a new Horizon instance can use (a build supports exactly one)."""
        try:
            manifest = meta_cache.get_manifest()
            supported = [v["id"] for v in manifest.get("versions", [])
                         if v.get("type") == "release" and v.get("id") == HORIZON_MC_VERSION]
            return {"success": True, "versions": supported, "minecraft": HORIZON_MC_VERSION}
        except Exception:
            log_exception("get_horizon_mc_versions")
            return {"success": True, "versions": [HORIZON_MC_VERSION],
                    "minecraft": HORIZON_MC_VERSION}

    def create_instance(self, version, loader, loader_version, instance_name, critclient=False):
        try:
            name = sanitize_instance_name(instance_name)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        if instance_dir(name).exists():
            return {"success": False, "error": "Instance already exists."}
        if not version:
            return {"success": False, "error": "Select a Minecraft version."}
        if loader in MOD_LOADERS and not loader_version:
            return {"success": False, "error": f"Select a {LOADER_NICE[loader]} version."}

        if loader == "horizon":
            
            
            if critclient:
                return {"success": False, "error": "Crit Client needs Fabric or Forge."}
            if version != HORIZON_MC_VERSION:
                return {"success": False,
                        "error": f"Horizon only supports Minecraft {HORIZON_MC_VERSION}."}
            try:
                self._make_instance(name, HORIZON_MC_VERSION, "horizon", None)
            except Exception as exc:
                log_exception("create_instance (horizon)")
                return {"success": False, "error": f"Failed to create instance: {exc}"}
            with self._proc_lock:
                self._installing.add(name)
            threading.Thread(target=self._horizon_worker, args=(name,), daemon=True).start()
            self.push_state()
            return {"success": True}

        jar_name = None
        if critclient:
            if loader not in ("fabric", "forge"):
                return {"success": False, "error": "Crit Client needs Fabric or Forge."}
            try:
                jar_name = (self._critclient_versions().get(loader) or {}).get(version)
            except Exception:
                log_exception("critclient lookup")
                return {"success": False, "error": "Couldn't reach the Crit Client server."}
            if not jar_name:
                return {"success": False,
                        "error": f"Crit Client isn't available for {loader.capitalize()} {version}."}

        try:
            self._make_instance(name, version, loader, loader_version)
            if jar_name:
                update_instance_meta(name, lambda m: m.__setitem__("critclient", True))
                with self._proc_lock:
                    self._installing.add(name)
                threading.Thread(target=self._critclient_worker, args=(name, version, loader, jar_name),
                                 daemon=True).start()
            self.push_state()
            return {"success": True}
        except Exception as exc:
            log_exception("create_instance")
            return {"success": False, "error": f"Failed to create instance: {exc}"}


    def scan_import_sources(self):
        try:
            return {"success": True, "sources": scan_all_launchers()}
        except Exception:
            log_exception("scan_import_sources")
            return {"success": False, "error": "Couldn't scan for other launchers."}

    def pick_import_folder(self):
        try:
            import webview
            if self._window is None:
                return {"success": False, "error": "The window isn't ready yet."}
            dialog_type = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
            result = self._window.create_file_dialog(dialog_type)
            if not result:
                return {"success": True, "path": None}
            path = Path(result[0] if isinstance(result, (list, tuple)) else result)
            return {"success": True, "path": str(path), "guess": _guess_launcher_for_dir(path)}
        except Exception:
            log_exception("pick_import_folder")
            return {"success": False, "error": "Couldn't open the folder picker."}

    def scan_custom_import_dir(self, launcher, path):
        try:
            root = Path(path)
            if not root.is_dir():
                return {"success": False, "error": "That folder doesn't exist."}
            if launcher not in _LAUNCHER_SCANNERS:
                return {"success": False, "error": "Pick which launcher this folder belongs to."}
            src = scan_custom_launcher_dir(launcher, root)
            if not src["instances"]:
                return {"success": False,
                        "error": f"No {LAUNCHER_LABELS[launcher]} instances were found in that folder. "
                                 "Make sure it's the launcher's root folder."}
            return {"success": True, "source": src}
        except Exception:
            log_exception("scan_custom_import_dir")
            return {"success": False, "error": "Couldn't scan that folder."}

    def get_import_targets(self):
        try:
            return {"success": True, "instances": [
                {"name": i["name"], "version": i["version"], "loader": i.get("loader", "vanilla"),
                 "loader_version": i.get("loader_version")} for i in list_instances()]}
        except Exception:
            log_exception("get_import_targets")
            return {"success": False, "error": "Couldn't list instances."}

    def import_launcher_instance(self, launcher, source_id, dest_name, existing, copy_options, copy_mods, copy_resourcepacks, copy_saves=False, copy_config=False,
                                copy_shaderpacks=False, copy_schematics=False, copy_screenshots=False,
                                custom_root=None):
        try:
            inst, root = _find_scanned_instance(launcher, source_id, custom_root)
            if not inst:
                return {"success": False, "error": "That instance couldn't be found anymore. Try rescanning."}
            if existing:
                if not read_instance_meta(existing):
                    return {"success": False, "error": "Destination instance not found."}
                dest_name = existing
            else:
                try:
                    dest_name = sanitize_instance_name(dest_name or inst["name"])
                except ValueError as exc:
                    return {"success": False, "error": str(exc)}
                if instance_dir(dest_name).exists():
                    return {"success": False, "error": "An instance with that name already exists."}
                if not inst.get("version"):
                    return {"success": False, "error": "Couldn't work out which Minecraft version this uses."}
        except Exception:
            log_exception("import_launcher_instance (validate)")
            return {"success": False, "error": "Import failed."}

        with self._proc_lock:
            if dest_name in self._importing:
                return {"success": False, "error": "Already importing into that instance."}
            self._importing.add(dest_name)
        threading.Thread(
            target=self._import_worker,
            args=(launcher, inst, root, dest_name, bool(existing),
                  bool(copy_options), bool(copy_mods), bool(copy_resourcepacks), bool(copy_saves), bool(copy_config),
                  bool(copy_shaderpacks), bool(copy_schematics), bool(copy_screenshots)),
            daemon=True,
        ).start()
        return {"success": True, "instance": dest_name}

    def _import_worker(self, launcher, inst, root, dest_name, into_existing,
                       copy_options, copy_mods, copy_resourcepacks, copy_saves=False, copy_config=False,
                       copy_shaderpacks=False, copy_schematics=False, copy_screenshots=False):
        try:
            self.emit("importStarted", {"instance": dest_name})
            supported = inst["loader"] == "vanilla" or inst["loader"] in MOD_LOADERS
            dest_loader = inst["loader"] if supported else "vanilla"
            if not into_existing:
                self._make_instance(dest_name, inst["version"], dest_loader,
                                    inst.get("loader_version") if supported else None)

            options_path, mods_dir, packs_dir, saves_dir, config_dir, shaders_dir, schem_dir, shots_dir = _source_paths(launcher, inst["source_id"], root)
            game_dir = instance_game_dir(dest_name)
            ensure_game_layout(dest_name, dest_loader)

            copied = {"options": False, "servers": False, "hotbar": False,
                      "mods": 0, "resourcepacks": 0, "saves": 0, "config": 0,
                      "shaderpacks": 0, "schematics": 0, "screenshots": 0}
            if copy_options and options_path:
                if options_path.is_file():
                    try:
                        shutil.copy2(options_path, game_dir / "options.txt")
                        copied["options"] = True
                    except OSError:
                        log_exception("copying options.txt")
                for fname, key in (("servers.dat", "servers"), ("hotbar.nbt", "hotbar")):
                    src_file = options_path.parent / fname
                    if src_file.is_file():
                        try:
                            shutil.copy2(src_file, game_dir / fname)
                            copied[key] = True
                        except OSError:
                            log_exception(f"copying {fname}")
            if copy_mods and inst["loader"] in MOD_LOADERS and mods_dir:
                copied["mods"] = _copy_tree_contents(mods_dir, instance_mods_dir(dest_name))
            if copy_resourcepacks and packs_dir:
                copied["resourcepacks"] = _copy_tree_contents(packs_dir, game_dir / "resourcepacks")
            if copy_saves and saves_dir:
                copied["saves"] = _copy_tree_contents(saves_dir, game_dir / "saves")
            if copy_config and inst["loader"] in MOD_LOADERS and config_dir:
                copied["config"] = _copy_tree_contents(config_dir, instance_config_dir(dest_name))
            if copy_shaderpacks and shaders_dir:
                copied["shaderpacks"] = _copy_tree_contents(shaders_dir, game_dir / "shaderpacks")
            if copy_schematics and schem_dir:
                copied["schematics"] = _copy_tree_contents(schem_dir, game_dir / "schematics")
            if copy_screenshots and shots_dir:
                copied["screenshots"] = _copy_tree_contents(shots_dir, game_dir / "screenshots")

            self.push_state()
            self.emit("importFinished", {"instance": dest_name, "copied": copied})
        except Exception as exc:
            log_exception("_import_worker")
            self.emit("importError", {"instance": dest_name, "message": str(exc) or "Import failed."})
        finally:
            with self._proc_lock:
                self._importing.discard(dest_name)

    def delete_instance(self, instance_name):
        try:
            with self._proc_lock:
                if instance_name in self._processes:
                    return {"success": False, "error": "Stop the instance before deleting it."}
                if instance_name in self._installing:
                    return {"success": False, "error": "Instance is currently installing."}
            target = instance_dir(instance_name)
            if not target.exists():
                return {"success": False, "error": "Instance not found."}
            shutil.rmtree(target, ignore_errors=True)

            def _clear_active(s):
                if s.get("active_instance") == instance_name:
                    s["active_instance"] = None
            settings_store.update(_clear_active)

            self.push_state()
            return {"success": True}
        except Exception:
            log_exception("delete_instance")
            return {"success": False, "error": "Failed to delete instance."}

    def set_active_instance(self, instance_name):
        try:
            if not read_instance_meta(instance_name):
                return {"success": False, "error": "Instance not found."}
            settings_store.update(lambda s: s.__setitem__("active_instance", instance_name))
            self.push_state()
            return {"success": True}
        except Exception:
            log_exception("set_active_instance")
            return {"success": False, "error": "Failed to set active instance."}

    def set_instance_icon(self, instance_name, icon_key):
        try:
            if not read_instance_meta(instance_name):
                return {"success": False, "error": "Instance not found."}
            custom_icon = custom_instance_icon_path(icon_key)
            if icon_key not in INSTANCE_ICONS and not (custom_icon and custom_icon.is_file()):
                return {"success": False, "error": "Choose a valid icon."}
            update_instance_meta(instance_name, lambda m: m.__setitem__("icon", icon_key))
            self.push_state()
            return {"success": True}
        except Exception:
            log_exception("set_instance_icon")
            return {"success": False, "error": "Couldn't change the icon."}

    def add_custom_instance_icon(self, image_data, file_name):
        try:
            prefix = "data:image/png;base64,"
            if not isinstance(image_data, str) or not image_data.startswith(prefix):
                return {"success": False, "error": "The icon image must be a PNG."}
            encoded = image_data[len(prefix):]
            if len(encoded) > 28 * 1024 * 1024:
                return {"success": False, "error": "That image is too large (max 20 MB)."}
            try:
                image = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error):
                return {"success": False, "error": "That image couldn't be read."}
            if len(image) > 20 * 1024 * 1024 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                return {"success": False, "error": "That image isn't a valid PNG."}
            base_name = str(file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
            base_name = base_name.rsplit(".", 1)[0] if "." in base_name else base_name
            base_name = sanitize_custom_icon_name(base_name)
            used_names = {
                path.stem if path.suffix.lower() == ".png" else path.name
                for path in INSTANCE_ICONS_DIR.iterdir() if path.is_file()
            }
            used_names = {name.casefold() for name in used_names}
            icon_name = base_name
            suffix = 2
            while icon_name.casefold() in used_names:
                suffix_text = f" ({suffix})"
                icon_name = f"{base_name[:100 - len(suffix_text)].rstrip(' .')}{suffix_text}"
                suffix += 1
            icon_path = INSTANCE_ICONS_DIR / icon_name
            temp_path = INSTANCE_ICONS_DIR / f".{uuid.uuid4().hex}.tmp"
            try:
                temp_path.write_bytes(image)
                os.replace(temp_path, icon_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
            self.push_state()
            return {"success": True, "key": f"custom:{icon_name}", "name": icon_name}
        except Exception:
            log_exception("add_custom_instance_icon")
            return {"success": False, "error": "Couldn't save the custom icon."}

    def delete_custom_instance_icon(self, icon_key):
        try:
            icon_path = custom_instance_icon_path(icon_key)
            if not icon_path or not icon_path.is_file():
                return {"success": False, "error": "Custom icon not found."}
            for meta in list_instances():
                if meta.get("icon") == icon_key:
                    if not update_instance_meta(meta["name"], lambda m: m.__setitem__("icon", None)):
                        return {"success": False, "error": "Couldn't clear an instance using this icon."}
            icon_path.unlink()
            self.push_state()
            return {"success": True}
        except Exception:
            log_exception("delete_custom_instance_icon")
            return {"success": False, "error": "Couldn't delete the custom icon."}

    def rename_instance(self, old_name, new_name):
        try:
            old_meta = read_instance_meta(old_name)
            if not old_meta:
                return {"success": False, "error": "Instance not found."}
            try:
                new_name = sanitize_instance_name(new_name)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            if new_name == old_name:
                return {"success": True}
            if instance_dir(new_name).exists():
                return {"success": False, "error": "An instance with that name already exists."}
            with self._proc_lock:
                if old_name in self._processes or old_name in self._installing:
                    return {"success": False, "error": "Stop or wait for the instance first."}
            shutil.move(str(instance_dir(old_name)), str(instance_dir(new_name)))
            old_meta["name"] = new_name
            write_instance_meta(new_name, old_meta)

            def _fix(s):
                if s.get("active_instance") == old_name:
                    s["active_instance"] = new_name
            settings_store.update(_fix)

            self.push_state()
            return {"success": True}
        except Exception:
            log_exception("rename_instance")
            return {"success": False, "error": "Couldn't rename the instance."}

    def get_disk_usage(self, kind):
        try:
            root = INSTANCES_DIR if kind == "instances" else SERVERS_DIR
            out = {}
            if root.is_dir():
                for d in root.iterdir():
                    if d.is_dir():
                        out[d.name] = _dir_size(d)
            return {"success": True, "sizes": out}
        except Exception:
            log_exception("get_disk_usage")
            return {"success": False, "sizes": {}}

    def get_instance_screenshots(self, instance_name):
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            folder = instance_game_dir(instance_name) / "screenshots"
            if not folder.is_dir():
                return {"success": True, "screenshots": []}
            shots = []
            for p in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    shots.append({"file": p.name, "taken": p.stat().st_mtime})
            return {"success": True, "screenshots": shots}
        except Exception:
            log_exception("get_instance_screenshots")
            return {"success": False, "error": "Couldn't read the screenshots folder."}

    def get_screenshot_image(self, instance_name, filename):
        try:
            folder = instance_game_dir(instance_name) / "screenshots"
            path = folder / Path(filename).name
            if not path.is_file() or not is_within(folder, path):
                return {"success": False, "error": "Screenshot not found."}
            if path.stat().st_size > 40_000_000:
                return {"success": False, "error": "That screenshot is too large to preview."}
            ext = path.suffix.lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return {"success": True, "image": f"data:{mime};base64,{data}"}
        except Exception:
            log_exception("get_screenshot_image")
            return {"success": False, "error": "Couldn't read that screenshot."}

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

    def get_changelog(self, force=False):
        try:
            now = time.time()
            if not force and _changelog_mem["value"] and now - _changelog_mem["at"] < 600:
                return {"success": True, **_changelog_mem["value"]}
            rel = http_get_json(f"https://api.github.com/repos/{CHANGELOG_REPO}/releases/latest",
                                timeout=15, retries=1)
            asset = next((a for a in rel.get("assets", [])
                          if (a.get("name") or "").lower() == "changelog.txt"), None)
            text = None
            if asset and asset.get("browser_download_url"):
                try:
                    text = http_get_text(asset["browser_download_url"]).strip()
                except Exception:
                    log_exception("changelog.txt download")
            if not text:
                text = (rel.get("body") or "").strip()
            if not text:
                text = "No changelog was published with this release."
            assets = rel.get("assets", [])
            setup = next((a for a in assets if (a.get("name") or "").lower() == "setup.exe"), None) or next(
                (a for a in assets if (a.get("name") or "").lower().endswith(".exe")
                 and "setup" in (a.get("name") or "").lower()), None)
            value = {"tag": rel.get("tag_name"), "name": rel.get("name"),
                     "date": rel.get("published_at"), "text": text[:20000],
                     "setup_url": setup.get("browser_download_url") if setup else None,
                     "setup_size": int(setup.get("size") or 0) if setup else 0,
                     "assets": assets, "release_tag": rel.get("tag_name")}
            _changelog_mem["at"], _changelog_mem["value"] = now, value
            try:
                CHANGELOG_CACHE.write_text(json.dumps(value), encoding="utf-8")
            except OSError:
                pass
            return {"success": True, **value}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"success": False, "error": "No releases have been published yet."}
            log_exception("get_changelog")
        except Exception:
            log_exception("get_changelog")
        try:
            cached = json.loads(CHANGELOG_CACHE.read_text(encoding="utf-8"))
            return {"success": True, "cached": True, **cached}
        except Exception:
            return {"success": False, "error": "Couldn't load the changelog. Check your connection."}

    def check_for_updates(self, force=False):
        try:
            res = self.get_changelog(bool(force))
            if not res.get("success"):
                return {"success": False, "error": res.get("error") or "Couldn't check for updates."}
            tag = res.get("tag") or ""
            muted = settings_store.read().get("update_muted_at") == APP_VERSION
            return {"success": True, "current": APP_VERSION, "latest": tag,
                    "available": _version_tuple(tag) > _version_tuple(APP_VERSION),
                    "installer": bool(res.get("setup_url")), "muted": muted}
        except Exception:
            log_exception("check_for_updates")
            return {"success": False, "error": "Couldn't check for updates."}

    def mute_updates(self):
        try:
            settings_store.update(lambda st: st.__setitem__("update_muted_at", APP_VERSION))
            return {"success": True}
        except Exception:
            log_exception("mute_updates")
            return {"success": False, "error": "Couldn't save that."}

    def install_update(self):
        if IS_LINUX:
            try:
                import webbrowser
                webbrowser.open(f"https://github.com/{CHANGELOG_REPO}/releases/latest")
            except Exception:
                log_exception("open releases page")
            return {"success": False,
                    "error": "Opened the releases page in your browser. Download the new AppImage there."}
        if os.name != "nt":
            return {"success": False, "error": "Automatic updating is only available on Windows."}
        threading.Thread(target=self._update_worker, daemon=True).start()
        return {"success": True}

    def _update_worker(self):
        try:
            res = self.get_changelog(True)
            url = res.get("setup_url") if res.get("success") else None
            if not url:
                self.emit("updateError", {"message": "The latest release has no setup.exe to install."})
                return
            size = int(res.get("setup_size") or 0)
            dest = DATA_DIR / "update" / "setup.exe"

            # A hijacked release is only dangerous because we run it, so the
            # signature has to be checked before anything reaches startfile.
            expected = None
            try:
                expected = update_signed_sha256(res, Path(url).name)
            except RuntimeError as exc:
                if UPDATE_TRUSTED_KEYS:
                    self.emit("updateError", {"message": str(exc)})
                    return
                logging.warning("Update signature not checked: %s", exc)
            done = [0]

            def progress(n):
                done[0] += n
                if size:
                    self.emit("updateProgress", {"percent": min(100, int(done[0] * 100 / size))})

            self.emit("updateProgress", {"percent": 0})
            http_download(url, dest, size or None, expected, progress_cb=progress)
            self.emit("updateInstalling", {})
            os.startfile(str(dest))
            time.sleep(1.5)
            if self._window is not None:
                self._window.destroy()
        except Exception:
            log_exception("update")
            self.emit("updateError", {"message": "Couldn't download the update. Check your connection and try again."})

    def quit_app(self):
        """Close Crit Launcher (used when the first-run agreement is declined)."""
        try:
            if self._window is not None:
                self._window.destroy()
            return {"success": True}
        except Exception:
            log_exception("quit_app")
            return {"success": False, "error": "Couldn't close the launcher."}

    def open_url(self, url):
        try:
            import webbrowser
            if not isinstance(url, str) or not url.startswith(("https://", "http://", "mailto:")):
                return {"success": False}
            webbrowser.open(url)
            return {"success": True}
        except Exception:
            log_exception("open_url")
            return {"success": False}

    def _create_linux_shortcut(self, instance_name, icon_data):
        """Write a freedesktop .desktop entry (app menu, plus ~/Desktop when it exists)."""
        if not isinstance(icon_data, str) or not icon_data.startswith("data:image/png;base64,"):
            return {"success": False, "error": "Couldn't prepare the instance icon for the shortcut."}
        raw_icon = base64.b64decode(icon_data.split(",", 1)[1], validate=True)
        if not raw_icon or len(raw_icon) > 12 * 1024 * 1024:
            return {"success": False, "error": "The instance icon is invalid or too large."}
        from io import BytesIO
        from PIL import Image
        icon_dir = DATA_DIR / "shortcut_icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_path = icon_dir / f"{instance_name}.png"
        with Image.open(BytesIO(raw_icon)) as image:
            image = image.convert("RGBA")
            if image.width < 1 or image.height < 1 or image.width > 4096 or image.height > 4096:
                return {"success": False, "error": "The instance icon has invalid dimensions."}
            image.save(icon_path, format="PNG")

        def quote(arg):
            # Exec= quoting from the Desktop Entry spec.
            arg = str(arg).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
            return '"' + arg.replace("%", "%%") + '"'

        appimage = os.environ.get("APPIMAGE")
        if appimage:
            command = [appimage]
        elif getattr(sys, "frozen", False):
            command = [str(Path(sys.executable).resolve())]
        else:
            command = [sys.executable, str(Path(__file__).resolve())]
        command += ["--launch-instance", instance_name]
        text_name = re.sub(r"[\r\n]", " ", instance_name)
        entry = (
            "[Desktop Entry]\nType=Application\nVersion=1.0\n"
            f"Name=Crit Launcher - {text_name}\n"
            f"Comment=Launch {text_name} in Crit Launcher\n"
            f"Exec={' '.join(quote(c) for c in command)}\n"
            f"Icon={icon_path}\nTerminal=false\nCategories=Game;\n"
        )
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", instance_name).strip("_") or "instance"
        digest = hashlib.sha1(instance_name.encode("utf-8")).hexdigest()[:6]
        file_name = f"crit-launcher-{safe}-{digest}.desktop"
        targets = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"]
        desktop = Path.home() / "Desktop"
        if desktop.is_dir():
            targets.append(desktop)
        first = None
        for folder in targets:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / file_name
            path.write_text(entry, encoding="utf-8")
            path.chmod(0o755)
            first = first or path
        return {"success": True, "path": str(first)}

    def create_desktop_shortcut(self, instance_name, icon_data=None):
        try:
            if IS_LINUX:
                instance_name = sanitize_instance_name(instance_name)
                if not read_instance_meta(instance_name):
                    return {"success": False, "error": "Instance not found."}
                return self._create_linux_shortcut(instance_name, icon_data)
            if os.name != "nt":
                return {"success": False, "error": "Desktop shortcuts are only supported on Windows."}
            instance_name = sanitize_instance_name(instance_name)
            if not read_instance_meta(instance_name):
                return {"success": False, "error": "Instance not found."}

            desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
            if not desktop.is_dir():
                desktop = Path.home() / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            shortcut_path = desktop / f"Crit Launcher - {instance_name}.lnk"
            icon_dir = DATA_DIR / "shortcut_icons"
            icon_dir.mkdir(parents=True, exist_ok=True)
            icon_path = icon_dir / f"{instance_name}.ico"
            if not isinstance(icon_data, str) or not icon_data.startswith("data:image/png;base64,"):
                return {"success": False, "error": "Couldn't prepare the instance icon for the shortcut."}
            raw_icon = base64.b64decode(icon_data.split(",", 1)[1], validate=True)
            if not raw_icon or len(raw_icon) > 12 * 1024 * 1024:
                return {"success": False, "error": "The instance icon is invalid or too large."}
            from io import BytesIO
            from PIL import Image
            with Image.open(BytesIO(raw_icon)) as image:
                image = image.convert("RGBA")
                if image.width < 1 or image.height < 1 or image.width > 4096 or image.height > 4096:
                    return {"success": False, "error": "The instance icon has invalid dimensions."}
                image.save(icon_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

            target = Path(sys.executable).resolve()
            if getattr(sys, "frozen", False):
                arguments = subprocess.list2cmdline(["--launch-instance", instance_name])
            else:
                arguments = subprocess.list2cmdline([
                    str(Path(__file__).resolve()), "--launch-instance", instance_name
                ])
            def ps_quote(value):
                return "'" + str(value).replace("'", "''") + "'"

            script = (
                "$wsh = New-Object -ComObject WScript.Shell; "
                f"$link = $wsh.CreateShortcut({ps_quote(shortcut_path)}); "
                f"$link.TargetPath = {ps_quote(target)}; "
                f"$link.Arguments = {ps_quote(arguments)}; "
                f"$link.WorkingDirectory = {ps_quote(APP_DIR)}; "
                f"$link.IconLocation = {ps_quote(str(icon_path) + ',0')}; "
                "$link.Save()"
            )
            encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
            return {"success": True, "path": str(shortcut_path)}
        except Exception as exc:
            log_exception("create_desktop_shortcut")
            return {"success": False, "error": f"Couldn't create a desktop shortcut: {exc}"}

    def open_instance_folder(self, instance_name):
        try:
            if not read_instance_meta(instance_name):
                return {"success": False, "error": "Instance not found."}
            meta = read_instance_meta(instance_name) or {}
            ensure_game_layout(instance_name, meta.get("loader", "vanilla"))
            open_in_file_manager(instance_game_dir(instance_name))
            return {"success": True}
        except Exception:
            log_exception("open_instance_folder")
            return {"success": False, "error": "Couldn't open the folder."}


    def launch(self, instance_name):
        try:
            with self._proc_lock:
                if instance_name in self._processes:
                    return {"success": False, "error": "Instance is already running."}
                if instance_name in self._installing:
                    return {"success": False, "error": "Instance is currently installing."}

            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            if meta.get("loader") == "horizon":
                
                saved = meta.get("horizon_resources")
                if meta.get("horizon_prepared") and saved and Path(saved).is_dir():
                    return self._do_horizon_launch(instance_name, meta)
                with self._proc_lock:
                    self._installing.add(instance_name)
                self.push_state()
                threading.Thread(target=self._horizon_worker, args=(instance_name, True),
                                 daemon=True).start()
                return {"success": True, "installing": True}

            accounts = accounts_store.read()
            settings = settings_store.read()
            active_account = find_account(accounts, settings.get("active_account"))
            if not active_account and accounts:
                active_account = accounts[0]
            if not active_account:
                return {"success": False, "error": "Please add an account first."}
            try:
                active_account = ensure_fresh_account(active_account)
            except AuthError as exc:
                return {"success": False, "error": str(exc)}

            needs_install = not is_installed(meta)
            if not needs_install:
                java_path = resolve_java(java_info_from_meta(meta), allow_download=False)
                if java_path:
                    return self._do_launch(instance_name, meta, active_account, java_path)

            with self._proc_lock:
                self._installing.add(instance_name)
            self.push_state()
            threading.Thread(
                target=self._launch_worker,
                args=(instance_name, meta, active_account, needs_install),
                daemon=True,
            ).start()
            return {"success": True, "installing": True}
        except Exception:
            log_exception("launch")
            return {"success": False, "error": "Failed to launch Minecraft."}

    def _horizon_worker(self, name, then_launch=False):
        """Installs Horizon, Java and Minecraft, then prepares the game's resources for one instance."""
        try:
            self.emit("installStarted", {"instance": name,
                                         "title": f"Setting up Horizon for {name}..."})
            tracker = ProgressTracker(self.emit)
            info = horizon_setup(name, tracker)
            update_instance_meta(name, lambda m: m.update(info))
            self.emit("installFinished", {"instance": name,
                                          "message": f"Horizon is ready in {name}"})
            self.push_state()
            if then_launch:
                meta = read_instance_meta(name) or {}
                result = self._do_horizon_launch(name, meta)
                if not result.get("success"):
                    self.emit("launchError", {"instance": name,
                                              "message": result.get("error", "Failed to launch.")})
        except Exception as exc:
            log_exception("horizon setup failed")
            self.emit("installError", {"message": f"Horizon setup failed: {exc}"})
        finally:
            with self._proc_lock:
                self._installing.discard(name)
            self.push_state()

    def _horizon_session(self, instance_name, account):
        data = {
            "name": account["name"],
            "uuid": account["uuid"].replace("-", ""),
            "access_token": account["mc_token"],
            "user_type": "msa",
        }
        if account.get("xuid"):
            data["xuid"] = str(account["xuid"])
        path = instance_dir(instance_name) / "horizon-session.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        if os.name == "posix":
            os.chmod(path, 0o600)
        return path

    def _do_horizon_launch(self, instance_name, meta):
        try:
            saved_dir = meta.get("horizon_dir")
            saved_resources = meta.get("horizon_resources")
            vdir = Path(saved_dir) if saved_dir else Path()
            resources = Path(saved_resources) if saved_resources else Path()
            client = vdir / horizon_exe("horizon-client")
            if not saved_dir or not saved_resources or not vdir.is_dir() \
                    or not client.is_file() or not resources.is_dir():
                return {"success": False,
                        "error": "Horizon isn't set up yet. Press Play to finish installing it."}

            ensure_game_layout(instance_name, "horizon")
            game_dir = instance_game_dir(instance_name)
            settings = settings_store.read()
            accounts = accounts_store.read()
            account = find_account(accounts, settings.get("active_account"))
            if not account and accounts:
                account = accounts[0]

            if account and account.get("type") == "microsoft":
                try:
                    account = ensure_fresh_account(account)
                except AuthError as exc:
                    return {"success": False, "error": str(exc)}

            cmd = [
                str(client), "--resources", str(resources),
                "--game-dir", str(game_dir), "--home", str(game_dir),
            ]
            is_microsoft_account = account and account.get("type") == "microsoft"
            if account and not is_microsoft_account:
                cmd += ["--name", str(account.get("name"))]
            env = dict(os.environ)
            env["HORIZON_HOME"] = str(game_dir)

            log_path = instance_dir(instance_name) / "launch_output.log"
            log_file = open(log_path, "wb")
            session_path = None
            try:
                if is_microsoft_account:
                    session_path = self._horizon_session(instance_name, account)
                    cmd += ["--session-file", str(session_path)]
                logging.info("Launching Horizon %s: %s", instance_name, " ".join(cmd)[:1000])
                proc = subprocess.Popen(
                    cmd, cwd=str(game_dir), stdin=subprocess.DEVNULL,
                    stdout=log_file, stderr=subprocess.STDOUT, env=env,
                    creationflags=CREATE_NO_WINDOW, shell=False,
                )
            except Exception:
                log_file.close()
                if session_path and session_path.is_file():
                    try:
                        session_path.unlink()
                    except OSError:
                        logging.exception("Couldn't remove Horizon session file after launch failure")
                raise

            started = time.time()
            with self._proc_lock:
                self._processes[instance_name] = proc
                self._sessions[instance_name] = started

            update_instance_meta(instance_name, lambda m: m.__setitem__("last_played", started))
            threading.Thread(
                target=self._watch_process,
                args=(instance_name, proc, log_file, log_path, started),
                daemon=True,
            ).start()
            self.push_state()
            return {"success": True}
        except Exception as exc:
            log_exception("_do_horizon_launch")
            return {"success": False, "error": f"Failed to start Horizon: {exc}"}

    def _launch_worker(self, instance_name, meta, account, needs_install):
        try:
            if needs_install:
                if not Installer(self, meta).run():
                    return
                meta = read_instance_meta(instance_name) or meta

            info = java_info_from_meta(meta)
            java_path = resolve_java(info, allow_download=False)
            if not java_path:
                self.emit("installStarted", {
                    "instance": instance_name,
                    "title": f"Downloading Java {info['majorVersion']}..."})
                tracker = ProgressTracker(self.emit)
                java_path = resolve_java(info, allow_download=True, tracker=tracker)
                self.emit("installFinished", {"instance": instance_name})

            result = self._do_launch(instance_name, meta, account, java_path)
            if not result.get("success"):
                self.emit("launchError", {"instance": instance_name,
                                          "message": result.get("error", "Failed to launch.")})
        except Exception as exc:
            log_exception("_launch_worker")
            self.emit("installError", {"message": str(exc) or "Failed to launch Minecraft."})
        finally:
            with self._proc_lock:
                self._installing.discard(instance_name)
            self.push_state()

    def _do_launch(self, instance_name, meta, account, java_path):
        try:
            if not java_path:
                major = java_info_from_meta(meta)["majorVersion"]
                return {"success": False, "error": f"Java {major} was not found."}

            settings = settings_store.read()
            memory_mb = clamp_memory_mb(settings.get("memory_mb", 2048))
            command = build_launch_command(meta, account, memory_mb, console_java(java_path), settings)
            game_dir = instance_game_dir(instance_name)
            ensure_game_layout(instance_name, meta.get("loader", "vanilla"))

            log_path = instance_dir(instance_name) / "launch_output.log"
            log_file = open(log_path, "wb")
            logging.info("Launching %s: %s", instance_name, " ".join(command)[:2500])

            try:
                proc = subprocess.Popen(
                    command,
                    cwd=str(game_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW,
                    shell=False,
                )
            except Exception:
                log_file.close()
                raise

            started = time.time()
            with self._proc_lock:
                self._processes[instance_name] = proc
                self._sessions[instance_name] = started

            update_instance_meta(instance_name, lambda m: m.__setitem__("last_played", started))
            threading.Thread(
                target=self._watch_process,
                args=(instance_name, proc, log_file, log_path, started),
                daemon=True,
            ).start()

            self.push_state()
            return {"success": True}
        except Exception as exc:
            log_exception("_do_launch")
            return {"success": False, "error": f"Failed to launch Minecraft: {exc}"}

    def _watch_process(self, instance_name, proc, log_file, log_path, started):
        code = proc.wait()
        try:
            log_file.close()
        except OSError:
            pass
        elapsed = int(time.time() - started)

        with self._proc_lock:
            self._processes.pop(instance_name, None)
            self._sessions.pop(instance_name, None)
            user_stopped = instance_name in self._stopping
            self._stopping.discard(instance_name)

        if elapsed >= 1:
            try:
                settings_store.update(lambda s: _bump_playtime(s, elapsed))
                update_instance_meta(
                    instance_name,
                    lambda m: m.__setitem__("playtime", int(m.get("playtime", 0)) + elapsed))
            except Exception:
                log_exception("playtime update")

        if code != 0 and not user_stopped:
            tail = _log_tail(log_path)
            logging.error("%s exited with code %s\n%s", instance_name, code, tail[-2000:])
            self.emit("launchError", {
                "instance": instance_name,
                "message": f"{instance_name} closed unexpectedly (exit code {code}). {_explain_crash(tail)}",
            })
        self.push_state()

    def get_instance_console(self, instance_name, offset=-1, session=None):
        try:
            name = sanitize_instance_name(instance_name)
            log_path = instance_dir(name) / "launch_output.log"
            with self._proc_lock:
                running = name in self._processes
                started = self._sessions.get(name)
            cur_session = str(int(started * 1000)) if started else None
            if not log_path.is_file():
                return {"success": True, "text": "", "offset": 0, "reset": True,
                        "running": running, "session": cur_session}
            size = log_path.stat().st_size
            try:
                offset = int(offset)
            except (TypeError, ValueError):
                offset = -1
            relaunched = cur_session is not None and cur_session != session and offset >= 0
            reset = offset < 0 or offset > size or relaunched
            if relaunched or offset > size:
                offset = 0
            elif offset < 0:
                offset = max(0, size - CONSOLE_TAIL_BYTES)
            with log_path.open("rb") as f:
                f.seek(offset)
                data = f.read(CONSOLE_CHUNK_BYTES)
            if reset and offset > 0:
                cut = data.find(b"\n")
                if cut != -1:
                    offset += cut + 1
                    data = data[cut + 1:]
            if running and offset + len(data) >= size and data and not data.endswith(b"\n"):
                cut = data.rfind(b"\n")
                data = data[:cut + 1] if cut != -1 else b""
            return {"success": True, "text": data.decode("utf-8", errors="replace"),
                    "offset": offset + len(data), "reset": reset,
                    "running": running, "session": cur_session}
        except Exception:
            log_exception("get_instance_console")
            return {"success": False, "error": "Couldn't read the console output."}

    def stop(self, instance_name):
        try:
            with self._proc_lock:
                proc = self._processes.get(instance_name)
                if proc:
                    self._stopping.add(instance_name)
            if not proc:
                return {"success": False, "error": "Instance is not running."}
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return {"success": True}
        except Exception:
            log_exception("stop")
            return {"success": False, "error": "Failed to stop instance."}


    def _window_hwnd(self):
        import ctypes
        from ctypes import wintypes
        hwnd = 0
        try:
            hwnd = int(self._window.native.Handle.ToInt64())
        except Exception:
            pass
        if not hwnd:
            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = wintypes.HWND
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            title = getattr(self._window, "title", None) or "Crit Launcher"
            hwnd = user32.FindWindowW(None, title) or 0
        return int(hwnd or 0)

    def set_titlebar_theme(self, payload):
        """Called by index.html whenever the colour mode / UI style changes (and once at startup).

        payload = {"mode": "light|dark|oled", "dark": bool, "background": "#rrggbb", "text": "#rrggbb"}
        """
        if sys.platform != "win32":
            return {"success": False, "error": "Title bar theming is Windows-only"}
        try:
            import ctypes
            if self._window is None:
                return {"success": False, "error": "Window not ready"}
            hwnd = self._window_hwnd()
            if not hwnd:
                return {"success": False, "error": "Window handle not found"}

            payload = payload or {}
            dark = 1 if payload.get("dark") else 0
            if _dwm_set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, dark) != 0:
                _dwm_set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, dark)

            
            bg = _colorref(payload.get("background") or "#0b0c0e")
            fg = _colorref(payload.get("text") or "#e8eaed")
            _dwm_set_attr(hwnd, DWMWA_CAPTION_COLOR, bg)
            _dwm_set_attr(hwnd, DWMWA_BORDER_COLOR, bg)
            _dwm_set_attr(hwnd, DWMWA_TEXT_COLOR, fg)

            
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)
            return {"success": True}
        except Exception as e:
            log_exception("set_titlebar_theme")
            return {"success": False, "error": str(e)}

    def ensure_assets(self):
        """index.html calls this when assets/<bg>.png isn't readable yet (first run, packaged exe)."""
        try:
            return ensure_home_assets()
        except Exception:
            log_exception("ensure_assets")
            return {"success": False, "files": {}}

    def ensure_mc_textures(self):
        try:
            return ensure_mc_textures()
        except Exception:
            log_exception("ensure_mc_textures")
            return {"success": False, "files": {}}

    def ensure_mc_panorama(self):
        try:
            return ensure_mc_panorama()
        except Exception:
            log_exception("ensure_mc_panorama")
            return {"success": False}

    def apply_saved_titlebar_theme(self):
        """Theme the title bar from saved settings, so it is right before the page's JS has loaded."""
        try:
            st = settings_store.read()
            mode = st.get("color_mode") if st.get("color_mode") in COLOR_MODES else "dark"
            ui = st.get("ui_style")
            style = ui if ui in ("modrinth", "fastclient", "sklauncher", "minecraft") else "default"
            if style in ("fastclient", "sklauncher", "minecraft"):
                mode = "dark"
            bg, fg = TITLEBAR_FALLBACKS[(mode, style)]
            self.set_titlebar_theme({"mode": mode, "dark": mode != "light",
                                     "background": bg, "text": fg})
        except Exception:
            log_exception("apply_saved_titlebar_theme")

    
    
    
    def _install_frame_hook(self) -> bool:
        if getattr(self, "_frame_proc", None) is not None:
            return True
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = self._window_hwnd()
        if not hwnd:
            return False

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        get_ptr = user32.GetWindowLongPtrW if is64 else user32.GetWindowLongW
        set_ptr = user32.SetWindowLongPtrW if is64 else user32.SetWindowLongW
        get_ptr.restype = ctypes.c_void_p
        get_ptr.argtypes = [wintypes.HWND, ctypes.c_int]
        set_ptr.restype = ctypes.c_void_p
        set_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        call = user32.CallWindowProcW
        call.restype = LRESULT
        call.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        is_zoomed = user32.IsZoomed
        is_zoomed.argtypes = [wintypes.HWND]

        old = get_ptr(hwnd, GWLP_WNDPROC)
        if not old:
            return False

        def _frame_px(h):
            
            try:
                user32.GetDpiForWindow.argtypes = [wintypes.HWND]
                dpi = user32.GetDpiForWindow(h) or 96
                return user32.GetSystemMetricsForDpi(33, dpi) + user32.GetSystemMetricsForDpi(92, dpi)
            except Exception:
                return user32.GetSystemMetrics(33) + user32.GetSystemMetrics(92)

        def _proc(h, msg, wp, lp):
            try:
                if msg == WM_NCCALCSIZE and wp and not getattr(self, "_frame_disabled", False):
                    params = ctypes.cast(lp, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                    top = params.rgrc[0].top
                    call(old, h, msg, wp, lp)             
                    if is_zoomed(h):
                        
                        
                        params.rgrc[0].top = top + _frame_px(h)
                    else:
                        params.rgrc[0].top = top          
                    return 0                              
                if msg == WM_NCDESTROY:
                    set_ptr(h, GWLP_WNDPROC, old)
            except Exception:
                pass
            return call(old, h, msg, wp, lp)

        self._frame_proc = WNDPROC(_proc)                 
        set_ptr(hwnd, GWLP_WNDPROC, ctypes.cast(self._frame_proc, ctypes.c_void_p))
        _dwm_set_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)   
        return True

    def _frame_state(self) -> dict:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = self._window_hwnd()
        return {"success": True, "maximized": bool(user32.IsZoomed(ctypes.c_void_p(hwnd))),
                "active": (user32.GetForegroundWindow() or 0) == hwnd}

    def _frame_geometry(self, hwnd):
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")          
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = ctypes.c_void_p
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
        wr, cr, pt = wintypes.RECT(), wintypes.RECT(), wintypes.POINT(0, 0)
        u.GetWindowRect(hwnd, ctypes.byref(wr))
        u.GetClientRect(hwnd, ctypes.byref(cr))
        u.ClientToScreen(hwnd, ctypes.byref(pt))
        kids = []
        Enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(child, _lp):
            try:
                if (u.GetParent(child) or 0) == hwnd and u.IsWindowVisible(child):
                    r = wintypes.RECT()
                    u.GetWindowRect(child, ctypes.byref(r))
                    kids.append((int(child), r.left, r.top, r.right, r.bottom))
            except Exception:
                pass
            return True

        cb = Enum(_cb)
        u.EnumChildWindows(hwnd, ctypes.cast(cb, ctypes.c_void_p), 0)
        return {"window": (wr.left, wr.top, wr.right, wr.bottom), "client_origin": (pt.x, pt.y),
                "client_size": (cr.right, cr.bottom), "maximized": bool(u.IsZoomed(hwnd)), "children": kids}

    def _fit_children(self, hwnd) -> bool:
        """Make sure the web view fills the whole client area (it must start at the client's top-left)."""
        import ctypes
        from ctypes import wintypes
        g = self._frame_geometry(hwnd)
        ox, oy = g["client_origin"]
        cw, ch = g["client_size"]
        u = ctypes.WinDLL("user32")
        u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
        moved = False
        for kid, left, top, right, bottom in g["children"]:
            if (right - left) < cw * 0.5:
                continue
            if left != ox or top != oy or (right - left) != cw or (bottom - top) != ch:
                u.MoveWindow(kid, 0, 0, cw, ch, True)
                moved = True
        return moved

    def _disable_frame_hook(self, hwnd):
        import ctypes
        self._frame_disabled = True
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)  

    def _verify_frame(self):
        """After the caption is removed: log what the window looks like, fix the web view if it is
        misplaced, and if the page still wouldn't start at the top, go back to the normal title bar
        so the top bar is never cut off."""
        try:
            hwnd = self._window_hwnd()
            if not hwnd or getattr(self, "_frame_disabled", False):
                return
            g = self._frame_geometry(hwnd)
            logging.info("custom title bar geometry: %s", g)
            if g["maximized"]:
                return
            if self._fit_children(hwnd):
                g = self._frame_geometry(hwnd)
                logging.info("custom title bar: web view was misplaced, resized it. now: %s", g)
            if g["client_origin"][1] != g["window"][1]:
                logging.warning("custom title bar: client area doesn't start at the window top, using the native bar")
                self._disable_frame_hook(hwnd)
                self.emit("customTitlebarOff")
        except Exception:
            log_exception("_verify_frame")

    def enable_custom_titlebar(self):
        """index.html calls this once at startup. Until it succeeds the native bar stays."""
        if sys.platform != "win32" or self._window is None or getattr(self, "_frame_disabled", False):
            return {"success": False}
        try:
            if settings_store.read().get("native_titlebar"):      
                return {"success": False}
            if not self._install_frame_hook():
                return {"success": False}
            threading.Timer(0.7, self._verify_frame).start()
            return self._frame_state()
        except Exception:
            log_exception("enable_custom_titlebar")
            return {"success": False}

    def get_window_state(self):
        try:
            return self._frame_state()
        except Exception:
            return {"success": False}

    def window_control(self, action):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(self._window_hwnd())
            if action == "minimize":
                user32.ShowWindow(hwnd, SW_MINIMIZE)
            elif action in ("maximize", "restore"):
                user32.ShowWindow(hwnd, SW_RESTORE if user32.IsZoomed(hwnd) else SW_MAXIMIZE)
            elif action == "close":
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)     
            else:
                return {"success": False}
            return {"success": True}
        except Exception:
            log_exception("window_control")
            return {"success": False}

    def window_drag(self):
        """Hand the drag to Windows itself, so snapping, maximize-on-drag and restore-on-drag all work.

        The web view owns the mouse capture, and only the window's own UI thread can take it over,
        so the move is started on that thread (not on the js-api worker thread)."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):   
                return {"success": False}
            hwnd = self._window_hwnd()
            if not hwnd:
                return {"success": False}
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)

            def _start():
                try:
                    user32.ReleaseCapture()
                    user32.SendMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, lparam)
                except Exception:
                    log_exception("window_drag (ui thread)")

            try:
                from System import Action
                self._window.native.BeginInvoke(Action(_start))   
            except Exception:
                log_exception("window_drag: UI-thread invoke unavailable, falling back")
                user32.ReleaseCapture()
                user32.PostMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, lparam)
            return {"success": True}
        except Exception:
            log_exception("window_drag")
            return {"success": False}

    def save_settings(self, patch: dict):
        try:
            def _apply(settings):
                if "memory_mb" in patch:
                    try:
                        mb = int(patch["memory_mb"])
                        settings["memory_mb"] = clamp_memory_mb(mb)
                    except (TypeError, ValueError):
                        pass
                if "java_path" in patch:
                    settings["java_path"] = (patch["java_path"] or "").strip() or None
                if "min_memory_mb" in patch:
                    try:
                        mm = int(patch["min_memory_mb"] or 0)
                        settings["min_memory_mb"] = 0 if mm < 256 else min(mm, 32768)
                    except (TypeError, ValueError):
                        pass
                if "jvm_args" in patch:
                    settings["jvm_args"] = str(patch["jvm_args"] or "").strip()[:600]
                for key in ("game_width", "game_height"):
                    if key in patch:
                        try:
                            v = int(patch[key] or 0)
                            settings[key] = 0 if v < 1 else max(240, min(v, 7680))
                        except (TypeError, ValueError):
                            pass
                if "fullscreen" in patch:
                    settings["fullscreen"] = bool(patch["fullscreen"])
                if "custom_accent" in patch and _clean_hex(patch["custom_accent"], None):
                    settings["custom_accent"] = _clean_hex(patch["custom_accent"])
                if isinstance(patch.get("recent_accents"), list):
                    settings["recent_accents"] = [_clean_hex(x) for x in patch["recent_accents"]
                                                  if _clean_hex(x, None)][:10]
                if "reduce_motion" in patch:
                    settings["reduce_motion"] = bool(patch["reduce_motion"])
                if "bg_pattern" in patch:
                    settings["bg_pattern"] = bool(patch["bg_pattern"])
                if "sidebar_collapsed" in patch:
                    settings["sidebar_collapsed"] = bool(patch["sidebar_collapsed"])
                if patch.get("theme") in THEMES:
                    settings["theme"] = patch["theme"]
                if patch.get("ui_style") in UI_STYLES:
                    settings["ui_style"] = patch["ui_style"]
                if patch.get("addon_view") in ADDON_VIEWS:
                    settings["addon_view"] = patch["addon_view"]
                if "addon_instance" in patch:
                    ai = patch["addon_instance"]
                    settings["addon_instance"] = ai.strip()[:200] if isinstance(ai, str) and ai.strip() else None
                if patch.get("color_mode") in COLOR_MODES:
                    settings["color_mode"] = patch["color_mode"]
                if patch.get("catalog_source") in CATALOG_SOURCES:
                    settings["catalog_source"] = patch["catalog_source"]
                if patch.get("addon_source") in ADDON_SOURCES:
                    settings["addon_source"] = patch["addon_source"]
                if "agreement_accepted" in patch:
                    settings["agreement_accepted"] = bool(patch["agreement_accepted"])
                if "agreement_version" in patch:
                    settings["agreement_version"] = max(0, min(2, int(patch["agreement_version"])))
                if "server_beta_seen" in patch:
                    settings["server_beta_seen"] = bool(patch["server_beta_seen"])
                if isinstance(patch.get("local_skin_warning_seen"), list):
                    settings["local_skin_warning_seen"] = [str(x) for x in patch["local_skin_warning_seen"]][:200]
            settings = settings_store.update(_apply)
            _JAVA_UI_CACHE["at"] = 0.0
            self.push_state()
            return {"success": True, "settings": settings}
        except Exception:
            log_exception("save_settings")
            return {"success": False, "error": "Failed to save settings."}


    def _search_curseforge(self, query, project_type, index, offset, limit, filters):
        if project_type not in CF_CLASS_IDS:
            return {"success": False, "error": "CurseForge has no data packs. Switch the source to Modrinth."}
        try:
            found = _cf_search(query, project_type, index, offset, limit, filters)
            return {"success": True, "hits": found["hits"], "source": "curseforge",
                    "total": int(found.get("total") or 0)}
        except CurseForgeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("search_curseforge")
            return {"success": False, "error": "Couldn't search CurseForge."}

    def search_addons(self, query="", project_type="mod", index=None, offset=0, limit=30, filters=None,
                      source="modrinth"):
        if source == "curseforge":
            try:
                if project_type not in ADDON_TYPES:
                    project_type = "mod"
                if index not in ("relevance", "downloads", "follows", "newest", "updated"):
                    index = "relevance" if query else "downloads"
                off = max(0, int(offset or 0))
                lim = max(1, min(int(limit or 30), 100))
            except (TypeError, ValueError):
                off, lim = 0, 30
            return self._search_curseforge(query, project_type, index, off, lim, filters)
        try:
            if project_type not in ADDON_TYPES:
                project_type = "mod"
            if index not in ("relevance", "downloads", "follows", "newest", "updated"):
                index = "relevance" if query else "downloads"
            try:
                offset = max(0, int(offset or 0))
                limit = max(1, min(int(limit or 30), 100))
            except (TypeError, ValueError):
                offset, limit = 0, 30
            facets = [[f"project_type:{project_type}"]]
            if isinstance(filters, dict):
                ok = re.compile(r"^[A-Za-z0-9_.+\-]{1,40}$")
                ver = filters.get("version")
                if isinstance(ver, str) and ok.match(ver):
                    facets.append([f"versions:{ver}"])
                loaders = [x for x in (filters.get("loaders") or []) if isinstance(x, str) and ok.match(x)]
                if loaders:
                    facets.append([f"categories:{x}" for x in loaders])
                for cat in (filters.get("categories") or [])[:12]:
                    if isinstance(cat, str) and ok.match(cat):
                        facets.append([f"categories:{cat}"])
            params = {
                "query": query or "",
                "limit": str(limit),
                "offset": str(offset),
                "facets": json.dumps(facets),
                "index": index,
            }
            url = f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params)
            data = http_get_json(url)
            return {"success": True, "hits": data.get("hits", []),
                    "total": int(data.get("total_hits", 0) or 0), "source": "modrinth"}
        except Exception:
            log_exception("search_addons")
            return {"success": False, "error": "Failed to search Modrinth."}

    def get_addon_categories(self):
        try:
            now = time.time()
            if _addon_cats_mem["value"] and now - _addon_cats_mem["at"] < 86400:
                return {"success": True, "categories": _addon_cats_mem["value"]}
            data = http_get_json(f"{MODRINTH_API}/tag/category")
            cats = [{"name": c.get("name"), "project_type": c.get("project_type"), "header": c.get("header")}
                    for c in data if isinstance(c, dict) and c.get("name")]
            _addon_cats_mem["at"], _addon_cats_mem["value"] = now, cats
            return {"success": True, "categories": cats}
        except Exception:
            log_exception("get_addon_categories")
            return {"success": False, "error": "Couldn't load categories."}

    def get_addon(self, project_id):
        try:
            data = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}")
            return {"success": True, "project": data}
        except Exception:
            log_exception("get_addon")
            return {"success": False, "error": "Failed to load addon info."}

    def get_addon_targets(self, project_type="mod"):
        try:
            result = []
            for inst in list_instances():
                loader = inst.get("loader", "vanilla")
                if project_type == "mod" and loader not in MOD_LOADERS:
                    continue
                if project_type == "shader" and loader != "fabric":
                    continue
                item = {
                    "name": inst["name"], "version": inst["version"],
                    "loader": loader, "loader_version": inst.get("loader_version"),
                    "installed": is_installed(inst),
                }
                if project_type == "shader":
                    item["iris"] = self._fabric_has_iris(inst["name"])
                result.append(item)
            return {"success": True, "instances": result}
        except Exception:
            log_exception("get_addon_targets")
            return {"success": False, "error": "Failed to list instances."}

    def _find_version(self, project_id, loader=None, mc_version=None):
        params = {}
        if loader:
            params["loaders"] = json.dumps([loader])
        if mc_version:
            params["game_versions"] = json.dumps([mc_version])
        url = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return pick_best_version(http_get_json(url))

    def _download_version_file(self, version, dest_dir: Path):
        file_info = primary_file(version)
        if not file_info:
            raise RuntimeError("That version has no downloadable file.")
        filename = Path(file_info["filename"]).name
        dest = dest_dir / filename
        if not is_within(dest_dir, dest):
            raise RuntimeError("Unsafe file name in addon.")
        http_download(file_info["url"], dest, file_info.get("size"),
                      (file_info.get("hashes") or {}).get("sha1"))
        return filename

    def _install_mod_with_deps(self, version, mods_dir, loader, mc_version, visited, files, depth=0):
        files.append(self._download_version_file(version, mods_dir))
        if depth >= 3:
            return
        for dep in version.get("dependencies", []):
            if dep.get("dependency_type") != "required":
                continue
            pid = dep.get("project_id")
            if not pid or pid in visited:
                continue
            visited.add(pid)
            try:
                if dep.get("version_id"):
                    dep_version = http_get_json(
                        f"{MODRINTH_API}/version/{urllib.parse.quote(dep['version_id'])}")
                else:
                    dep_version = self._find_version(pid, loader, mc_version)
                if dep_version:
                    self._install_mod_with_deps(dep_version, mods_dir, loader, mc_version,
                                                visited, files, depth + 1)
            except Exception:
                log_exception(f"dependency {pid} failed")

    def get_addon_dependencies(self, project_id, instance_name=None):
        if isinstance(project_id, str) and project_id.startswith("cf:"):
            return self._cf_addon_dependencies(project_id, instance_name)
        try:
            meta = read_instance_meta(instance_name) if instance_name else None
            mc_version = meta["version"] if meta else None
            loader = meta.get("loader") if meta else None
            version = self._find_version(project_id, loader, mc_version)
            if not version:
                return {"success": True, "dependencies": []}
            deps = []
            for dep in version.get("dependencies", []):
                if dep.get("dependency_type") != "required":
                    continue
                pid = dep.get("project_id")
                if not pid:
                    continue
                try:
                    proj = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(pid)}")
                    deps.append({"project_id": pid, "title": proj.get("title", pid),
                                "icon_url": proj.get("icon_url")})
                except Exception:
                    deps.append({"project_id": pid, "title": pid, "icon_url": None})
            return {"success": True, "dependencies": deps}
        except Exception:
            log_exception("get_addon_dependencies")
            return {"success": False, "error": "Couldn't load dependencies."}

    def _cf_addon_dependencies(self, project_id, instance_name=None):
        try:
            meta = read_instance_meta(instance_name) if instance_name else None
            mc_version = meta["version"] if meta else None
            loader = meta.get("loader") if meta else None
            mod_id = _cf_number(project_id)
            files = _cf_get(f"/mods/{mod_id}/files", {"gameVersion": mc_version}) or []
            if isinstance(files, dict):
                files = files.get("data") or []
            chosen = _cf_pick_file(files, mc_version, loader)
            deps = []
            for dep in _cf_dependencies(mod_id, chosen.get("id") if chosen else None):
                dep_id = int(dep["modId"])
                title, icon_url = f"CurseForge mod {dep_id}", None
                try:
                    proj = _cf_get(f"/mods/{dep_id}") or {}
                    if isinstance(proj, dict):
                        title = proj.get("name") or title
                        logo = proj.get("logo") if isinstance(proj.get("logo"), dict) else {}
                        icon_url = logo.get("thumbnailUrl") or logo.get("url")
                except Exception:
                    pass
                deps.append({"project_id": f"cf:{dep_id}", "title": title, "icon_url": icon_url})
            return {"success": True, "dependencies": deps}
        except CurseForgeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            log_exception("get_addon_dependencies curseforge")
            return {"success": False, "error": "Couldn't load dependencies."}

    def _install_curseforge(self, project_id, project_type, instance_name, include_deps):
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            mc_version = meta["version"]
            loader = meta.get("loader", "vanilla")
            if project_type == "mod":
                if loader not in MOD_LOADERS:
                    return {"success": False,
                            "error": f"{LOADER_NICE.get(loader, 'Vanilla')} instances can't run mods."}
                dest_dir = instance_mods_dir(instance_name)
            elif project_type == "resourcepack":
                dest_dir = instance_game_dir(instance_name) / "resourcepacks"
            elif project_type == "shader":
                dest_dir = instance_game_dir(instance_name) / "shaderpacks"
            else:
                return {"success": False, "error": "CurseForge installs support mods, resource packs and shaders."}
            dest_dir.mkdir(parents=True, exist_ok=True)
            files = curseforge_install_to(_cf_number(project_id), dest_dir, mc_version,
                                          loader if project_type == "mod" else None,
                                          set(), 0, bool(include_deps))
            return {"success": True, "files": files}
        except CurseForgeError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            log_exception("install_curseforge")
            return {"success": False, "error": f"Failed to install: {exc}"}

    def install_addon(self, project_id, project_type, instance_name, include_deps=True):
        if isinstance(project_id, str) and project_id.startswith("cf:"):
            return self._install_curseforge(project_id, project_type, instance_name, include_deps)
        try:
            meta = read_instance_meta(instance_name)
            if not meta:
                return {"success": False, "error": "Instance not found."}
            mc_version = meta["version"]
            loader = meta.get("loader", "vanilla")

            if project_type == "mod":
                if loader not in MOD_LOADERS:
                    return {"success": False,
                            "error": f"{LOADER_NICE.get(loader, 'Vanilla')} instances can't run mods."}
                version = self._find_version(project_id, loader, mc_version)
                if not version:
                    return {"success": False,
                            "error": f"No version of this mod supports {LOADER_NICE.get(loader, loader)} {mc_version}."}
                mods_dir = instance_mods_dir(instance_name)
                mods_dir.mkdir(parents=True, exist_ok=True)
                files: list = []
                if include_deps:
                    self._install_mod_with_deps(version, mods_dir, loader, mc_version,
                                                {project_id}, files)
                else:
                    files.append(self._download_version_file(version, mods_dir))
                return {"success": True, "files": files}

            if project_type == "resourcepack":
                note = None
                version = self._find_version(project_id, None, mc_version)
                if not version:
                    version = self._find_version(project_id)
                    note = f"No exact match for {mc_version}, installed the newest version instead."
                if not version:
                    return {"success": False, "error": "That resource pack has no downloadable version."}
                dest_dir = instance_game_dir(instance_name) / "resourcepacks"
                dest_dir.mkdir(parents=True, exist_ok=True)
                name = self._download_version_file(version, dest_dir)
                return {"success": True, "files": [name], "note": note}

            if project_type == "shader":
                if loader != "fabric":
                    return {"success": False, "error": "Shaders install into Fabric instances (with Iris)."}
                return self._install_shader(project_id, instance_name, meta)

            return {"success": False, "error": "Unsupported addon type."}
        except Exception as exc:
            log_exception("install_addon")
            return {"success": False, "error": f"Failed to install: {exc}"}

    def _critclient_worker(self, name, mc_version, loader, jar_name):
        try:
            self.emit("installStarted", {"instance": name, "title": f"Installing Crit Client into {name}..."})
            tracker = ProgressTracker(self.emit)
            mods_dir = instance_mods_dir(name)
            mods_dir.mkdir(parents=True, exist_ok=True)

            tracker.reset(1_000_000)
            tracker.set_task("Downloading Crit Client", force=True)
            jar_name = Path(jar_name).name
            http_download(CRIT_CLIENT_BASE + urllib.parse.quote(jar_name), mods_dir / jar_name,
                          progress_cb=tracker.add)

            tracker.set_task("Checking which mods are available", force=True)
            picks = []
            for slug in CRIT_CLIENT_MODS:
                try:
                    version = self._find_version(slug, loader, mc_version)
                except Exception:
                    log_exception(f"crit client mod lookup: {slug}")
                    version = None
                if version:
                    picks.append((slug, version))
                else:
                    logging.info("Crit Client: %s has no %s build for %s, skipping", slug, loader, mc_version)

            tracker.reset(len(picks) + 1, unit="steps")
            visited, files = set(), []
            for slug, version in picks:
                tracker.set_task(f"Downloading {slug}", force=True)
                pid = version.get("project_id")
                if pid in visited:
                    tracker.add(1)
                    continue
                visited.add(pid)
                try:
                    self._install_mod_with_deps(version, mods_dir, loader, mc_version, visited, files)
                except Exception:
                    log_exception(f"crit client mod install failed: {slug}")
                tracker.add(1)
            tracker.add(1)

            self.emit("installFinished", {"instance": name, "message": f"Crit Client installed into {name}"})
        except Exception as exc:
            log_exception("crit client install failed")
            shutil.rmtree(instance_dir(name), ignore_errors=True)
            self.emit("installError", {"message": f"Crit Client install failed: {exc}"})
        finally:
            with self._proc_lock:
                self._installing.discard(name)
            self.push_state()

    def install_modpack(self, project_id):
        with self._proc_lock:
            if self._addon_busy:
                return {"success": False, "error": "Another addon install is already running."}
            self._addon_busy = True
        if isinstance(project_id, str) and project_id.startswith("cf:"):
            threading.Thread(target=self._cf_modpack_worker, args=(project_id,), daemon=True).start()
        else:
            threading.Thread(target=self._modpack_worker, args=(project_id,), daemon=True).start()
        return {"success": True}

    def _cf_modpack_worker(self, project_id):
        """Install a CurseForge modpack: manifests list projectID/fileID pairs plus overrides."""
        pack_path = work = None
        try:
            tracker = ProgressTracker(self.emit)
            tracker.event = "addonProgress"
            tracker.set_task("Fetching modpack", force=True)

            mod_id = _cf_number(project_id)
            project = _cf_get(f"/mods/{mod_id}") or {}
            files = _cf_get(f"/mods/{mod_id}/files") or []
            if isinstance(files, dict):
                files = files.get("data") or []
            files = [f for f in files if isinstance(f, dict) and not f.get("isServerPack")]
            zips = [f for f in files if str(f.get("fileName") or "").lower().endswith(".zip")]
            files = zips or files
            files.sort(key=lambda f: str(f.get("fileDate") or ""), reverse=True)
            releases = [f for f in files if int(f.get("releaseType") or 1) == 1]
            pool = releases or files
            if not pool:
                raise RuntimeError("This modpack has no downloadable file.")
            chosen = pool[0]

            work = TMP_DIR / f"cf_pack_{mod_id}"
            work.mkdir(parents=True, exist_ok=True)
            tracker.reset(chosen.get("fileLength") or 1_000_000)
            tracker.set_task("Downloading modpack", force=True)
            pack_path = work / _cf_download_file(mod_id, chosen, work)

            with zipfile.ZipFile(pack_path) as zf:
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except KeyError:
                    raise RuntimeError("That CurseForge pack is missing its manifest.json.")
            mc = manifest.get("minecraft") or {}
            mc_version = mc.get("version")
            if not mc_version:
                raise RuntimeError("This modpack doesn't say which Minecraft version it needs.")

            loader, loader_version = "vanilla", None
            for entry in (mc.get("modLoaders") or []):
                if not isinstance(entry, dict) or not entry.get("primary"):
                    continue
                kind, _, ver = str(entry.get("id") or "").partition("-")
                if kind == "fabric":
                    loader, loader_version = "fabric", ver
                elif kind == "forge":
                    loader, loader_version = "forge", ver
                elif kind == "neoforge":
                    loader, loader_version = "neoforge", ver
                elif kind in ("quilt", "liteloader"):
                    raise RuntimeError(f"This modpack needs {kind.capitalize()}, which Crit Launcher doesn't support yet.")

            name = unique_instance_name(manifest.get("name") or project.get("name") or "Modpack")
            self._make_instance(name, mc_version, loader, loader_version)
            game_dir = instance_game_dir(name)
            mods_dir = game_dir / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)

            wanted = []
            for item in (manifest.get("files") or []):
                if not isinstance(item, dict) or item.get("required") is False:
                    continue
                pid, fid = item.get("projectID"), item.get("fileID")
                if pid and fid:
                    wanted.append((int(pid), int(fid)))

            tracker.set_task("Resolving modpack files", force=True)

            def _file_info(pair):
                try:
                    info = _cf_get(f"/mods/{pair[0]}/files/{pair[1]}") or {}
                    return info if isinstance(info, dict) else None
                except Exception:
                    return None

            infos = []
            if wanted:
                with ThreadPoolExecutor(max_workers=6) as pool_exec:
                    for info in pool_exec.map(_file_info, wanted):
                        if info:
                            infos.append(info)

            items = []
            for info in infos:
                fname = Path(str(info.get("fileName") or "")).name
                url = info.get("downloadUrl")
                if not fname or not isinstance(url, str) or not url.startswith("https://"):
                    continue
                if not is_within(mods_dir, mods_dir / fname):
                    continue
                items.append(({"path": fname, "url": url, "size": info.get("fileLength"),
                               "sha1": _cf_file_sha1(info)}, mods_dir))

            tracker.reset(sum(entry_weight(e) for e, _ in items) + 1)
            tracker.set_task("Downloading modpack files", force=True)
            download_all(items, tracker, workers=8)

            tracker.set_task("Applying overrides", force=True)
            with zipfile.ZipFile(pack_path) as zf:
                for member in zf.namelist():
                    for prefix in ("overrides/", "client-overrides/"):
                        if not member.startswith(prefix):
                            continue
                        rel = member[len(prefix):]
                        if not rel:
                            break
                        dest = game_dir / rel
                        if not is_within(game_dir, dest):
                            break
                        if member.endswith("/"):
                            dest.mkdir(parents=True, exist_ok=True)
                        else:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(zf.read(member))
                        break

            self.push_state()
            self.emit("addonFinished", {"name": name})
        except Exception as exc:
            log_exception("curseforge modpack install failed")
            self.emit("addonError", {"message": str(exc) or "Modpack install failed."})
        finally:
            try:
                if pack_path:
                    Path(pack_path).unlink(missing_ok=True)
                if work:
                    shutil.rmtree(work, ignore_errors=True)
            except OSError:
                pass
            with self._proc_lock:
                self._addon_busy = False

    def _modpack_worker(self, project_id):
        pack_path = None
        try:
            tracker = ProgressTracker(self.emit)
            tracker.event = "addonProgress"
            tracker.set_task("Fetching modpack", force=True)

            project = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}")
            versions = http_get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id)}/version")
            version = pick_best_version(versions)
            if not version:
                raise RuntimeError("This modpack has no downloadable versions.")
            files = version.get("files", [])
            pack_file = (next((f for f in files if f.get("filename", "").endswith(".mrpack")), None)
                         or primary_file(version))
            if not pack_file:
                raise RuntimeError("This modpack has no downloadable file.")

            pack_path = TMP_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', project_id)}.mrpack"
            tracker.reset(pack_file.get("size") or 1_000_000)
            tracker.set_task("Downloading modpack", force=True)
            http_download(pack_file["url"], pack_path, pack_file.get("size"),
                          (pack_file.get("hashes") or {}).get("sha1"), tracker.add)

            with zipfile.ZipFile(pack_path) as zf:
                index = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
                deps = index.get("dependencies", {})
                mc_version = deps.get("minecraft")
                if not mc_version:
                    raise RuntimeError("This modpack doesn't say which Minecraft version it needs.")

                loader, loader_version = "vanilla", None
                if "fabric-loader" in deps:
                    loader, loader_version = "fabric", deps["fabric-loader"]
                elif "forge" in deps:
                    loader, loader_version = "forge", deps["forge"]
                elif "neoforge" in deps:
                    loader, loader_version = "neoforge", deps["neoforge"]
                elif "quilt-loader" in deps:
                    raise RuntimeError("This modpack needs Quilt, which Crit Launcher doesn't support yet.")

                name = unique_instance_name(index.get("name") or project.get("title") or "Modpack")
                self._make_instance(name, mc_version, loader, loader_version)
                game_dir = instance_game_dir(name)

                items = []
                for f in index.get("files", []):
                    if (f.get("env") or {}).get("client") == "unsupported":
                        continue
                    rel = f.get("path", "")
                    urls = f.get("downloads") or []
                    if not rel or not urls or not is_within(game_dir, game_dir / rel):
                        continue
                    items.append(({
                        "path": rel, "url": urls[0], "size": f.get("fileSize"),
                        "sha1": (f.get("hashes") or {}).get("sha1"),
                    }, game_dir))

                tracker.reset(sum(entry_weight(e) for e, _ in items) + 1)
                tracker.set_task("Downloading modpack files", force=True)
                download_all(items, tracker, workers=8)

                tracker.set_task("Applying overrides", force=True)
                for member in zf.namelist():
                    for prefix in ("overrides/", "client-overrides/"):
                        if member.startswith(prefix):
                            rel = member[len(prefix):]
                            if not rel:
                                break
                            dest = game_dir / rel
                            if not is_within(game_dir, dest):
                                break
                            if member.endswith("/"):
                                dest.mkdir(parents=True, exist_ok=True)
                            else:
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                dest.write_bytes(zf.read(member))
                            break

            self.push_state()
            self.emit("addonFinished", {"name": name})
        except Exception as exc:
            log_exception("modpack install failed")
            self.emit("addonError", {"message": str(exc) or "Modpack install failed."})
        finally:
            if pack_path:
                try:
                    Path(pack_path).unlink(missing_ok=True)
                except OSError:
                    pass
            with self._proc_lock:
                self._addon_busy = False


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--launch-instance":
        instance_name = " ".join(sys.argv[2:])
        api = API()
        result = api.launch(instance_name)
        if not result.get("success"):
            logging.error("Shortcut launch failed for %s: %s", instance_name, result.get("error"))
            return 1
        while True:
            with api._proc_lock:
                installing = instance_name in api._installing
            if not installing:
                break
            time.sleep(0.25)
        return 0

    if IS_LINUX:
        # Use the Qt (QtWebEngine) backend. It ships inside the AppImage; GTK/WebKit would not.
        os.environ.setdefault("QT_API", "pyqt6")
    import webview

    start_asset_download()
    api = API()
    index_path = prepare_page()

    window = webview.create_window(
        "Crit Launcher",
        str(index_path),
        js_api=api,
        width=1080,
        height=700,
        min_size=(900, 580),
        resizable=True,
        background_color="#0b0c0e",
    )
    api.set_window(window)

    def on_closing():
        try:
            api.stop_all_servers()
        except Exception:
            log_exception("stop servers on close")

    window.events.closing += on_closing

    def on_shown():
        api.apply_saved_titlebar_theme()

    try:
        window.events.shown += on_shown
    except Exception:
        log_exception("hook shown event")

    webview.start(gui="qt" if IS_LINUX else None, debug=False)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
