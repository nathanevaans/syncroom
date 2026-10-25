# SyncRoom

Spotify → Sonos + Google Home/Nest + Bluetooth speakers (e.g. the Samsung soundbar), time-aligned by mic calibration.

Runs on one always-on machine on your Wi-Fi that has a mic and Bluetooth — a Linux box / Raspberry Pi 4–5 (with a USB mic) or a Mac.

## Setup (Linux / Pi)
```bash
sudo apt install ffmpeg pulseaudio-utils libportaudio2 cargo   # PipeWire's pulse layer is fine
cargo install librespot                                        # Spotify Connect receiver (needs Premium)
pip install -r requirements.txt
bluetoothctl                # pair + connect the soundbar: scan on / pair XX / trust XX / connect XX
python syncroom.py discover # prints ready-to-paste config lines for every speaker found
cp syncroom.example.json syncroom.json   # paste the discovered lines into "outputs"
python syncroom.py run --calibrate
```
On a Mac: `brew install librespot ffmpeg portaudio`, and use `{"type": "local", "device": "Samsung Soundbar"}` for Bluetooth speakers instead of `pulse`.

Then in Spotify pick **SyncRoom** as the device, and open `http://<that-machine>:8765` for the control page.

## Calibration
Put the machine's mic where you usually sit, keep the room quiet, press **Calibrate all** (≈10 s per Wi-Fi speaker, 4 s per Bluetooth one). Music mutes, each speaker plays four chirps, the hub finds when they arrive and delays every other speaker to match the slowest. Then press **Sync test** — you should hear one click per beat, not a flam. Fine-tune with the ±10 ms trims.

## Things to know
- **Recalibrate after restarting** or after a speaker reconnects: Sonos/Google Home pick a new buffer size each time they connect.
- **Drift:** each speaker's crystal runs a few tens of ppm off, so Wi-Fi speakers can creep a few ms per minute. Over a long session press Calibrate again (or nudge trims).
- The Google Homes must be added **individually**, not as a Google speaker group.
- If the Sonos is grouped, ungroup it in the Sonos app first.
- Total delay from pressing play to sound ≈ the slowest speaker (~2–3 s) — fine for music, not for TV lip-sync.
- Open TCP port 8765 on the host's firewall so the speakers can pull the stream.
