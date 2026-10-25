"""Calibration signal processing: the chirp and the matched-filter latency estimate."""

import numpy as np


def make_chirp(sr, dur=0.35, f0=600.0, f1=6000.0):
    """Exponential sine sweep with tapered ends: sharp autocorrelation, reproducible on small
    speakers."""
    from scipy.signal.windows import tukey

    t = np.arange(int(sr * dur)) / sr
    k = np.log(f1 / f0)
    phase = 2 * np.pi * f0 * dur / k * (np.exp(t / dur * k) - 1)
    return (np.sin(phase) * tukey(len(t), 0.2)).astype(np.float32)


def estimate_latency(rec, sr, t_rec0, emits, chirp, max_lat):
    """rec: mic samples, sample 0 at monotonic time t_rec0. emits: times each chirp was handed to
    the speaker. Finds the single latency L that best explains all chirps (irregular spacing
    makes it unambiguous). Returns (L seconds, snr)."""
    from scipy.signal import fftconvolve, hilbert

    k_first = max(0, int((min(emits) - t_rec0) * sr))
    k_last = int((max(emits) - t_rec0 + max_lat) * sr) + len(chirp)
    seg = rec[k_first:k_last]
    if len(seg) < len(chirp) * 2:
        return None, 0.0
    env = np.abs(hilbert(fftconvolve(seg, chirp[::-1], mode="valid")))  # env[k]: chirp starts at k
    lags = np.arange(int(max_lat * sr))
    score = np.zeros(len(lags))
    for e in emits:
        idx = int(round((e - t_rec0) * sr)) - k_first + lags
        ok = (idx >= 0) & (idx < len(env))
        score[ok] += env[idx[ok]]
    best = int(np.argmax(score))
    # every chirp must stand out on its own, so one random bang can't fake a detection
    noise = np.median(env) + 1e-12
    per = []
    for e in emits:
        k = int(round((e - t_rec0) * sr)) - k_first + best
        w = env[max(0, k - int(0.002 * sr)) : k + int(0.002 * sr) + 1]
        per.append(w.max() / noise if len(w) else 0.0)
    return best / sr, float(min(per))
