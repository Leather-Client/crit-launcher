"""JS-bridge API: instance console log."""

from ..core.logs import log_exception
from ..instances.store import instance_dir, sanitize_instance_name


CONSOLE_TAIL_BYTES = 400_000
CONSOLE_CHUNK_BYTES = 262_144


class ConsoleApi:
    """Reading an instance's game log."""

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
