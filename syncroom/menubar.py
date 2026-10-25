"""SyncRoom as a Mac menu-bar app: Start finds every speaker and plays Spotify on all of them.

The hub does the work; this is only the menu. Menu callbacks run on the main thread, so anything
slow (starting, scanning, calibrating, stopping) runs on a worker thread and the menu catches up
from a 1 s timer."""

import contextlib
import json
import os
import plistlib
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import rumps

from .cli import DEFAULT_CAPTURE
from .config import LOG_FILE
from .hub import Hub
from .logs import log
from .source import CaptureProc

LOGIN_AGENT = Path.home() / "Library" / "LaunchAgents" / "local.syncroom.plist"
SOUND_PANE = "x-apple.systempreferences:com.apple.Sound-Settings.extension"
ICONS = {  # SF Symbols, drawn as template images so they follow the menu bar's colour
    "stopped": "hifispeaker",
    "busy": "ellipsis.circle",
    "running": "hifispeaker.2.fill",
    "calibrating": "waveform",
    "problem": "exclamationmark.triangle",
}


def _bundle_path():
    """Path of SyncRoom.app when running from it, else None (e.g. from a terminal)."""
    try:
        from Foundation import NSBundle

        p = NSBundle.mainBundle().bundlePath()
        return p if p.endswith(".app") else None
    except Exception:  # noqa: BLE001
        return None


def _clear(item):
    """Empty a submenu (rumps' clear() fails on one that has never had items)."""
    if getattr(item, "_menu", None) is not None:
        item.clear()


def _notify(title, message):
    # needs an app bundle; the menu shows the same thing anyway
    with contextlib.suppress(Exception):
        rumps.notification("SyncRoom", title, message, sound=False)


