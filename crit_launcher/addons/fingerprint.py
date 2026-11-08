"""File hashes and CurseForge fingerprints for identifying mods."""

import struct
from pathlib import Path

from ..core.fsutil import file_sha1


_MOD_HASH_CACHE = {}


def _mod_hash(path: Path) -> str:
    """SHA-1 of a mod file, remembered until the file's size or modification time changes."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    digest = _MOD_HASH_CACHE.get(key)
    if digest is None:
        digest = _MOD_HASH_CACHE[key] = file_sha1(path)
    return digest


_CF_FP_CACHE = {}


def _cf_fingerprint(data: bytes) -> int:
    """CurseForge's file fingerprint: MurmurHash2 (32-bit, seed 1) over the file with the
    whitespace bytes 9, 10, 13 and 32 removed."""
    data = data.translate(None, b"\t\n\r ")
    m, length = 0x5BD1E995, len(data)
    h = (1 ^ length) & 0xFFFFFFFF
    end = length - (length % 4)
    for (k,) in struct.iter_unpack("<I", data[:end]):
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> 24
        k = (k * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
    tail = length - end
    if tail >= 3:
        h ^= data[end + 2] << 16
    if tail >= 2:
        h ^= data[end + 1] << 8
    if tail >= 1:
        h ^= data[end]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


def _mod_fingerprint(path: Path) -> int:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    fp = _CF_FP_CACHE.get(key)
    if fp is None:
        fp = _CF_FP_CACHE[key] = _cf_fingerprint(path.read_bytes())
    return fp
