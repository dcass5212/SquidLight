"""Logging to %APPDATA%\\RGBApp\\logs\\app.log with rotation, plus the console."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .paths import log_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, console: bool = True) -> None:
    """Idempotent: safe to call more than once."""
    root = logging.getLogger()
    if getattr(root, "_rgbapp_configured", False):
        root.setLevel(level)
        return

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    root._rgbapp_configured = True  # type: ignore[attr-defined]
