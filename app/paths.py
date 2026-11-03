"""Lokasi file. Data user (database, token, log) disimpan di folder data, bukan di samping .exe."""

import os
import sys
from functools import cache
from pathlib import Path


def resource_dir() -> Path:
    """Folder berisi web/ (juga saat dibungkus PyInstaller)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


@cache
def data_dir() -> Path:
    override = os.environ.get("DUCKLIVE_DATA")
    if override:
        p = Path(override)
    elif os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home()))
        p = _migrate(root / "SubathonTimer", root / "DuckLive")
    else:
        root = Path.home() / ".local" / "share"
        p = _migrate(root / "subathon-timer", root / "ducklive")
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return _migrate(data_dir() / "subathon.db", data_dir() / "ducklive.db")


def _migrate(legacy: Path, new: Path) -> Path:
    """Pindahkan data dari nama lama (Subathon Timer). Jika gagal (mis. sedang dipakai), pakai yang lama."""
    if legacy.exists() and not new.exists():
        try:
            legacy.rename(new)
        except OSError:
            return legacy
    return new
