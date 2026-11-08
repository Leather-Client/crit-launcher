"""Progress tracking for installs and downloads."""

import threading
import time


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
