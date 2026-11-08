"""JS-bridge API: desktop shortcuts."""

import base64
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from ..config.paths import APP_DIR, DATA_DIR, ENTRY_SCRIPT, IS_LINUX
from ..core.logs import log_exception
from ..core.system import CREATE_NO_WINDOW
from .store import read_instance_meta, sanitize_instance_name


class ShortcutsApi:
    """Desktop / app-menu shortcuts that launch an instance."""

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
            command = [sys.executable, str(ENTRY_SCRIPT)]
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
                    str(ENTRY_SCRIPT), "--launch-instance", instance_name
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
