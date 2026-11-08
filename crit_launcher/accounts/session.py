"""Refreshing an account's tokens before launch."""

import time

from .ely import refresh_ely_account
from .microsoft import refresh_ms_account
from .tokens import _jwt_expired


def ensure_fresh_account(account: dict) -> dict:
    if account.get("type") == "ely":
        if account.get("mc_token") and _jwt_expired(account["mc_token"]) is False:
            return account
        return refresh_ely_account(account)
    if account.get("type") != "microsoft":
        return account
    if account.get("mc_token") and float(account.get("mc_expires", 0)) - time.time() > 60:
        return account
    return refresh_ms_account(account)
