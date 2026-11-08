"""JS-bridge API: native window chrome."""

import logging
import sys
import threading

from ..config.settings import COLOR_MODES, settings_store
from ..core.logs import log_exception
from .winframe import (
    DWMWA_BORDER_COLOR,
    DWMWA_CAPTION_COLOR,
    DWMWA_TEXT_COLOR,
    DWMWA_USE_IMMERSIVE_DARK_MODE,
    DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
    DWMWA_WINDOW_CORNER_PREFERENCE,
    DWMWCP_ROUND,
    GWLP_WNDPROC,
    HTCAPTION,
    SWP_FLAGS,
    SW_MAXIMIZE,
    SW_MINIMIZE,
    SW_RESTORE,
    TITLEBAR_FALLBACKS,
    WM_CLOSE,
    WM_NCCALCSIZE,
    WM_NCDESTROY,
    WM_NCLBUTTONDOWN,
    _NCCALCSIZE_PARAMS,
    _colorref,
    _dwm_set_attr,
)


class WindowApi:
    """Native window chrome: custom title bar, drag, resize, theme."""

    def _window_hwnd(self):
        import ctypes
        from ctypes import wintypes
        hwnd = 0
        try:
            hwnd = int(self._window.native.Handle.ToInt64())
        except Exception:
            pass
        if not hwnd:
            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = wintypes.HWND
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            title = getattr(self._window, "title", None) or "Crit Launcher"
            hwnd = user32.FindWindowW(None, title) or 0
        return int(hwnd or 0)

    def set_titlebar_theme(self, payload):
        """Called by index.html whenever the colour mode / UI style changes (and once at startup).

        payload = {"mode": "light|dark|oled", "dark": bool, "background": "#rrggbb", "text": "#rrggbb"}
        """
        if sys.platform != "win32":
            return {"success": False, "error": "Title bar theming is Windows-only"}
        try:
            import ctypes
            if self._window is None:
                return {"success": False, "error": "Window not ready"}
            hwnd = self._window_hwnd()
            if not hwnd:
                return {"success": False, "error": "Window handle not found"}

            payload = payload or {}
            dark = 1 if payload.get("dark") else 0
            if _dwm_set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, dark) != 0:
                _dwm_set_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, dark)

            
            bg = _colorref(payload.get("background") or "#0b0c0e")
            fg = _colorref(payload.get("text") or "#e8eaed")
            _dwm_set_attr(hwnd, DWMWA_CAPTION_COLOR, bg)
            _dwm_set_attr(hwnd, DWMWA_BORDER_COLOR, bg)
            _dwm_set_attr(hwnd, DWMWA_TEXT_COLOR, fg)

            
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)
            return {"success": True}
        except Exception as e:
            log_exception("set_titlebar_theme")
            return {"success": False, "error": str(e)}

    def apply_saved_titlebar_theme(self):
        """Theme the title bar from saved settings, so it is right before the page's JS has loaded."""
        try:
            st = settings_store.read()
            mode = st.get("color_mode") if st.get("color_mode") in COLOR_MODES else "dark"
            ui = st.get("ui_style")
            style = ui if ui in ("modrinth", "fastclient", "sklauncher", "minecraft") else "default"
            if style in ("fastclient", "sklauncher", "minecraft"):
                mode = "dark"
            bg, fg = TITLEBAR_FALLBACKS[(mode, style)]
            self.set_titlebar_theme({"mode": mode, "dark": mode != "light",
                                     "background": bg, "text": fg})
        except Exception:
            log_exception("apply_saved_titlebar_theme")

    def _install_frame_hook(self) -> bool:
        if getattr(self, "_frame_proc", None) is not None:
            return True
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = self._window_hwnd()
        if not hwnd:
            return False

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        get_ptr = user32.GetWindowLongPtrW if is64 else user32.GetWindowLongW
        set_ptr = user32.SetWindowLongPtrW if is64 else user32.SetWindowLongW
        get_ptr.restype = ctypes.c_void_p
        get_ptr.argtypes = [wintypes.HWND, ctypes.c_int]
        set_ptr.restype = ctypes.c_void_p
        set_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        call = user32.CallWindowProcW
        call.restype = LRESULT
        call.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        is_zoomed = user32.IsZoomed
        is_zoomed.argtypes = [wintypes.HWND]

        old = get_ptr(hwnd, GWLP_WNDPROC)
        if not old:
            return False

        def _frame_px(h):
            
            try:
                user32.GetDpiForWindow.argtypes = [wintypes.HWND]
                dpi = user32.GetDpiForWindow(h) or 96
                return user32.GetSystemMetricsForDpi(33, dpi) + user32.GetSystemMetricsForDpi(92, dpi)
            except Exception:
                return user32.GetSystemMetrics(33) + user32.GetSystemMetrics(92)

        def _proc(h, msg, wp, lp):
            try:
                if msg == WM_NCCALCSIZE and wp and not getattr(self, "_frame_disabled", False):
                    params = ctypes.cast(lp, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                    top = params.rgrc[0].top
                    call(old, h, msg, wp, lp)             
                    if is_zoomed(h):
                        
                        
                        params.rgrc[0].top = top + _frame_px(h)
                    else:
                        params.rgrc[0].top = top          
                    return 0                              
                if msg == WM_NCDESTROY:
                    set_ptr(h, GWLP_WNDPROC, old)
            except Exception:
                pass
            return call(old, h, msg, wp, lp)

        self._frame_proc = WNDPROC(_proc)                 
        set_ptr(hwnd, GWLP_WNDPROC, ctypes.cast(self._frame_proc, ctypes.c_void_p))
        _dwm_set_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)   
        return True

    def _frame_state(self) -> dict:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = self._window_hwnd()
        return {"success": True, "maximized": bool(user32.IsZoomed(ctypes.c_void_p(hwnd))),
                "active": (user32.GetForegroundWindow() or 0) == hwnd}

    def _frame_geometry(self, hwnd):
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")          
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = ctypes.c_void_p
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
        wr, cr, pt = wintypes.RECT(), wintypes.RECT(), wintypes.POINT(0, 0)
        u.GetWindowRect(hwnd, ctypes.byref(wr))
        u.GetClientRect(hwnd, ctypes.byref(cr))
        u.ClientToScreen(hwnd, ctypes.byref(pt))
        kids = []
        Enum = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(child, _lp):
            try:
                if (u.GetParent(child) or 0) == hwnd and u.IsWindowVisible(child):
                    r = wintypes.RECT()
                    u.GetWindowRect(child, ctypes.byref(r))
                    kids.append((int(child), r.left, r.top, r.right, r.bottom))
            except Exception:
                pass
            return True

        cb = Enum(_cb)
        u.EnumChildWindows(hwnd, ctypes.cast(cb, ctypes.c_void_p), 0)
        return {"window": (wr.left, wr.top, wr.right, wr.bottom), "client_origin": (pt.x, pt.y),
                "client_size": (cr.right, cr.bottom), "maximized": bool(u.IsZoomed(hwnd)), "children": kids}

    def _fit_children(self, hwnd) -> bool:
        """Make sure the web view fills the whole client area (it must start at the client's top-left)."""
        import ctypes
        from ctypes import wintypes
        g = self._frame_geometry(hwnd)
        ox, oy = g["client_origin"]
        cw, ch = g["client_size"]
        u = ctypes.WinDLL("user32")
        u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
        moved = False
        for kid, left, top, right, bottom in g["children"]:
            if (right - left) < cw * 0.5:
                continue
            if left != ox or top != oy or (right - left) != cw or (bottom - top) != ch:
                u.MoveWindow(kid, 0, 0, cw, ch, True)
                moved = True
        return moved

    def _disable_frame_hook(self, hwnd):
        import ctypes
        self._frame_disabled = True
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0, SWP_FLAGS)  

    def _verify_frame(self):
        """After the caption is removed: log what the window looks like, fix the web view if it is
        misplaced, and if the page still wouldn't start at the top, go back to the normal title bar
        so the top bar is never cut off."""
        try:
            hwnd = self._window_hwnd()
            if not hwnd or getattr(self, "_frame_disabled", False):
                return
            g = self._frame_geometry(hwnd)
            logging.info("custom title bar geometry: %s", g)
            if g["maximized"]:
                return
            if self._fit_children(hwnd):
                g = self._frame_geometry(hwnd)
                logging.info("custom title bar: web view was misplaced, resized it. now: %s", g)
            if g["client_origin"][1] != g["window"][1]:
                logging.warning("custom title bar: client area doesn't start at the window top, using the native bar")
                self._disable_frame_hook(hwnd)
                self.emit("customTitlebarOff")
        except Exception:
            log_exception("_verify_frame")

    def enable_custom_titlebar(self):
        """index.html calls this once at startup. Until it succeeds the native bar stays."""
        if sys.platform != "win32" or self._window is None or getattr(self, "_frame_disabled", False):
            return {"success": False}
        try:
            if settings_store.read().get("native_titlebar"):      
                return {"success": False}
            if not self._install_frame_hook():
                return {"success": False}
            threading.Timer(0.7, self._verify_frame).start()
            return self._frame_state()
        except Exception:
            log_exception("enable_custom_titlebar")
            return {"success": False}

    def get_window_state(self):
        try:
            return self._frame_state()
        except Exception:
            return {"success": False}

    def window_control(self, action):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(self._window_hwnd())
            if action == "minimize":
                user32.ShowWindow(hwnd, SW_MINIMIZE)
            elif action in ("maximize", "restore"):
                user32.ShowWindow(hwnd, SW_RESTORE if user32.IsZoomed(hwnd) else SW_MAXIMIZE)
            elif action == "close":
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)     
            else:
                return {"success": False}
            return {"success": True}
        except Exception:
            log_exception("window_control")
            return {"success": False}

    def window_drag(self):
        """Hand the drag to Windows itself, so snapping, maximize-on-drag and restore-on-drag all work.

        The web view owns the mouse capture, and only the window's own UI thread can take it over,
        so the move is started on that thread (not on the js-api worker thread)."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):   
                return {"success": False}
            hwnd = self._window_hwnd()
            if not hwnd:
                return {"success": False}
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)

            def _start():
                try:
                    user32.ReleaseCapture()
                    user32.SendMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, lparam)
                except Exception:
                    log_exception("window_drag (ui thread)")

            try:
                from System import Action
                self._window.native.BeginInvoke(Action(_start))   
            except Exception:
                log_exception("window_drag: UI-thread invoke unavailable, falling back")
                user32.ReleaseCapture()
                user32.PostMessageW(ctypes.c_void_p(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, lparam)
            return {"success": True}
        except Exception:
            log_exception("window_drag")
            return {"success": False}
