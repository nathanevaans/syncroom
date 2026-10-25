"""The master clock: a ring buffer fed by BlackHole capture (or ffmpeg for a test file)."""

import collections
import contextlib
import os
import subprocess
import threading
import time

import numpy as np

from .constants import BUF_SEC, CH, FRAME_BYTES, LEAD, SR
from .logs import log


class CaptureProc:
    """Captures an input device (BlackHole) directly with CoreAudio/PortAudio, resampling to
    44.1 kHz with soxr if needed. Quacks like the subprocess Source expects."""

    def __init__(self, device):
        import sounddevice as sd

        info = sd.query_devices(device, "input")
        self.device = device
        self.rate = int(info["default_samplerate"])
        self.q = collections.deque()
        self.overflows = 0
        self.rs = None
        if self.rate != SR:
            import soxr

            self.rs = soxr.ResampleStream(self.rate, SR, 2, dtype="float32", quality="HQ")
        self.stream = sd.InputStream(
            device=device,
            samplerate=self.rate,
            channels=2,
            dtype="float32",
            callback=self._cb,
            latency="high",
        )
        self.stream.start()
        log(
            f"source: capturing '{info['name']}' at {self.rate} Hz"
            + (" (high-quality resample to 44.1 kHz)" if self.rs else "")
        )

    def _cb(self, indata, frames, ti, status):
        if status.input_overflow:
            self.overflows += 1
        x = self.rs.resample_chunk(indata) if self.rs is not None else indata
        self.q.append((np.clip(x, -1.0, 32767 / 32768) * 32767).astype(np.int16).tobytes())

    def read(self):
        out = []
        while self.q:
            out.append(self.q.popleft())
        return b"".join(out)

    def poll(self):
        return None

    def terminate(self):
        self.stream.close()


class Source:
    """Master clock. Takes live capture (or ffmpeg PCM, paced at exactly real time) and pads
    silence when Spotify is paused, so every stream keeps flowing."""

    def __init__(self, cmd, live=False):
        self.cmd = cmd
        self.live = live  # True for real-time capture: never block it, trim backlog
        self.buf = np.zeros((SR * BUF_SEC, CH), np.int16)
        self.n = 0
        self.t0 = time.monotonic()
        self.lock = threading.Lock()
        self.muted = False
        self.pending = b""
        self.proc = None
        self.running = False

    def _spawn(self):
        if isinstance(self.cmd, CaptureProc):
            self.proc = self.cmd
        else:
            self.proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL
            )
            os.set_blocking(self.proc.stdout.fileno(), False)
        self.pending = b""

    def _read(self, need):
        if isinstance(self.proc, CaptureProc):
            return self.proc.read()
        return os.read(self.proc.stdout.fileno(), need)

    def start(self):
        self._spawn()
        self.t0 = time.monotonic()
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _raw(self):
        return int((time.monotonic() - self.t0) * SR)

    def now(self):
        """The frame that is 'current' for readers."""
        return self._raw() - LEAD

    def time_of(self, f):
        """Monotonic time at which frame f is 'current' for readers."""
        return self.t0 + (f + LEAD) / SR

    def stop(self):
        self.running = False
        if self.proc is not None:
            with contextlib.suppress(Exception):
                self.proc.terminate()

    def _run(self):
        while self.running:
            if self.proc.poll() is not None:
                log("source exited — restarting in 2 s")
                time.sleep(2)
                self._spawn()
            raw = self._raw()
            want = raw - self.n
            if want <= 0:
                time.sleep(0.005)
                continue
            # live capture: drain everything so the capture never stalls;
            # decoders: read only what's due
            need = (1 << 20) if self.live else want * FRAME_BYTES - len(self.pending)
            if need > 0:
                try:
                    chunk = self._read(need)
                    self.pending += chunk
                except BlockingIOError:
                    pass
            if self.live and len(self.pending) > int(0.5 * SR) * FRAME_BYTES:
                # capture clock ran ahead of ours: drop the excess once, keeping 0.1 s
                keep = int(0.1 * SR) * FRAME_BYTES
                self.pending = self.pending[-keep:]
            nb = min(len(self.pending) // FRAME_BYTES, want) * FRAME_BYTES
            data = np.frombuffer(self.pending[:nb], np.int16).reshape(-1, CH)
            self.pending = self.pending[nb:]
            # pad with silence only when the input has genuinely stalled (Spotify paused, capture
            # hiccup) — i.e. readers are about to run dry — never for ordinary bursty delivery
            short = (raw - LEAD + int(0.05 * SR)) - (self.n + len(data))
            if short > 0:
                data = np.vstack([data, np.zeros((short, CH), np.int16)])
            if len(data):
                with self.lock:
                    idx = np.arange(self.n, self.n + len(data)) % len(self.buf)
                    self.buf[idx] = data
                    self.n += len(data)
            time.sleep(0.005)

    def read(self, a, b):
        out = np.zeros((b - a, CH), np.int16)
        if self.muted or b <= a:
            return out
        with self.lock:
            lo, hi = max(a, self.n - len(self.buf)), min(b, self.n)
            if hi > lo:
                out[lo - a : hi - a] = self.buf[np.arange(lo, hi) % len(self.buf)]
        return out
