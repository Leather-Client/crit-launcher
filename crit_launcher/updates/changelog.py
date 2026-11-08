"""Release notes and version comparison."""

import re

from ..config.paths import DATA_DIR


CHANGELOG_REPO = "snigglaberry/crit-launcher"


def _version_tuple(tag) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", str(tag or ""))]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums) or (0,)


CHANGELOG_CACHE = DATA_DIR / "changelog_cache.json"
_changelog_mem = {"at": 0.0, "value": None}
