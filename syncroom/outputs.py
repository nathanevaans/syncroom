"""Speaker outputs: HTTP stream (Sonos / Chromecast), PortAudio device, PulseAudio sink."""

import subprocess
import threading
import time

import numpy as np

from .constants import SR
from .logs import log


class Output:
    def __init__(self, hub, cfg):
        self.hub, self.cfg = hub, cfg
        self.name, self.type = cfg["name"], cfg["type"]
        self.overlays = []  # (start_frame, mono float32 signal) for chirps/clicks
        self.olock = threading.Lock()
        self.clients = 0
        self.status = "idle"
        self.level_db, self.level_t = -120.0, 0.0  # loudness of what we're sending right now
        self.stale = False  # True when the device reconnected after its latency was measured
        self.stopped = False  # set when the speaker is switched off or removed in the control page
        self.paused = False
        self.kick = threading.Event()  # "reconnect now"

    @property
    def connected(self):
        if self.type in ("sonos", "chromecast", "http"):
            return self.clients > 0
        return self.status == "playing"

    @property
    def gain(self):
        return self.hub.cfg["gain"].get(self.name, 1.0)

    def off_frames(self):
        return int(round(self.hub.offset_of(self.name) * SR))

    def add_overlay(self, start, sig):
        with self.olock:
            # judge "old" on this speaker's own (delayed) timeline, not the live one
            cutoff = self.hub.src.now() - self.off_frames() - 10 * SR
            self.overlays = [(s, x) for s, x in self.overlays if s + len(x) > cutoff]
            self.overlays.append((start, sig))

    def render(self, a, b):
        x = self.hub.src.read(a, b).astype(np.float32) * self.gain
        with self.olock:
            for s, sig in self.overlays:
                lo, hi = max(a, s), min(b, s + len(sig))
                if hi > lo:
                    x[lo - a : hi - a] += sig[lo - s : hi - s, None] * 32767
        if len(x):
            rms = float(np.sqrt(np.mean(x * x)))
            self.level_db = 20 * np.log10(rms / 32768) if rms > 0 else -120.0
            self.level_t = time.monotonic()
        return np.clip(x, -32768, 32767).astype(np.int16)

    def stream(self, write, tick=0.02):
        """Push audio as it becomes due for this output. Clients can never run ahead of real time,
        so a device's latency is just its own buffer + DSP — exactly what calibration measures."""
        src = self.hub.src
        off = self.off_frames()
        pos = src.now() - off
        while not self.stopped:
            new = self.off_frames()
            if new != off:  # trim/calibration changed: repeat or skip a little audio
                pos -= new - off
                off = new
            target = src.now() - off
            if target - pos > 3 * SR:  # fell way behind (machine slept etc.)
                pos = target
            if target > pos:
                write(self.render(pos, target))
                pos = target
            time.sleep(tick)

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        self.kick.set()


class HttpOutput(Output):
    """Served at /s/<name>.mp3. 'sonos' and 'chromecast' types are also told to play it."""

    def url(self):
        return f"http://{self.hub.host}:{self.hub.port}/s/{self.name}.mp3"

    def _watchdog(self):
        """If the speaker stops pulling the stream (dropped, or another app took it over),
        tell it to play again. A fresh connection means fresh latency, so flag recalibration."""
        idle_since = None
        time.sleep(30)
        while not self.stopped:
            time.sleep(3)
            if self.stopped:
                return
            if self.hub.calibrating or getattr(self, "paused", False):
                idle_since = None
                continue
            if self.clients > 0 and not self._cast_idle():
                idle_since = None
                continue
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since > 12:
                log(f"[{self.name}] stopped listening to the stream — telling it to play again")
                self._start()
                if self.name in self.hub.cfg["latency"]:
                    self.stale = True
                    log(
                        f"[{self.name}] reconnected: its latency may have changed — "
                        f"run `c {self.name}`"
                    )
                idle_since = None
                time.sleep(20)

    def start(self):
        self.paused = False
        threading.Thread(target=self._start, daemon=True).start()
        if self.type in ("sonos", "chromecast") and not getattr(self, "_watching", False):
            self._watching = True
            threading.Thread(target=self._watchdog, daemon=True).start()

    def stop(self):
        super().stop()
        self.status = "off"

        def _tell():
            try:
                if self.type == "sonos":
                    import soco

                    soco.SoCo(self.cfg["ip"]).stop()
                elif self.type == "chromecast" and getattr(self, "cast", None) is not None:
                    self.cast.media_controller.stop()
            except Exception as e:  # noqa: BLE001
                log(f"[{self.name}] couldn't tell it to stop: {e}")

        threading.Thread(target=_tell, daemon=True).start()

    def _cast_idle(self):
        """True if a Chromecast reports it isn't playing our stream any more."""
        cast = getattr(self, "cast", None)
        if self.type != "chromecast" or cast is None:
            return False
        try:
            st = cast.media_controller.status
            return (
                st is None
                or st.player_state in ("IDLE", "UNKNOWN")
                or (st.content_id and st.content_id != self.url())
            )
        except Exception:  # noqa: BLE001
            return False

    def _start(self):
        try:
            if self.type == "sonos":
                import soco

                sp = soco.SoCo(self.cfg["ip"])
                sp.play_uri(self.url(), title="SyncRoom", force_radio=True)
                self.status = "playing"
                log(f"[{self.name}] Sonos {sp.player_name} -> {self.url()}")
            elif self.type == "chromecast":
                import pychromecast

                if getattr(self, "cast", None) is None or not self.cast.socket_client.is_connected:
                    casts, browser = pychromecast.get_listed_chromecasts(
                        friendly_names=[self.cfg["cast_name"]]
                    )
                    self.browser = (
                        browser  # must stay alive: the cast connection uses its zeroconf
                    )
                    if not casts:
                        self.status = "not found"
                        log(f"[{self.name}] Chromecast '{self.cfg['cast_name']}' not found")
                        return
                    self.cast = casts[0]
                self.cast.wait(timeout=20)
                mc = self.cast.media_controller
                mc.play_media(self.url(), "audio/mpeg", title="SyncRoom", stream_type="LIVE")
                mc.block_until_active(timeout=20)
                self.status = "playing"
                log(f"[{self.name}] casting -> {self.url()}")
            else:
                self.status = "serving"
        except Exception as e:  # noqa: BLE001
            self.status = f"error: {e}"
            log(f"[{self.name}] start failed: {e}")


