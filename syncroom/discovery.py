"""Finding speakers on the network and audio devices on this machine."""

import shutil
import subprocess

from .logs import log


def discover():
    """Print ready-to-paste config entries for every speaker found (`syncroom discover`)."""
    print("== Sonos ==")
    try:
        import soco

        for z in sorted(soco.discover(timeout=5) or [], key=lambda z: z.player_name):
            slug = z.player_name.lower().replace(" ", "-")
            print(f'  {{"name": "{slug}", "type": "sonos", "ip": "{z.ip_address}"}}')
    except Exception as e:  # noqa: BLE001
        print("  (failed:", e, ")")
    print("== Google Home / Nest / Chromecast ==")
    try:
        import pychromecast

        casts, browser = pychromecast.get_chromecasts(timeout=8)
        browser.stop_discovery()
        for c in casts:
            ci = getattr(c, "cast_info", None)
            fn = ci.friendly_name if ci else c.name
            model = ci.model_name if ci else ""
            kind = (
                " (speaker GROUP — use the individual speakers instead)"
                if ci and ci.cast_type == "group"
                else ""
            )
            slug = fn.lower().replace(" ", "-")
            print(
                f'  {{"name": "{slug}", "type": "chromecast", "cast_name": "{fn}"}}'
                f"   # {model}{kind}"
            )
    except Exception as e:  # noqa: BLE001
        print("  (failed:", e, ")")
    if shutil.which("pactl"):
        print("== PulseAudio / PipeWire sinks (Bluetooth = bluez_output...) ==")
        out = subprocess.run(
            ["pactl", "list", "short", "sinks"], capture_output=True, text=True
        ).stdout
        for line in out.splitlines():
            sink = line.split("\t")[1]
            print(f'  {{"name": "{sink.split(".")[0][:20]}", "type": "pulse", "sink": "{sink}"}}')
    try:
        import sounddevice as sd

        print("== Audio devices (use for 'local' outputs and 'mic') ==")
        print(sd.query_devices())
    except Exception as e:  # noqa: BLE001
        print("  (sounddevice unavailable:", e, ")")


def scan_network():
    """Sonos and Google Home / Chromecast speakers on the LAN, for the 'Add speakers' panel."""
    found = []
    try:
        import soco

        for z in sorted(soco.discover(timeout=4) or [], key=lambda z: z.player_name):
            found.append({"type": "sonos", "name": z.player_name, "ip": z.ip_address})
    except Exception as e:  # noqa: BLE001
        log(f"scan: Sonos search failed: {e}")
    try:
        import pychromecast

        casts, browser = pychromecast.get_chromecasts(timeout=6)
        browser.stop_discovery()
        for c in casts:
            ci = getattr(c, "cast_info", None)
            if ci and ci.cast_type == "group":
                continue  # groups do their own sync; add the individual speakers instead
            fn = ci.friendly_name if ci else c.name
            found.append(
                {
                    "type": "chromecast",
                    "name": fn,
                    "cast_name": fn,
                    "model": ci.model_name if ci else "",
                }
            )
    except Exception as e:  # noqa: BLE001
        log(f"scan: Google Home / Chromecast search failed: {e}")
    return found
