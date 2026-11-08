"""Windows title bar / frame constants and DWM helpers."""

DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19   
DWMWA_USE_IMMERSIVE_DARK_MODE = 20       
DWMWA_BORDER_COLOR = 34                  
DWMWA_CAPTION_COLOR = 35                 
DWMWA_TEXT_COLOR = 36                    
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  
TITLEBAR_FALLBACKS = {
    ("dark", "default"): ("#0b0c0e", "#e8eaed"),
    ("dark", "modrinth"): ("#141518", "#ecf0f3"),
    ("dark", "fastclient"): ("#0b0c0f", "#f1f2f4"),   
    ("dark", "sklauncher"): ("#0a0d14", "#f4f4f5"),
    ("dark", "minecraft"): ("#0b0c0e", "#e8eaed"),
    ("oled", "default"): ("#000000", "#e8eaed"),
    ("oled", "modrinth"): ("#000000", "#ecf0f3"),
    ("light", "default"): ("#f2f4f7", "#1b1f24"),
    ("light", "modrinth"): ("#f2f4f7", "#1b1f24"),
}
import ctypes as _ct


class _RECT(_ct.Structure):
    _fields_ = [("left", _ct.c_long), ("top", _ct.c_long), ("right", _ct.c_long), ("bottom", _ct.c_long)]


class _NCCALCSIZE_PARAMS(_ct.Structure):
    _fields_ = [("rgrc", _RECT * 3), ("lppos", _ct.c_void_p)]


WM_NCCALCSIZE = 0x0083
WM_NCDESTROY = 0x0082
WM_NCLBUTTONDOWN = 0x00A1
WM_CLOSE = 0x0010
HTCAPTION = 2
GWLP_WNDPROC = -4
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9


def _colorref(hex_color: str) -> int:
    h = str(hex_color).strip().lstrip("#")
    if len(h) == 3:                      
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)      


def _dwm_set_attr(hwnd, attr, value):
    import ctypes
    v = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v))
