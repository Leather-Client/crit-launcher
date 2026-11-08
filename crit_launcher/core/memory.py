"""System memory detection and the RAM slider limits."""

import os
import subprocess
import threading
from pathlib import Path

from .system import CREATE_NO_WINDOW, current_os_name


_MEM_TOTAL_MB = None
_MEM_LOCK = threading.Lock()


def total_system_memory_mb() -> int:
    """Physical RAM in MB, or 0 when it can't be determined."""
    global _MEM_TOTAL_MB
    with _MEM_LOCK:
        if _MEM_TOTAL_MB is not None:
            return _MEM_TOTAL_MB
    total = 0
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total = int(stat.ullTotalPhys // (1024 * 1024))
        elif current_os_name() == "osx":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 timeout=5, creationflags=CREATE_NO_WINDOW)
            total = int(out.stdout.decode().strip()) // (1024 * 1024)
        else:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                    break
    except Exception:
        total = 0
    if total > 0:
        with _MEM_LOCK:
            _MEM_TOTAL_MB = total
    return total


def memory_ceiling_mb() -> int:
    """Most a Minecraft instance should be given: about half of physical RAM."""
    total = total_system_memory_mb()
    if total <= 0:
        return 32768
    return max(1024, int(total * 0.5))


def clamp_memory_mb(value) -> int:
    mb = int(value)
    return max(512, min(mb, memory_ceiling_mb()))