class SyncRoomApp(rumps.App):
    def __init__(self, cfg_path):
        super().__init__("SyncRoom", title="♫", quit_button=None)
        self.cfg_path = cfg_path
        self.hub = None
        self.phase = "stopped"  # stopped / starting / scanning / running / stopping
        self.error = None
        self.icon_name = None
        self.speaker_sig = self.input_sig = None

        self.status = rumps.MenuItem("Stopped")
        self.warning = rumps.MenuItem("", callback=self.on_warning)
        self.toggle = rumps.MenuItem("Start", callback=self.on_toggle, key="s")
        self.speakers = rumps.MenuItem("Speakers")
        self.inputs = rumps.MenuItem("Music input")
        self.calibrate = rumps.MenuItem("Calibrate all speakers…", callback=self.on_calibrate)
        self.beep = rumps.MenuItem("Test beep", callback=self.on_beep)
        self.page = rumps.MenuItem("Open control page", callback=self.on_page, key="o")
        self.autostart = rumps.MenuItem("Start when SyncRoom opens", callback=self.on_autostart)
        self.login = rumps.MenuItem("Open SyncRoom at login", callback=self.on_login)
        self.menu = [
            self.status,
            self.warning,
            None,
            self.toggle,
            self.speakers,
            self.inputs,
            self.calibrate,
            self.beep,
            None,
            self.page,
            self.autostart,
            self.login,
            rumps.MenuItem("Show log", callback=self.on_log),
            None,
            rumps.MenuItem("Quit SyncRoom", callback=self.on_quit, key="q"),
        ]
        self.warning.hide()
        self.autostart.state = self._cfg().get("start_on_open", True)
        self.login.state = LOGIN_AGENT.exists()
        if _bundle_path() is None:
            self.login.title = "Open SyncRoom at login (only from SyncRoom.app)"
            self.login.set_callback(None)
        self.timer = rumps.Timer(self.refresh, 1)
        self.timer.start()
        if self.autostart.state:
            self.start()

    # ---- config while the hub isn't running (it owns the file while it is)
    def _cfg(self):
        try:
            with open(self.cfg_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _set_cfg(self, **kw):
        if self.hub is not None:
            self.hub.cfg.update(kw)
            self.hub.save()
            return
        cfg = self._cfg()
        cfg.update(kw)
        with open(self.cfg_path, "w") as f:
            json.dump(cfg, f, indent=2)

    def _capture(self):
        cap = self._cfg().get("capture") or DEFAULT_CAPTURE
        return DEFAULT_CAPTURE if cap.startswith("app:") else cap  # old ScreenCaptureKit setting

    def _bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ---- start / stop
    def start(self):
        if self.phase != "stopped":
            return
        self.phase, self.error = "starting", None
        self._bg(self._start)

    def _start(self):
        hub = None
        try:
            cap = CaptureProc(self._capture())
            hub = Hub(self.cfg_path, cap, live=True)
            hub.start()
            self.hub, self.phase = hub, "scanning"
            added = hub.auto_setup()
            self.phase = "running"
            if added:
                self._announce(hub, added)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if isinstance(e, OSError) and e.errno == 48:
                msg = "port 8765 is busy — is SyncRoom already running in a terminal?"
            elif "BlackHole" in msg or "No input device" in msg:
                msg = f"{self._capture()} not found — install it: brew install blackhole-2ch"
            log(f"couldn't start: {msg}")
            self.error = msg
            if hub is not None:
                hub.stop()
            self.hub, self.phase = None, "stopped"

    def _announce(self, hub, added):
        off = [n for n in added if n not in hub.outputs]
        on = [n for n in added if n in hub.outputs]
        if on:
            _notify(f"Playing on {', '.join(on)} too", "Calibrate so it plays in time.")
        if off:
            _notify(
                f"Found {', '.join(off)}",
                "Switch it on under Speakers in the SyncRoom menu (then calibrate).",
            )

    def stop(self):
        if self.phase in ("running", "scanning") and self.hub is not None:
            self.phase = "stopping"
            self._bg(self._stop)

    def _stop(self):
        hub, self.hub = self.hub, None
        if hub is not None:
            hub.stop()
        self.phase = "stopped"

    # ---- menu actions
    def on_toggle(self, _):
        if self.phase == "stopped":
            self.start()
        else:
            self.stop()

    def on_calibrate(self, _):
        hub = self.hub
        if hub is None or hub.calibrating:
            return
        ok = rumps.alert(
            "Calibrate all speakers?",
            "Put the Mac where you usually listen and keep the room quiet. The music pauses "
            "while each speaker plays a few chirps (about 20 s per speaker).",
            ok="Calibrate",
            cancel="Cancel",
        )
        if ok == 1:
            self._bg(self._calibrate, hub)

    def _calibrate(self, hub):
        hub.calibrate(None)
        res = hub.cal.get("results", {})
        bad = [n for n, r in res.items() if not r.get("ok")]
        if res:
            _notify(
                "Calibration finished" if not bad else f"Couldn't hear {', '.join(bad)}",
                f"{len(res) - len(bad)} of {len(res)} speakers measured."
                + (" Move the Mac closer or turn them up, then try again." if bad else ""),
            )

    def on_beep(self, _):
        if self.hub is not None:
            self.hub.sync_test(None)

    def on_page(self, _):
        if self.hub is not None:
            webbrowser.open(f"http://localhost:{self.hub.port}/")

    def on_warning(self, item):
        if "BlackHole" in item.title:
            subprocess.run(["open", SOUND_PANE])
        elif "Calibrate" in item.title:
            self.on_calibrate(item)

    def on_speaker(self, item):
        hub, name = self.hub, item.speaker
        if hub is not None and not hub.calibrating:
            self._bg(hub.set_enabled, name, not item.state)

    def on_find(self, _):
        hub = self.hub
        if hub is None or self.phase != "running":
            return

        def find():
            self.phase = "scanning"
            added = hub.auto_setup()
            self.phase = "running"
            if added:
                self._announce(hub, added)
            else:
                _notify("No new speakers found", "")

        self._bg(find)

    def on_input(self, item):
        dev = item.device
        if self.hub is not None:
            try:
                self.hub.set_capture(dev)
            except Exception as e:  # noqa: BLE001
                rumps.alert("Couldn't switch the music input", str(e))
        else:
            self._set_cfg(capture=dev)
        self.input_sig = None

    def on_autostart(self, item):
        item.state = not item.state
        self._set_cfg(start_on_open=bool(item.state))

    def on_login(self, item):
        if item.state:
            LOGIN_AGENT.unlink(missing_ok=True)
        else:
            LOGIN_AGENT.parent.mkdir(parents=True, exist_ok=True)
            with open(LOGIN_AGENT, "wb") as f:
                plistlib.dump(
                    {
                        "Label": "local.syncroom",
                        "ProgramArguments": ["/usr/bin/open", "-a", _bundle_path()],
                        "RunAtLoad": True,
                    },
                    f,
                )
        item.state = LOGIN_AGENT.exists()

    def on_log(self, _):
        subprocess.run(["open", str(LOG_FILE)])

    def on_quit(self, _):
        self.title = "…"
        if self.hub is not None:
            self.hub.stop()  # tell the Sonos / Google Homes to stop before we go
        rumps.quit_application()

    # ---- keep the menu up to date
    def refresh(self, _=None):
        hub, phase = self.hub, self.phase
        if hub is not None and hub.calibrating:
            cal = hub.cal
            step = f" ({cal.get('i', 0) + 1}/{cal['n']})" if cal.get("n") else ""
            self.status.title = f"Calibrating{step}: {cal.get('phase', '…')}"
            icon = "calibrating"
        elif phase == "running" and hub is not None:
            outs = hub.outputs
            on = sum(o.connected for o in outs.values())
            self.status.title = (
                f"Playing on {on} of {len(outs)} speakers · {hub.global_delay():.1f} s behind"
                if outs
                else "No speakers yet — use Speakers ▸ Find speakers"
            )
            icon = "running"
        else:
            self.status.title = {
                "starting": "Starting…",
                "scanning": "Looking for speakers…",
                "stopping": "Stopping…",
            }.get(phase, "Stopped")
            icon = "busy" if phase in ("starting", "scanning", "stopping") else "stopped"
        warn = self.error
        if warn:
            icon = "problem"
        elif hub is not None and phase == "running" and not hub.calibrating:
            todo = [n for n in hub.outputs if n not in hub.cfg["latency"] or hub.outputs[n].stale]
            if todo:
                warn = f"Calibrate {', '.join(todo)} so they play in time"
        self.warning.title = f"⚠ {warn}" if warn else ""
        if warn:
            self.warning.show()
        else:
            self.warning.hide()
        self.toggle.title = "Stop" if phase in ("running", "scanning") else "Start"
        ready = phase == "running" and hub is not None and not hub.calibrating and hub.outputs
        self.calibrate.set_callback(self.on_calibrate if ready else None)
        self.beep.set_callback(self.on_beep if ready else None)
        self.page.set_callback(self.on_page if hub is not None else None)
        self._refresh_speakers(hub)
        self._refresh_inputs(hub)
        self._set_icon(icon)

    def _refresh_speakers(self, hub):
        rows = []
        if hub is not None:
            for c in hub.cfg["outputs"]:
                n, o = c["name"], hub.outputs.get(c["name"])
                if o is None:
                    rows.append((n, False, f"{n} — off"))
                    continue
                st = "connected" if o.connected else o.status
                lat = hub.latency(n)
                note = (
                    " · recalibrate"
                    if o.stale
                    else f" · {lat:.1f} s"
                    if n in hub.cfg["latency"]
                    else " · not calibrated"
                )
                rows.append((n, True, f"{n} — {st}{note}"))
        sig = (tuple(rows), self.phase)
        if sig == self.speaker_sig:
            return
        self.speaker_sig = sig
        _clear(self.speakers)
        if hub is None:
            self.speakers.add(rumps.MenuItem("Start SyncRoom to see your speakers"))
            return
        for n, on, label in rows:
            item = rumps.MenuItem(label, callback=self.on_speaker)
            item.speaker, item.state = n, on
            self.speakers.add(item)
        self.speakers.add(None)
        self.speakers.add(
            rumps.MenuItem(
                "Find speakers", callback=self.on_find if self.phase == "running" else None
            )
        )

    def _refresh_inputs(self, hub):
        cur = getattr(hub.src.cmd, "device", None) if hub else self._capture()
        choices = [cur]
        if hub is not None:
            choices += [
                d for d in hub.devices().get("inputs", []) if "BlackHole" in d and d != cur
            ]
        sig = (cur, tuple(choices))
        if sig == self.input_sig:
            return
        self.input_sig = sig
        _clear(self.inputs)
        for d in choices:
            item = rumps.MenuItem(d, callback=self.on_input)
            item.device, item.state = d, d == cur
            self.inputs.add(item)

    def _set_icon(self, name):
        if name == self.icon_name:
            return
        try:
            from AppKit import NSImage

            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                ICONS[name], "SyncRoom"
            )
            img.setTemplate_(True)
            button = self._nsapp.nsstatusitem.button()
            button.setImage_(img)
            button.setTitle_("")
            self.icon_name = name
        except Exception:  # noqa: BLE001  (falls back to the ♫ title)
            pass


def run_menubar(cfg_path):
    if not sys.stdout.isatty():  # launched as an app: keep a log file instead of a terminal
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5_000_000:
            os.replace(LOG_FILE, LOG_FILE.with_suffix(".old.log"))
        sys.stdout = sys.stderr = open(LOG_FILE, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    log(f"SyncRoom menu bar starting (config {cfg_path})")
    SyncRoomApp(cfg_path).run()
