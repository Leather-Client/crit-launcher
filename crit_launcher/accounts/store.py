"""The accounts file and account lookup."""

from ..config.paths import ACCOUNTS_FILE
from ..core.jsonstore import JsonStore


accounts_store = JsonStore(ACCOUNTS_FILE, [], sensitive=True)


def find_account(accounts: list, key):
    if not key:
        return None
    return (next((a for a in accounts if a.get("uuid") == key), None)
            or next((a for a in accounts if a.get("name") == key), None))
