"""Copying Horizon's sound assets."""

import logging
import time
from pathlib import Path

from ..core.logs import log_exception
from .game import horizon_run_logged
from .layout import horizon_exe, horizon_resources_dir


HORIZON_SOUNDS_OK = ".crit-sounds-ok"
_HORIZON_SOUNDS_TRIED = set()


def horizon_sounds_pending(resources) -> bool:
    """True when this instance's sounds still need copying and haven't been tried this session."""
    return (not (Path(resources) / HORIZON_SOUNDS_OK).is_file()
            and str(resources) not in _HORIZON_SOUNDS_TRIED)


def horizon_copy_sounds(vdir: Path, mc_root: Path, tracker, java_path) -> bool:
    """Copy the game's sounds (about 380 MB) from the Minecraft files this launcher downloaded.

    horizon-prepare wants the subcommand first: `horizon-prepare sounds --root ...`. With the
    options first it fails with "unrecognized argument: sounds" and the game is silent.
    """
    resources = horizon_resources_dir(vdir)
    _HORIZON_SOUNDS_TRIED.add(str(resources))
    prep = Path(vdir) / horizon_exe("horizon-prepare")
    if not prep.is_file():
        logging.warning("Can't copy Horizon's sounds: %s is missing", prep.name)
        return False
    cmd = [str(prep), "sounds", "--root", str(vdir), "--minecraft-dir", str(mc_root)]
    code, output = horizon_run_logged(cmd, vdir, tracker, "Copying Horizon's sounds", java_path)
    if code != 0:
        logging.warning("horizon-prepare sounds failed (the game will be silent):\n%s", output[-2000:])
        return False
    try:
        (resources / HORIZON_SOUNDS_OK).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        log_exception("write sounds marker")
    return True
