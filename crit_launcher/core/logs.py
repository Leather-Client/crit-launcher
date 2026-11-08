"""Log file setup and the log_exception helper."""

import logging
import traceback

from ..config.paths import LOG_FILE


def setup_logging() -> None:
    """Send everything to DATA_DIR/launcher.log. The data directory must already exist."""
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def log_exception(context: str):
    logging.error("%s\n%s", context, traceback.format_exc())
