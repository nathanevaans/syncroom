# SyncRoom — context for Claude Code

Python app that plays the Mac's audio (Spotify) on several speakers at once, time-aligned:
Sonos Play:1, Google Home / Nest Mini (Chromecast), and a Bluetooth Samsung soundbar.
Owner: Nathan. Runs on his MacBook (macOS, Python 3.12).

## Code layout (package `syncroom/`, PEP 8 via ruff, line length 99 — config in `pyproject.toml`)
`cli.py` (argparse entry) · `hub.py` (Hub: timing model, speakers, config, state) ·
`calibration.py` (CalibrationMixin on Hub) · `outputs.py` (Http/Local/Pulse outputs) ·
`source.py` (ring buffer + BlackHole capture) · `dsp.py` (chirp + matched filter) ·
`web.py` (HTTP API + streams) · `static/index.html` (control page) · `console.py` ·
`discovery.py` · `logs.py` · `net.py` · `constants.py`. `syncroom.py` at the top is only a launcher.
Check before finishing: `ruff check . && ruff format --check . && pytest` (tests in `tests/`).

## How Nathan runs it
```bash
python3 syncroom.py run --capture "BlackHole 2ch"              # use saved latencies (= python3 -m syncroom ...)
python3 syncroom.py run --calibrate --capture "BlackHole 2ch"  # measure first
python3 syncroom.py discover                                   # list speakers
```
Mac setup: System Settings → Sound → Output = BlackHole 2ch (~80–90% volume), Spotify volume 100%,
soundbar paired over Bluetooth, its own volume maxed in Audio MIDI Setup.
Control page: `http://<mac-ip>:8765`. Console at the `syncroom>` prompt (type anything for help).

## Architecture
- **Source (master clock)**: 40 s ring buffer of 44.1 kHz s16 stereo. Reader "now" = wall clock − LEAD (0.4 s).
  `--capture DEVICE` → `CaptureProc`: sounddevice InputStream on BlackHole (48 kHz) → soxr ResampleStream → 44.1 kHz.
  Live mode drains input each tick, pads silence only on real stalls (Spotify paused), drops backlog > 0.5 s.
  (Earlier the source was ffmpeg avfoundation — replaced: it crackled.)
- **Outputs** read the buffer at `offset = global_delay − latency + trim`, `global_delay = max(latency) + 50 ms`.
  - `sonos` / `chromecast` / `http`: per-output MP3 320k stream at `/s/<name>.mp3` (lameenc). Sonos told via SoCo
    `play_uri(force_radio=True)`; Chromecast via pychromecast `play_media(..., stream_type="LIVE")`.
    Clients are fed only what's "due", so device latency = its own buffer → measurable.
    Watchdog re-plays if a device stops pulling for 12 s, or a Chromecast reports IDLE / other content.
    Socket timeout 10 s so dead connections free up.
  - `local`: sounddevice OutputStream by device name (soundbar = "[ AV ] T400"), latency="low",
    resync if |pos − target| > 40 ms. Waits/reconnects if the BT device is missing.
  - `pulse`: Linux pacat sink (unused on Mac).
- **Calibration**: mute music, wait for buffers to drain, per speaker inject 4 chirps (0.35 s exp sweep 600–6000 Hz)
  at irregular offsets [0, 1.13, 2.41, 3.37] s into that output only, record MacBook mic, matched filter +
  Hilbert envelope, pick the single latency that lines up all 4 (unambiguous due to irregular spacing).
  Accept only if every chirp individually ≥ 4× median envelope ("snr"). Listening window per type:
  MAX_LAT = sonos 12 s, chromecast 20 s, local 1.5 s; override per output with `"max_latency_s"`.
  Interactive prompts per speaker (Enter / s skip / r retry). Constant mic latency cancels out.
- **Config** `syncroom.json` (auto-saved): outputs list, `latency` (seconds), `trim_ms`, `gain`, `mic`,
  optional `enabled: false`, `max_latency_s`, `capture`.
- **Control**: web page is the main UI (no config editing needed). Per speaker: measured latency (+ when / snr),
  suggested added delay (`global_delay − latency`), live added delay (editable; = suggested + trim, ±1/10/50 nudges,
  "Use suggested" resets trim), connection pill + Reconnect, volume, Beep/Calibrate/Turn off. Checkbox selection
  drives "Test beep N" / "Calibrate N" (none selected = all). Calibration panel shows phase + countdown + chirp
  progress + live log, with Cancel. "Speakers & mic" dialog: mic, music input (capture mode only, hot-swapped),
  turn speakers on/off, remove, scan network/audio devices and add. Delay/speaker edits are locked (409) while
  calibrating. Disabled speakers are dropped from `hub.outputs` (dict is replaced, never mutated) and `stop()`ped.
  Console: `c [name]`, `t [name]`, `s` (status + send levels + capture overflows), `lat <name> <ms>`,
  `delay <name> <ms>`, `+10/-10 <name>`, `r <name>`, `off <name>`, `rec` (10 s WAV of captured audio), `q`.

## Nathan's speakers (current config)
sonos-bedroom (192.168.1.75, ~3.8 s latency), kitchen-speaker (Google Home Mini, 192.168.1.71, ~12–15 s),
danny-bedroom-speaker (Google Home, may be disabled), soundbar (BT "[ AV ] T400", ~0.2 s). Mac IP 192.168.1.17.

## History / lessons (don't regress)
- **librespot is abandoned**: Spotify refuses audio keys for Nathan's account (`error audio key 0 1`,
  librespot issues #1649/#1735, unfixed). Hence BlackHole capture. Code path still exists without `--capture`.
- Chromecast: keep the pychromecast browser alive (stopping discovery before `cast.wait()` broke connecting).
- Overlay pruning must use the output's own delayed timeline (`now − offset − 10 s`). Using `now` deleted
  chirps when offsets exceeded ~10 s (bug fixed 2026-10-25).
- latency="high" + 80 ms drift guard on the BT output broke chirps on Nathan's Mac — reverted to "low"/40 ms.
- Quiet capture (−44 dB) caused noisy sound → fix was Mac/Spotify volume, not code.

## Known issues / ideas
- Sonos sometimes drops and reconnects the stream; each reconnect changes its latency (flagged "stale").
- Kitchen Mini drops its control connection (SSL EOF on :8009) — likely weak Wi-Fi.
- Wi-Fi speakers drift a few ms/min vs the Mac clock; no automatic correction yet (recalibrate or trim).
  Possible improvement: periodic silent re-measure, or adaptive resampling per output.
- Everything is delayed to the slowest speaker (Google Homes, 12–15 s) → long lag on play/skip.
- Tested in a sandbox only (no real speakers); verify on hardware.
