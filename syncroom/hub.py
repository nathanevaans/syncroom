"""The hub: timing model, speakers, config and the state shown on the control page."""

import contextlib
import json
import os
import re
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import numpy as np

from . import discovery
from .calibration import CalibrationMixin
from .config import OBSOLETE_KEYS
from .console import run_console
from .constants import CH, DEFAULT_LAT, SR
from .dsp import make_chirp
from .logs import LOG, log
from .net import lan_ip
from .outputs import make_output
from .source import CaptureProc, Source
from .web import make_handler

# the config field that identifies each kind of speaker
SPEAKER_KEY = {"sonos": "ip", "chromecast": "cast_name", "local": "device", "http": None}


class Hub(CalibrationMixin):
    def __init__(self, cfg_path, src_cmd, live=False):
        self.cfg_path = cfg_path
        try:
            with open(cfg_path) as f:
                self.cfg = json.load(f)
        except FileNotFoundError:
            self.cfg = {}
        for k in ("latency", "trim_ms", "gain", "measured_at"):
            self.cfg.setdefault(k, {})
        self.cfg.setdefault("outputs", [])
        self.cfg.setdefault("ignored", [])  # speakers removed by hand: auto-setup leaves them out
        for k in OBSOLETE_KEYS:
            self.cfg.pop(k, None)
        self.port = self.cfg.get("http_port", 8765)
        self.host = self.cfg.get("host_ip") or lan_ip()
        self.src = Source(src_cmd, live=live)
        self.outputs = {
            o["name"]: make_output(self, o) for o in self.cfg["outputs"] if o.get("enabled", True)
        }
        self.calibrating = False
        self.cal_cancel = False
        self.cal = {}  # live calibration progress for the control page
        self.last_cal = {}
        self.slock = threading.Lock()  # speaker add/remove/on/off
        self.srv = None

    # ---- timing model
    def latency(self, name):
        o = self.outputs.get(name)
        return self.cfg["latency"].get(name, DEFAULT_LAT[o.type if o else "http"])

    def global_delay(self):
        return max((self.latency(n) for n in self.outputs), default=0.0) + 0.05

    def suggested_delay(self, name):
        """Added delay that lines this speaker up with the slowest one (before any fine-tuning)."""
        return max(0.0, self.global_delay() - self.latency(name))

    def offset_of(self, name):
        return max(0.0, self.suggested_delay(name) + self.cfg["trim_ms"].get(name, 0) / 1000)

    def set_added_delay(self, name, ms):
        """Pin a speaker's added delay to exactly `ms` by choosing the matching trim."""
        base = self.suggested_delay(name) * 1000
        self.cfg["trim_ms"][name] = round(max(0.0, float(ms)) - base, 1)
        self.save()

    def save(self):
        tmp = self.cfg_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.cfg, f, indent=2)
        os.replace(tmp, self.cfg_path)

    # ---- speakers (managed from the control page)
    def out_cfg(self, name):
        return next((c for c in self.cfg["outputs"] if c["name"] == name), None)

    def _sync_outputs(self):
        """Make the running outputs match the config: start new/enabled ones, stop disabled or
        removed ones. The dict is replaced, never mutated, so threads iterating the old one are
        safe."""
        cur, new = self.outputs, {}
        for c in self.cfg["outputs"]:
            if c.get("enabled", True):
                new[c["name"]] = cur.get(c["name"]) or make_output(self, c)
        self.outputs = new
        for n, o in cur.items():
            if n not in new:
                o.stop()
                log(f"[{n}] switched off")
        for n, o in new.items():
            if n not in cur:
                log(f"[{n}] switched on")
                o.start()

    def set_enabled(self, name, on):
        with self.slock:
            self.out_cfg(name)["enabled"] = bool(on)
            self._sync_outputs()
            self.save()

    def add_speaker(self, spec):
        t = spec.get("type")
        if t not in SPEAKER_KEY:
            raise ValueError(f"unknown speaker type {t!r}")
        k = SPEAKER_KEY[t]
        if k and not spec.get(k):
            raise ValueError(f"{t} speaker needs '{k}'")
        with self.slock:
            for c in self.cfg["outputs"]:
                if k and c["type"] == t and c.get(k) == spec[k]:
                    raise ValueError(f"already added as '{c['name']}'")
            base = (
                re.sub(r"[^a-z0-9]+", "-", (spec.get("name") or spec.get(k) or t).lower()).strip(
                    "-"
                )
                or t
            )
            name, i = base, 2
            while self.out_cfg(name):
                name, i = f"{base}-{i}", i + 1
            c = {"name": name, "type": t}
            if k:
                c[k] = spec[k]
            if spec.get("uid"):
                c["uid"] = spec["uid"]  # Sonos: lets auto-setup follow it to a new IP
            if spec.get("enabled") is False:
                c["enabled"] = False
            self.cfg["outputs"].append(c)
            self._sync_outputs()
            self.save()
        log(f"[{name}] added ({t})")
        return name

    def remove_speaker(self, name):
        with self.slock:
            c = self.out_cfg(name)
            if c is not None:
                self.cfg["ignored"] = sorted(set(self.cfg["ignored"]) | self._idents(c))
            self.cfg["outputs"] = [c for c in self.cfg["outputs"] if c["name"] != name]
            for k in ("latency", "trim_ms", "gain", "measured_at"):
                self.cfg[k].pop(name, None)
            self._sync_outputs()
            self.save()
        log(f"[{name}] removed")

    @staticmethod
    def _idents(c):
        """Ways of recognising a speaker in a scan, e.g. {"chromecast:Kitchen speaker"}."""
        t, k = c["type"], SPEAKER_KEY.get(c["type"])
        return {f"{t}:{c[f]}" for f in (k, "uid") if f and c.get(f)}

    def auto_setup(self):
        """Find every speaker and fold it into the config: follow a Sonos whose IP changed, add
        Bluetooth speakers connected to this Mac (switched on), and add new Sonos / Google Home
        speakers switched *off* — they may be someone else's — except ones removed by hand
        before. Returns the names added."""
        found = discovery.scan_network() + discovery.bluetooth_speakers()
        added = []
        for f in found:
            if self.calibrating:
                break
            t = f["type"]
            with self.slock:
                known = [c for c in self.cfg["outputs"] if c["type"] == t]
                if t == "sonos":
                    c = next(
                        (c for c in known if c.get("uid") == f["uid"] or c.get("ip") == f["ip"]),
                        None,
                    )
                    if c is not None:
                        old = c["ip"]
                        if (c.get("uid"), old) != (f["uid"], f["ip"]):
                            c["uid"], c["ip"] = f["uid"], f["ip"]
                            self.save()
                        if old != f["ip"] and c["name"] in self.outputs:
                            log(f"[{c['name']}] Sonos moved from {old} to {f['ip']}")
                            self.outputs[c["name"]].start()
                        continue
                elif any(c.get(SPEAKER_KEY[t]) == f[SPEAKER_KEY[t]] for c in known):
                    continue
                if self._idents(f) & set(self.cfg["ignored"]):
                    continue
            if t != "local":
                f["enabled"] = False
            try:
                added.append(self.add_speaker(f))
            except ValueError as e:
                log(f"auto-setup: couldn't add {f.get('name')}: {e}")
        log(
            f"auto-setup: {len(found)} speakers found"
            + (f", added {', '.join(added)}" if added else ", nothing new")
        )
        return added

    def reconnect(self, name):
        o = self.outputs[name]
        log(f"[{name}] reconnect requested")
        o.start()

    # ---- audio devices on this machine
    def devices(self):
        try:
            import sounddevice as sd

            devs = sd.query_devices()
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "inputs": [], "outputs": []}
        cur = self.src.cmd
        inputs = sorted({d["name"] for d in devs if d["max_input_channels"] > 0})
        return {
            "inputs": inputs,
            "outputs": sorted({d["name"] for d in devs if d["max_output_channels"] > 0}),
            "mic": self.cfg.get("mic"),
            "capture": cur.device if isinstance(cur, CaptureProc) else None,
            "capture_mode": isinstance(cur, CaptureProc),
        }

    def set_mic(self, dev):
        self.cfg["mic"] = dev or None
        self.save()
        log(f"calibration microphone: {dev or 'system default'}")

    def set_capture(self, dev):
        """Swap the music input (BlackHole) without restarting."""
        if not isinstance(self.src.cmd, CaptureProc):
            raise ValueError("SyncRoom is playing a test file, so there's no input to switch")
        old = self.src.proc
        new = CaptureProc(dev)
        self.src.cmd = self.src.proc = new
        if old is not None:
            old.terminate()
        self.cfg["capture"] = dev
        self.save()

    def discover(self):
        """Speakers on the network + audio devices here, for the 'Add speakers' panel."""
        found = discovery.scan_network()
        skip = {self.cfg.get("mic"), getattr(self.src.cmd, "device", None)}
        for d in self.devices()["outputs"]:
            if d not in skip:
                found.append({"type": "local", "name": d, "device": d})
        for f in found:
            k = SPEAKER_KEY[f["type"]]
            f["added"] = next(
                (
                    c["name"]
                    for c in self.cfg["outputs"]
                    if c["type"] == f["type"] and c.get(k) == f[k]
                ),
                None,
            )
        return found

    def record_source(self, seconds=10):
        import wave

        print(f"recording {seconds} s of exactly what SyncRoom is capturing…")
        time.sleep(seconds)
        end = self.src.now()
        x = self.src.read(end - seconds * SR, end) if not self.src.muted else None
        if x is None:
            print("music is muted (calibrating) — try again in a moment")
            return
        fn = os.path.abspath(f"syncroom-capture-{time.strftime('%H%M%S')}.wav")
        with wave.open(fn, "wb") as w:
            w.setnchannels(CH)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(x.tobytes())
        print(f"saved {fn} — listen on headphones: clean here = problem is after capture")

    def src_level(self):
        x = self.src.read(self.src.now() - SR // 2, self.src.now()).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)))
        return 20 * np.log10(rms / 32768) if rms > 0 else -120.0

    def sync_test(self, names=None):
        """Four clicks on the chosen speakers (default all) at the same moment: you should hear one
        click per beat, not a flam."""
        outs = [o for n, o in self.outputs.items() if names is None or n in names]
        if not outs:
            return
        click = make_chirp(SR, dur=0.03, f0=1500, f1=5000) * 0.8
        offs = [o.off_frames() for o in outs]
        f = self.src.now() + int(0.4 * SR) + max(-x for x in offs)
        for i in range(4):
            for o in outs:
                o.add_overlay(f + i * SR // 2, click)
        log(f"test beep: {', '.join(o.name for o in outs)}")

    def state(self):
        gd = self.global_delay()
        slowest = max(self.outputs, key=self.latency, default=None)
        speakers = []
        for c in self.cfg["outputs"]:
            n = c["name"]
            o = self.outputs.get(n)
            s = {
                "name": n,
                "type": c["type"],
                "enabled": o is not None,
                "where": c.get("ip")
                or c.get("cast_name")
                or c.get("device")
                or c.get("sink")
                or "",
            }
            if o is not None:
                s.update(
                    {
                        "status": o.status,
                        "connected": o.connected,
                        "clients": o.clients,
                        "paused": o.paused,
                        "stale": o.stale,
                        "gain": o.gain,
                        "latency_ms": round(self.latency(n) * 1000),
                        "measured": n in self.cfg["latency"],
                        "measured_at": self.cfg["measured_at"].get(n),
                        "last_cal": self.last_cal.get(n),
                        "suggested_ms": round(self.suggested_delay(n) * 1000),
                        "delay_ms": round(self.offset_of(n) * 1000),
                        "trim_ms": self.cfg["trim_ms"].get(n, 0),
                        "sending": time.monotonic() - o.level_t < 2,
                    }
                )
            speakers.append(s)
        cal = dict(self.cal)
        if cal.get("until"):
            cal["remaining_s"] = max(0.0, round(cal["until"] - time.time(), 1))
        return {
            "global_delay_ms": round(gd * 1000),
            "slowest": slowest,
            "calibrating": self.calibrating,
            "muted": self.src.muted,
            "cal": cal,
            "speakers": speakers,
            "log_seq": LOG[-1][0] if LOG else 0,
            "mic": self.cfg.get("mic"),
            "capture": getattr(self.src.cmd, "device", None),
        }

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    def start(self):
        """Start the music capture, the control page and every speaker."""
        self.src.start()
        self.srv = ThreadingHTTPServer(("0.0.0.0", self.port), make_handler(self))
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        log(f"control page: {self.url}")
        time.sleep(0.5)
        for o in self.outputs.values():
            o.start()

    def stop(self):
        """Hand the speakers back (Sonos / Google Home are told to stop) and release everything."""
        self.cal_cancel = True
        ts = [
            threading.Thread(target=o.stop, kwargs={"wait": True}, daemon=True)
            for o in self.outputs.values()
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join(8)
        self.src.stop()
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None
        log("stopped")

    def run(self, calibrate_first=False):
        self.start()
        if calibrate_first:
            net = [o for o in self.outputs.values() if o.type in ("sonos", "chromecast")]
            deadline = time.monotonic() + 30
            waiting = ", ".join(o.name for o in net)
            log(f"calibration: waiting up to 30 s for speakers to connect: {waiting}")
            last = None
            while time.monotonic() < deadline and any(o.clients == 0 for o in net):
                waiting = [o.name for o in net if o.clients == 0]
                if waiting != last:
                    log(f"  still waiting for: {', '.join(waiting)}")
                    last = waiting
                time.sleep(0.5)
            missing = [o.name for o in net if o.clients == 0]
            if missing:
                log(f"not connected (skipping in calibration): {', '.join(missing)}")
            log("calibration: all set, letting speaker buffers settle for 10 s…")
            time.sleep(10)  # let them settle; Sonos often reconnects once right after starting
            for o in self.outputs.values():
                o.stale = False
            names = [
                n
                for n, o in self.outputs.items()
                if (o.clients if o.type in ("sonos", "chromecast") else o.status == "playing")
            ]
            self.calibrate(names, ask=input if sys.stdin.isatty() else None)
        try:
            if sys.stdin.isatty():
                run_console(self)
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, EOFError):
            with contextlib.suppress(Exception):
                self.stop()
            log("bye")
            os._exit(0)
