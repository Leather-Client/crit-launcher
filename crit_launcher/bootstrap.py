"""One-time process setup: data folders, log file and platform fixes.

Importing launcher modules has no side effects. Call init() once before using anything that
reads or writes the data directory (app.main.main() does this; tests call it themselves).
"""
from .config.paths import ensure_data_dirs
from .core.logs import setup_logging
from .core.system import install_clean_env_popen

_done = False


def init() -> None:
    global _done
    if _done:
        return
    _done = True
    ensure_data_dirs()
    setup_logging()
    install_clean_env_popen()
