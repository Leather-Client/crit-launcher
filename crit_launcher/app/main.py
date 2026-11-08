"""Program entry: builds the window and wires events."""

import logging
import os
import sys
import time

from ..bootstrap import init as init_environment
from ..config.paths import IS_LINUX
from ..core.logs import log_exception
from ..ui.page import prepare_page, start_asset_download
from .facade import API


def main():
    init_environment()
    if len(sys.argv) >= 3 and sys.argv[1] == "--launch-instance":
        instance_name = " ".join(sys.argv[2:])
        api = API()
        result = api.launch(instance_name)
        if not result.get("success"):
            logging.error("Shortcut launch failed for %s: %s", instance_name, result.get("error"))
            return 1
        while True:
            with api._proc_lock:
                installing = instance_name in api._installing
            if not installing:
                break
            time.sleep(0.25)
        return 0

    if IS_LINUX:
        # Use the Qt (QtWebEngine) backend. It ships inside the AppImage; GTK/WebKit would not.
        os.environ.setdefault("QT_API", "pyqt6")
    import webview

    start_asset_download()
    api = API()
    index_path = prepare_page()

    window = webview.create_window(
        "Crit Launcher",
        str(index_path),
        js_api=api,
        width=1080,
        height=700,
        min_size=(900, 580),
        resizable=True,
        background_color="#0b0c0e",
    )
    api.set_window(window)
    api._presence_sync()

    def on_closing():
        api._presence_sync_off()
        try:
            api.stop_all_servers()
        except Exception:
            log_exception("stop servers on close")

    window.events.closing += on_closing

    def on_shown():
        api.apply_saved_titlebar_theme()

    try:
        window.events.shown += on_shown
    except Exception:
        log_exception("hook shown event")

    webview.start(gui="qt" if IS_LINUX else None, debug=False)
