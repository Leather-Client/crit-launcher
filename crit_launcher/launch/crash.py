"""Reading crash output and explaining it."""

import os
from pathlib import Path


def _log_tail(path: Path, max_bytes: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _explain_crash(tail: str) -> str:
    if "UnsupportedClassVersionError" in tail or "class file version" in tail:
        return "This Minecraft version needs a newer Java. Clear the custom Java path in Settings so Crit Launcher can pick one."
    if "Could not create the Java Virtual Machine" in tail or "Invalid maximum heap size" in tail:
        return "Java couldn't start with that memory setting. Try lowering the allocated memory in Settings."
    if "ClassNotFoundException" in tail or "NoClassDefFoundError" in tail:
        return "A library is missing. Delete the instance's files by re-installing it, then try again."
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    return lines[-1][:220] if lines else "Check launch_output.log in the instance folder."
