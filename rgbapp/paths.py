"""Locations for user data. Never inside the repo."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """%APPDATA%\\RGBApp (created on demand)."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / "RGBApp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