class PulseOutput(Output):
    """Linux (PulseAudio/PipeWire): Bluetooth speakers appear as sinks, e.g. bluez_output.XX..."""

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stopped:
            cmd = [
                "pacat",
                "--playback",
                "--raw",
                "--format=s16le",
                f"--rate={SR}",
                "--channels=2",
                "--latency-msec=60",
                f"--stream-name=SyncRoom {self.name}",
            ]
            if self.cfg.get("sink"):
                cmd.append(f"--device={self.cfg['sink']}")
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0)
            self.status = "playing"
            try:
                self.stream(lambda pcm, p=p: p.stdin.write(pcm.tobytes()))
            except (BrokenPipeError, OSError) as e:
                if not self.stopped:
                    self.status = f"restarting ({e})"
                    log(f"[{self.name}] pacat died ({e}); is the speaker connected? retrying")
                    time.sleep(3)
            finally:
                p.terminate()


class LocalOutput(Output):
    """macOS/Windows/any PortAudio device by name (Bluetooth speakers show up as devices).
    Waits for the device if it isn't connected yet, and reconnects if it drops."""

    def start(self):
        """First call starts the device loop; later calls (the Reconnect button) reopen the
        device."""
        self.paused = False
        t = getattr(self, "_thread", None)
        if t is not None and t.is_alive():
            self.kick.set()
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _callback(self, outdata, frames, ti, status):
        src = self.hub.src
        new = self.off_frames()
        if self.pos is None:
            self.pos = src.now() - new
        elif new != self.off:
            self.pos -= new - self.off
        self.off = new
        target = src.now() - new
        if abs(self.pos - target) > 0.04 * SR:  # clock drift guard
            self.pos = target
        outdata[:] = self.render(self.pos, self.pos + frames) / 32768.0
        self.pos += frames

    def _run(self):
        import sounddevice as sd

        dev = self.cfg.get("device")
        warned = False
        while not self.stopped:
            self.kick.clear()
            try:
                # PortAudio only sees devices that existed when it started, so rescan for a
                # Bluetooth speaker that connected later (not while the calibration mic is open)
                if warned and not self.hub.calibrating:
                    sd._terminate()
                    sd._initialize()
                done = threading.Event()
                self.pos, self.off = None, self.off_frames()
                with sd.OutputStream(
                    device=dev,
                    samplerate=SR,
                    channels=2,
                    dtype="float32",
                    callback=self._callback,
                    latency="low",
                    finished_callback=done.set,
                ):
                    self.status = "playing"
                    if warned:
                        log(f"[{self.name}] connected to '{dev}'")
                    warned = False
                    while not done.wait(1.0):
                        if self.stopped or self.kick.is_set():
                            break
                if self.stopped:
                    break
                if self.kick.is_set():
                    log(f"[{self.name}] reconnecting to '{dev}'")
                    continue
                log(f"[{self.name}] '{dev}' disconnected — waiting for it to come back")
                self.status = "disconnected"
                warned = True
            except Exception as e:  # noqa: BLE001
                if not warned:
                    log(
                        f"[{self.name}] '{dev}' not available ({e}) — connect it in Bluetooth "
                        f"settings; will keep retrying"
                    )
                    warned = True
                self.status = "waiting for device"
            self.kick.wait(5)
        self.status = "off"


def make_output(hub, cfg):
    t = cfg["type"]
    if t in ("sonos", "chromecast", "http"):
        return HttpOutput(hub, cfg)
    if t == "pulse":
        return PulseOutput(hub, cfg)
    if t == "local":
        return LocalOutput(hub, cfg)
    raise ValueError(f"unknown output type {t}")
