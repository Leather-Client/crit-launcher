"""JS-bridge API: start and stop the game."""

import logging
import subprocess
import threading
import time
from pathlib import Path

from ..accounts.errors import AuthError
from ..accounts.session import ensure_fresh_account
from ..accounts.store import accounts_store, find_account
from ..config.settings import settings_store
from ..core.logs import log_exception
from ..core.memory import clamp_memory_mb
from ..core.progress import ProgressTracker
from ..core.system import CREATE_NO_WINDOW
from ..horizon.sounds import horizon_sounds_pending
from ..instances.playtime import _bump_playtime
from ..instances.store import (
    ensure_game_layout,
    instance_dir,
    instance_game_dir,
    is_installed,
    read_instance_meta,
    update_instance_meta,
)
from ..minecraft.installer import Installer
from ..minecraft.java import console_java, java_info_from_meta, resolve_java
from .command import build_launch_command
from .crash import _explain_crash, _log_tail


class LaunchApi:
    """Starting and stopping the game."""

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
                if (meta.get("horizon_prepared") and saved and Path(saved).is_dir()
                        and not horizon_sounds_pending(saved)):
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
            needs_install = not is_installed(meta)
            if not needs_install:
                java_path = resolve_java(java_info_from_meta(meta), allow_download=False)
                if java_path:
                    with self._proc_lock:
                        self._installing.add(instance_name)

                    def go():
                        try:
                            try:
                                fresh = ensure_fresh_account(active_account)
                            except AuthError as exc:
                                self.emit("launchError", {"instance": instance_name,
                                                          "message": str(exc)})
                                return
                            result = self._do_launch(instance_name, meta, fresh, java_path)
                            if not result.get("success"):
                                self.emit("launchError", {"instance": instance_name,
                                                          "message": result.get("error", "Failed to launch.")})
                        except Exception:
                            log_exception("launch")
                            self.emit("launchError", {"instance": instance_name,
                                                      "message": "Failed to launch Minecraft."})
                        finally:
                            with self._proc_lock:
                                self._installing.discard(instance_name)
                    threading.Thread(target=go, daemon=True).start()
                    return {"success": True}

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
            shown = ["dontlogtokens!" if i and command[i - 1] == "--accessToken" else c
                     for i, c in enumerate(command)]
            logging.info("Launching %s: %s", instance_name, " ".join(shown)[:2500])

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
