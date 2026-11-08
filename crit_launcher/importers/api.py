"""JS-bridge API: importing instances."""

import shutil
import threading
from pathlib import Path

from ..core.fsutil import _copy_tree_contents
from ..core.logs import log_exception
from ..instances.store import (
    ensure_game_layout,
    instance_config_dir,
    instance_dir,
    instance_game_dir,
    instance_mods_dir,
    list_instances,
    read_instance_meta,
    sanitize_instance_name,
)
from ..minecraft.loaders import MOD_LOADERS
from .common import LAUNCHER_LABELS
from .registry import (
    _LAUNCHER_SCANNERS,
    _find_scanned_instance,
    _guess_launcher_for_dir,
    _source_paths,
    scan_all_launchers,
    scan_custom_launcher_dir,
)


class ImportApi:
    """Importing instances from other launchers."""

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
