"""Latency calibration: chirp each speaker, record the mic, find when the chirps arrive."""

import time

import numpy as np

from .constants import MAX_LAT, SR
from .dsp import estimate_latency, make_chirp
from .logs import log


class CalibrationCancelledError(Exception):
    pass


class CalibrationMixin:
    """Calibration part of Hub (needs its outputs, src, cfg, save, global_delay, offset_of)."""

    def _phase(self, phase, secs=None, **kw):
        """Publish what calibration is doing right now (shown live on the control page)."""
        self.cal.update(
            phase=phase,
            until=time.time() + secs if secs else None,
            phase_secs=secs,
            detail="",
            **kw,
        )
        log(f"calibration: {phase}")

    def _wait(self, secs, on_tick=None):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if self.cal_cancel:
                raise CalibrationCancelledError()
            if on_tick:
                on_tick()
            time.sleep(0.1)

    def calibrate(self, names=None, ask=None):
        """ask: optional prompt function (e.g. input) — waits for the user before each speaker."""
        if self.calibrating:
            return
        self.calibrating, self.cal_cancel = True, False
        names = [n for n in (names or list(self.outputs)) if n in self.outputs]
        self.cal = {
            "active": True,
            "started": time.time(),
            "names": names,
            "i": 0,
            "n": len(names),
            "speaker": None,
            "results": {},
        }
        try:
            import sounddevice as sd

            for n in [n for n in names if not self.outputs[n].connected]:
                log(f"calibration: skipping {n} — not connected")
                self.cal["results"][n] = {"ok": False, "why": "not connected"}
            names = [n for n in names if self.outputs[n].connected]
            self.cal["n"] = len(names)
            if not names:
                raise ValueError("none of the chosen speakers are connected")
            mic = self.cfg.get("mic")
            mic_sr = int(sd.query_devices(mic, "input")["default_samplerate"])
            blocks, meta = [], {}

            def cb(indata, frames, ti, status):
                if "t0" not in meta:
                    meta["t0"] = time.monotonic() - frames / mic_sr
                blocks.append(indata[:, 0].copy())

            chirp_out, chirp_mic = make_chirp(SR), make_chirp(mic_sr)
            gaps = [
                0.0,
                1.13,
                2.41,
                3.37,
            ]  # always 4 chirps; irregular -> no aliasing between chirps
            self._phase("muting the music")
            self.src.muted = True
            drain = self.global_delay() + 0.5
            self._phase(
                f"clearing speaker buffers ({drain:.0f} s, so no music is left in them)", drain
            )
            self._wait(drain)
            self._phase(f"opening microphone '{mic or 'system default'}'")
            with sd.InputStream(
                device=mic, channels=1, samplerate=mic_sr, callback=cb, blocksize=1024
            ):
                self._wait(0.5)
                for i, name in enumerate(names, 1):
                    o = self.outputs[name]
                    self.cal.update(i=i, speaker=name)
                    while True:
                        if ask:
                            self._phase(f"waiting for you at the console before '{name}'")
                            if (
                                ask(f"\n>>> Ready for '{name}'? [Enter = chirp, s = skip] ")
                                .strip()
                                .lower()
                                .startswith("s")
                            ):
                                log(f"calibration: skipped {name}")
                                self.cal["results"][name] = {"ok": False, "why": "skipped"}
                                break
                        off = o.off_frames()
                        base = self.src.now() - off + int(0.3 * SR)
                        emits = []
                        for g in gaps:
                            f = base + int(g * SR)
                            o.add_overlay(f, chirp_out * 0.6)
                            emits.append(
                                self.src.time_of(f) + off / SR
                            )  # when the device receives it
                        ml = float(o.cfg.get("max_latency_s", MAX_LAT[o.type]))
                        listen = max(0.0, emits[-1] - time.monotonic()) + ml + 0.5
                        self._phase(
                            f"{name}: sending 4 chirps, listening up to {ml:.0f} s", listen
                        )

                        def tick(emits=emits):
                            sent = sum(e <= time.monotonic() for e in emits)
                            self.cal["detail"] = (
                                f"chirp {sent} of 4 sent"
                                if sent < 4
                                else "all 4 chirps sent — waiting for them to come out of the "
                                "speaker"
                            )

                        self._wait(listen, tick)
                        self._phase(f"{name}: analysing the recording")
                        rec = np.concatenate(blocks)
                        lat, snr = estimate_latency(rec, mic_sr, meta["t0"], emits, chirp_mic, ml)
                        ok = lat is not None and snr >= 4.0
                        if ok and lat > ml - 0.5:
                            log(
                                f"calibration: {name} answered right at the end of the "
                                f"{ml:.0f} s window — its real latency may be longer; add "
                                f'"max_latency_s": {ml * 2:.0f} to it in syncroom.json'
                            )
                        self.last_cal[name] = {"latency": lat, "snr": round(snr, 1), "ok": ok}
                        if ok:
                            o.stale = False
                            self.cfg["latency"][name] = round(lat, 4)
                            self.cfg["trim_ms"][name] = 0
                            self.cfg["measured_at"][name] = time.time()
                            self.cal["results"][name] = {
                                "ok": True,
                                "latency_ms": round(lat * 1000),
                                "snr": round(snr, 1),
                            }
                            log(f"calibration: {name} latency {lat * 1000:.0f} ms (snr {snr:.1f})")
                        else:
                            self.cal["results"][name] = {
                                "ok": False,
                                "snr": round(snr, 1),
                                "why": "not heard clearly",
                            }
                            log(
                                f"calibration: {name} NOT heard clearly (snr {snr:.1f}) — kept "
                                "old value; move the mic closer / turn the speaker up"
                            )

                        if ok or not ask:
                            break
                        if (
                            not ask(f">>> '{name}' wasn't heard. [r = retry, Enter = move on] ")
                            .strip()
                            .lower()
                            .startswith("r")
                        ):
                            break
            self._phase("done — music back on")
            log(
                f"calibration done. global delay {self.global_delay() * 1000:.0f} ms; offsets: "
                + ", ".join(f"{n}={self.offset_of(n) * 1000:.0f}ms" for n in self.outputs)
            )
        except CalibrationCancelledError:
            self._phase("cancelled — music back on")
        except Exception as e:  # noqa: BLE001
            self._phase(f"failed: {e}")
            self.cal["error"] = str(e)
            self.last_cal["error"] = str(e)
        finally:
            self.save()
            self.src.muted = False
            self.calibrating = False
            self.cal.update(active=False, until=None, finished=time.time())
