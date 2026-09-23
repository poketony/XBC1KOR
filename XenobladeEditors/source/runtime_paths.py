"""Writable portable settings and shared assets outside the frozen runtime."""
from pathlib import Path
import sys

APP_ROOT=Path(sys.executable).resolve().parent.parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
SETTINGS_DIR=APP_ROOT/'settings'
ARTWORK_DIR=APP_ROOT/'resources/artwork'
SETTINGS_DIR.mkdir(parents=True,exist_ok=True)
