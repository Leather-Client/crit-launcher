"""JS-bridge API: instance icons."""

import base64
import os
import uuid

from ..config.paths import INSTANCE_ICONS_DIR
from ..core.logs import log_exception
from .icons import INSTANCE_ICONS, custom_instance_icon_path, sanitize_custom_icon_name
from .store import list_instances, read_instance_meta, update_instance_meta


class InstanceIconsApi:
    """Built-in and custom instance icons."""

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
