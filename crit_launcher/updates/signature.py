"""Signature check for launcher updates."""

import base64
import binascii
import re

from ..core.ed25519 import horizon_ed25519_verify
from ..core.http import http_get_bytes


# Releases are signed with a detached ed25519 signature over SHA256SUMS.
# Empty means the check is skipped (a warning is logged). Fill it in to enforce;
# see the release notes for how to generate the pair.
UPDATE_TRUSTED_KEYS = ()
UPDATE_SUMS = "SHA256SUMS"
UPDATE_SIG = "SHA256SUMS.sig"


def update_signed_sha256(release: dict, asset_name: str) -> str:
    """sha256 for asset_name, but only from a signature-verified SHA256SUMS.

    This is the check that stops a hijacked release from running code: the sums
    list is only trusted when it carries a valid signature from a baked-in key.
    """
    assets = {a.get("name"): a for a in release.get("assets") or []}
    if UPDATE_SUMS not in assets or UPDATE_SIG not in assets:
        raise RuntimeError("This release isn't signed, so it wasn't installed.")
    sums = http_get_bytes(assets[UPDATE_SUMS]["browser_download_url"], limit=1 << 20)
    signature = http_get_bytes(assets[UPDATE_SIG]["browser_download_url"], limit=1 << 20)

    message = ("crit launcher release %s\n" % release.get("tag_name")).encode() + sums
    keys = [base64.b64decode(k) for k in UPDATE_TRUSTED_KEYS]
    signed = False
    for line in signature.decode("utf-8", "replace").splitlines():
        try:
            raw = base64.b64decode(line.strip(), validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 64 and any(horizon_ed25519_verify(k, message, raw) for k in keys):
            signed = True
            break
    if not signed:
        raise RuntimeError("The update's signature didn't match, so it wasn't installed.")

    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(None, 1)
        if (len(parts) == 2 and parts[1].lstrip("*") == asset_name
                and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0])):
            return parts[0].lower()
    raise RuntimeError("The updater isn't in the signed checksums, so it wasn't installed.")
