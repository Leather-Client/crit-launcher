"""Operating-system helpers: OS/arch names, child-process environment, opening folders."""

import os
import platform
import subprocess
import sys
from pathlib import Path

from ..config.paths import IS_LINUX


def install_clean_env_popen() -> None:
    """Frozen Linux builds: make every subprocess start with a clean environment."""
    if not (IS_LINUX and getattr(sys, "frozen", False)):
        return
    if getattr(subprocess.Popen, "_crit_clean_env", False):
        return
    _RealPopen = subprocess.Popen

    class _CleanEnvPopen(_RealPopen):
        _crit_clean_env = True

        # Qt WebEngine starts its own helper processes natively, so this only affects
        # programs launched from Python.
        def __init__(self, *args, env=None, **kwargs):
            super().__init__(*args, env=_clean_child_env(env), **kwargs)

    subprocess.Popen = _CleanEnvPopen


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


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


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
