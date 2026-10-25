"""Where SyncRoom keeps its config and log (shared by the menu-bar app and the command line)."""

import json
import shutil
import sys
from pathlib import Path

if sys.platform == "darwin":
    APP_DIR = Path.home() / "Library" / "Application Support" / "SyncRoom"
    LOG_FILE = Path.home() / "Library" / "Logs" / "SyncRoom.log"
else:
    APP_DIR = Path.home() / ".config" / "syncroom"
    LOG_FILE = APP_DIR / "syncroom.log"
CONFIG = APP_DIR / "syncroom.json"
# where the config used to live: next to the code, in the project folder
_PROJECT_CONFIG = Path(__file__).resolve().parent.parent / "syncroom.json"
# left over from the librespot days
OBSOLETE_KEYS = ("spotify_name", "librespot_args")


def default_config_path():
    """The shared config, created on first use (seeded from the project folder's syncroom.json)."""
    if not CONFIG.exists():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if _PROJECT_CONFIG.exists():
            shutil.copy(_PROJECT_CONFIG, CONFIG)
        else:
            CONFIG.write_text(json.dumps({"outputs": []}, indent=2))
    return str(CONFIG)
