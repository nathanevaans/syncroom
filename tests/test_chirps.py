"""Calibration chirps must all reach the speaker, however large its added delay is.

Regression test for the bug where speakers delayed by more than ~10 s played only the last of the
four chirps (overlay pruning used the live clock instead of the speaker's delayed timeline).
"""

import numpy as np
import pytest
from scipy.signal import fftconvolve

from syncroom.constants import CH, SR
from syncroom.dsp import make_chirp
from syncroom.hub import Hub
from syncroom.outputs import Output

GAPS = [0.0, 1.13, 2.41, 3.37]  # must match calibration.py


class FakeSource:
    muted = True

    def __init__(self):
        self.f = 100 * SR

    def now(self):
        return self.f

    def read(self, a, b):
        return np.zeros((b - a, CH), np.int16)


class FakeHub:
    latency = Hub.latency
    global_delay = Hub.global_delay
    suggested_delay = Hub.suggested_delay
    offset_of = Hub.offset_of

    def __init__(self, latency_s, trim_ms):
        self.cfg = {
            "latency": {"slow": 30.0, "x": latency_s},
            "trim_ms": {"x": trim_ms},
            "gain": {},
        }
        self.src = FakeSource()
        self.outputs = {n: Output(self, {"name": n, "type": "http"}) for n in ("slow", "x")}


def count_chirps(hub, out, chirp):
    off = out.off_frames()
    base = hub.src.now() - off + int(0.3 * SR)
    for g in GAPS:  # added one at a time, exactly like calibrate()
        out.add_overlay(base + int(g * SR), chirp * 0.6)
    pos, rendered = hub.src.now() - off, []
    for _ in range(int(6 / 0.02)):  # 6 s of 20 ms ticks
        hub.src.f += int(0.02 * SR)
        target = hub.src.now() - off
        rendered.append(out.render(pos, target)[:, 0].astype(np.float32))
        pos = target
    env = np.abs(fftconvolve(np.concatenate(rendered), chirp[::-1], "valid"))
    peaks = env > 0.5 * env.max()
    return int(np.count_nonzero(np.diff(peaks.astype(int)) == 1))


@pytest.mark.parametrize("latency_s, trim_ms", [(29.9, 0), (15.0, 0), (0.2, 0), (0.2, 10_000)])
def test_all_four_chirps_play(latency_s, trim_ms):
    hub = FakeHub(latency_s, trim_ms)
    assert count_chirps(hub, hub.outputs["x"], make_chirp(SR)) == 4


def test_latency_estimate_recovers_known_delay():
    from syncroom.dsp import estimate_latency

    sr, true_lat = 48000, 1.234
    chirp = make_chirp(sr)
    emits = [0.5 + g for g in GAPS]
    rec = np.random.default_rng(0).normal(0, 0.01, int(8 * sr)).astype(np.float32)
    for e in emits:
        k = int((e + true_lat) * sr)
        rec[k : k + len(chirp)] += chirp * 0.3
    lat, snr = estimate_latency(rec, sr, 0.0, emits, chirp, max_lat=3.0)
    assert lat == pytest.approx(true_lat, abs=0.002)
    assert snr >= 4.0
