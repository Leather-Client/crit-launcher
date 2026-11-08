"""Server process runtime state and sampling."""

import os
import re
import threading
import time
from pathlib import Path


def _has_java_error(line: str) -> bool:
    return "Incorrect argument" in line or "Unknown or incomplete" in line or "<--[HERE]" in line


class ProcSampler:

    def __init__(self, proc):
        self.proc = proc
        self.pid = proc.pid
        self._ps = None
        self._last = None
        try:
            import psutil
            self._ps = psutil.Process(self.pid)
            self._ps.cpu_percent(None)
        except Exception:
            self._ps = None

    def sample(self):
        ncpu = os.cpu_count() or 1
        try:
            if self._ps is not None:
                return self._ps.cpu_percent(None) / ncpu, self._ps.memory_info().rss
            if os.name == "nt":
                return self._sample_windows(ncpu)
            if os.path.exists(f"/proc/{self.pid}/stat"):
                return self._sample_linux(ncpu)
        except Exception:
            pass
        return None, None

    def _sample_windows(self, ncpu):
        import ctypes
        from ctypes import wintypes
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        handle = wintypes.HANDLE(int(self.proc._handle))

        class FT(ctypes.Structure):
            _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        c, e, kt, ut = FT(), FT(), FT(), FT()
        k32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
        cpu_100ns = ((kt.hi << 32) | kt.lo) + ((ut.hi << 32) | ut.lo)
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
        now = time.time()
        cpu = None
        if self._last:
            dt = now - self._last[0]
            if dt > 0:
                cpu = max(0.0, (cpu_100ns - self._last[1]) / 1e7 / dt / ncpu * 100)
        self._last = (now, cpu_100ns)
        return cpu, int(pmc.WorkingSetSize)

    def _sample_linux(self, ncpu):
        parts = Path(f"/proc/{self.pid}/stat").read_text().rsplit(")", 1)[1].split()
        ticks = int(parts[11]) + int(parts[12])
        hz = os.sysconf("SC_CLK_TCK")
        rss = int(Path(f"/proc/{self.pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        now = time.time()
        cpu = None
        if self._last:
            dt = now - self._last[0]
            if dt > 0:
                cpu = max(0.0, (ticks - self._last[1]) / hz / dt / ncpu * 100)
        self._last = (now, ticks)
        return cpu, rss


class ServerRuntime:
    def __init__(self, name):
        self.name = name
        self.proc = None
        self.status = "starting"
        self.started = time.time()
        self.log = []
        self.seq = 0
        self.pending = []
        self.players = {}
        self.exited = threading.Event()
        self.lock = threading.Lock()
        self.waiters = []
        self.swallow_until = 0.0
        self.eula_needed = False
        self.tick_ok = True
        self.tps = None
        self.mspt = None
        self.cpu = None
        self.ram = None
        self.stopping = False
        self.restart_after = False


TICK_NOISE_RE = re.compile(r"(The game is running|Target tick rate|Average time per tick|Percentiles|"
                           r"Unknown or incomplete command|<--\[HERE\]|Incorrect argument)")
LOG_JOIN_RE = re.compile(r"\]: ([A-Za-z0-9_.]{1,16}) joined the game\s*$")
LOG_LEAVE_RE = re.compile(r"\]: ([A-Za-z0-9_.]{1,16}) left the game\s*$")
