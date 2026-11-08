"""Playtime bookkeeping."""

import time

from ..config.settings import settings_store


def _bump_playtime(s: dict, elapsed: int):
    s["playtime_seconds"] = int(s.get("playtime_seconds", 0)) + elapsed
    days = s.get("playtime_days")
    days = dict(days) if isinstance(days, dict) else {}
    key = time.strftime("%Y-%m-%d")
    days[key] = int(days.get(key, 0)) + elapsed
    s["playtime_days"] = {k: days[k] for k in sorted(days)[-30:]}


def _playtime_days() -> dict:
    raw = settings_store.read().get("playtime_days")
    if not isinstance(raw, dict):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}
