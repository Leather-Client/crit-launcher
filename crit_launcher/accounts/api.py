"""JS-bridge API: accounts."""

import logging
import re
import threading
import time
import urllib.parse

from ..config.settings import settings_store
from ..core.logs import log_exception
from .ely import ely_sign_in
from .errors import AuthError
from .microsoft import MS_REDIRECT_URI, ms_authorize_url, ms_sign_in, ms_token_request
from .offline import offline_uuid
from .store import accounts_store, find_account
from .tokens import public_account


class AccountsApi:
    """Adding, removing and switching accounts; Microsoft web sign-in."""

    def add_ely_account(self, username, password, totp=""):
        try:
            username = (username or "").strip()
            if not username or len(username) > 190:
                return {"success": False, "error": "Enter your Ely.by username or e-mail."}
            if not password:
                return {"success": False, "error": "Enter your Ely.by password."}
            account = ely_sign_in(username, password, (totp or "").strip())
            self.push_state()
            return {"success": True, "account": public_account(account)}
        except AuthError as exc:
            if str(exc) == "__totp__":
                return {"success": False, "totp": True,
                        "error": "This account has two-factor enabled. Enter the code from your app."}
            return {"success": False, "error": str(exc) or "Ely.by sign-in failed."}
        except Exception:
            logging.warning("add_ely_account failed")
            return {"success": False, "error": "Couldn't reach Ely.by. Check your connection."}

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
