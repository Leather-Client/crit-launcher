"""Offline account UUIDs."""

import hashlib
import uuid


def offline_uuid(username: str) -> str:
    data = ("OfflinePlayer:" + username).encode("utf-8")
    digest = bytearray(hashlib.md5(data).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))
