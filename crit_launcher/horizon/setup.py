"""Full Horizon instance setup."""

import shutil
from pathlib import Path

from ..instances.store import read_instance_meta
from ..minecraft.constants import INSTALL_SCHEMA
from .constants import HORIZON_JAVA_MAJOR
from .game import (
    horizon_ensure_minecraft,
    horizon_ensure_server_jar,
    horizon_find_java,
    horizon_prepare_build,
)
from .layout import horizon_minecraft_version, horizon_resources_dir
from .releases import horizon_install_build
from .sounds import HORIZON_SOUNDS_OK, horizon_copy_sounds


def horizon_setup(name: str, tracker) -> dict:
    """Install and prepare everything one Horizon instance needs. Returns the meta to save."""
    meta = read_instance_meta(name) or {}
    build_version, vdir = horizon_install_build(tracker)
    mc_version = horizon_minecraft_version(vdir)
    resources = horizon_resources_dir(vdir)

    java_path = meta.get("horizon_java")
    if not (java_path and Path(java_path).is_file()):
        java_path = horizon_find_java(tracker)

    if not (resources / "horizon-resources.json").is_file():
        mc_root, vjson = horizon_ensure_minecraft(mc_version, tracker)
        fallback = Path(vdir) / ".local" / "minecraft" / mc_version / "manifest.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(vjson, fallback)
        server_jar = horizon_ensure_server_jar(mc_version, vjson, tracker)
        horizon_prepare_build(vdir, mc_version, mc_root, server_jar, tracker, java_path)

    if not (resources / "horizon-resources.json").is_file():
        raise RuntimeError("Horizon finished preparing but its resources are missing.")

    sounds_ok = (resources / HORIZON_SOUNDS_OK).is_file()
    if not sounds_ok:
        # Also repairs instances that were prepared earlier without sounds. The Minecraft files
        # are already downloaded by now, so this reuses them instead of fetching anything.
        mc_root, _ = horizon_ensure_minecraft(mc_version, tracker)
        sounds_ok = horizon_copy_sounds(vdir, mc_root, tracker, java_path)

    return {
        "horizon_sounds": sounds_ok,
        "version": mc_version,
        "loader": "horizon",
        "loader_version": None,
        "horizon_version": build_version,
        "horizon_dir": str(vdir),
        "horizon_resources": str(resources),
        "horizon_minecraft": mc_version,
        "horizon_java": java_path,
        "horizon_prepared": True,
        "installed": True,
        "schema": INSTALL_SCHEMA,
        "java_component": "horizon",
        "java_major": HORIZON_JAVA_MAJOR,
    }
