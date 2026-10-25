"""Entry point inside SyncRoom.app."""

from syncroom.config import default_config_path
from syncroom.menubar import run_menubar

run_menubar(default_config_path())
