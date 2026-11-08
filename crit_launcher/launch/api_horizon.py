"""JS-bridge API: Horizon launching."""

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from ..accounts.errors import AuthError
from ..accounts.session import ensure_fresh_account
from ..accounts.store import accounts_store, find_account
from ..config.settings import settings_store
from ..core.logs import log_exception
from ..core.progress import ProgressTracker
from ..core.system import CREATE_NO_WINDOW
from ..horizon.layout import horizon_exe
from ..horizon.setup import horizon_setup
from ..instances.store import (
    ensure_game_layout,
    instance_dir,
    instance_game_dir,
    read_instance_meta,
    update_instance_meta,
)


class HorizonLaunchApi:
    """Launching Horizon builds."""

    def _horizon_worker(self, name, then_launch=False):
        """Installs Horizon, Java and Minecraft, then prepares the game's resources for one instance."""
        try:
            self.emit("installStarted", {"instance": name,
                                         "title": f"Setting up Horizon for {name}..."})
            tracker = ProgressTracker(self.emit)
            info = horizon_setup(name, tracker)
            update_instance_meta(name, lambda m: m.update(info))
            message = f"Horizon is ready in {name}"
            if not info.get("horizon_sounds"):
                message += ", but its sounds couldn't be set up, so the game will be silent. See launcher.log"
            self.emit("installFinished", {"instance": name, "message": message})
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
