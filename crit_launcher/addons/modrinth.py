"""Modrinth API helpers."""

MODRINTH_API = "https://api.modrinth.com/v2"


def pick_best_version(versions: list):
    if not versions:
        return None
    for wanted in ("release", "beta", "alpha"):
        for v in versions:
            if v.get("version_type") == wanted:
                return v
    return versions[0]


def primary_file(version: dict):
    files = version.get("files", [])
    return next((f for f in files if f.get("primary")), files[0] if files else None)
