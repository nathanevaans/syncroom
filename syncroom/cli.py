"""Command line entry point: `syncroom discover` / `syncroom run`."""

import argparse
import json
import shutil
import sys

from .constants import SR
from .discovery import discover
from .hub import Hub
from .source import CaptureProc


def main():
    ap = argparse.ArgumentParser(prog="syncroom", description="Synchronized multi-speaker Spotify")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    r = sub.add_parser("run")
    r.add_argument("--config", default="syncroom.json")
    r.add_argument("--file", help="play this audio file (looped) instead of Spotify, for testing")
    r.add_argument("--calibrate", action="store_true", help="calibrate on start-up")
    r.add_argument("--librespot", default="librespot")
    r.add_argument(
        "--capture",
        metavar="DEVICE",
        help="capture this audio input instead of using librespot, e.g. 'BlackHole 2ch' "
        "(play Spotify in the normal app with its output set to that device)",
    )
    a = ap.parse_args()

    if a.cmd == "discover":
        return discover()

    with open(a.config) as f:
        cfg = json.load(f)
    capture = a.capture or cfg.get("capture")
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
    elif capture:
        cmd = CaptureProc(capture)
    else:
        if not shutil.which(a.librespot):
            sys.exit(
                "librespot not found. Install it: `cargo install librespot` "
                "(or your package manager)."
            )
        cmd = [
            a.librespot,
            "--name",
            cfg.get("spotify_name", "SyncRoom"),
            "--backend",
            "pipe",
            "--format",
            "S16",
            "--bitrate",
            "320",
            "--device-type",
            "speaker",
            "--initial-volume",
            "70",
            *cfg.get("librespot_args", []),
        ]
    Hub(a.config, cmd, live=bool(capture) and not a.file).run(calibrate_first=a.calibrate)
