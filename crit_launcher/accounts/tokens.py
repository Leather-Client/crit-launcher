"""JWT expiry and the public view of an account."""

import base64
import json
import time


def _jwt_expired(token):
    """True/False for a readable token, None when it cannot be parsed."""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        claims = json.loads(base64.urlsafe_b64decode(body))
        exp = claims.get("exp")
        return None if exp is None else float(exp) <= time.time() + 60
    except Exception:
        return None


def public_account(a: dict) -> dict:
    out = {"name": a.get("name"), "uuid": a.get("uuid"), "type": a.get("type", "offline")}
    return out
