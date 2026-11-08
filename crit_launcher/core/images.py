"""PNG helpers shared by skins and instance icons."""

import base64
import re
import struct


PNG_SIG = b"\x89PNG\r\n\x1a\n"


def decode_png_uri(uri: str) -> bytes:
    m = re.match(r"^data:image/png;base64,([A-Za-z0-9+/=\s]+)$", uri or "")
    if not m:
        raise ValueError("That isn't a PNG image.")
    try:
        data = base64.b64decode(m.group(1))
    except Exception:
        raise ValueError("That image couldn't be read.")
    if not data.startswith(PNG_SIG) or len(data) < 33:
        raise ValueError("That isn't a PNG image.")
    if len(data) > 200_000:
        raise ValueError("That file is too big to be a skin.")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) not in ((64, 64), (64, 32)):
        raise ValueError("Skins must be 64x64 (or 64x32) pixels.")
    return data


def png_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
