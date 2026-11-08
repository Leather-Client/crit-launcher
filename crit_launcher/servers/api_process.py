"""JS-bridge API: server start/stop/console."""

import re
import subprocess
import threading
import time

from ..core.logs import log_exception
from ..core.system import CREATE_NO_WINDOW
from ..minecraft.java import console_java, java_info_from_meta, resolve_java
from .meta import read_server_meta, server_dir, write_server_meta
from .properties import _clamp_int, gamerule_candidates
from .runtime import (
    LOG_JOIN_RE,
    LOG_LEAVE_RE,
    ProcSampler,
    ServerRuntime,
    TICK_NOISE_RE,
    _has_java_error,
)


class ServerProcessApi:
    """Starting, stopping and talking to a running server."""

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
