"""Command line entry point: `syncroom menubar` / `syncroom run` / `syncroom discover`."""

import argparse
import json

from .config import CONFIG, default_config_path
from .constants import SR
from .discovery import discover
from .hub import Hub
from .source import CaptureProc

DEFAULT_CAPTURE = "BlackHole 2ch"


def main():
    ap = argparse.ArgumentParser(prog="syncroom", description="Synchronized multi-speaker Spotify")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="list speakers and audio devices")
    m = sub.add_parser("menubar", help="run as a Mac menu-bar app")
    r = sub.add_parser("run", help="run in this terminal, with the syncroom> console")
    for p in (m, r):
        p.add_argument("--config", help=f"config file (default {CONFIG})")
    r.add_argument("--file", help="play this audio file (looped) instead of Spotify, for testing")
    r.add_argument("--calibrate", action="store_true", help="calibrate on start-up")
    r.add_argument(
        "--capture",
        metavar="INPUT",
        help="the input the music comes from (default 'BlackHole 2ch': set the Mac's sound "
        "output to it)",
    )
    a = ap.parse_args()

    if a.cmd == "discover":
        return discover()
    cfg_path = a.config or default_config_path()
    if a.cmd == "menubar":
        from .menubar import run_menubar

        return run_menubar(cfg_path)

    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    if a.file:
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            a.file,
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            str(SR),
            "-",
        ]
    else:
        cmd = CaptureProc(a.capture or cfg.get("capture") or DEFAULT_CAPTURE)
    Hub(cfg_path, cmd, live=not a.file).run(calibrate_first=a.calibrate)
