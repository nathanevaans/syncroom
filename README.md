# SyncRoom

Plays the Spotify app on Sonos, Google Home / Nest and Bluetooth speakers at once, time-aligned by
mic calibration. macOS 13+.

## Install
```bash
macapp/build.sh        # makes .venv, installs everything, builds ~/Applications/SyncRoom.app
```
Needs [BlackHole 2ch](https://existential.audio/blackhole/) (`brew install blackhole-2ch`): set
System Settings → Sound → Output to **BlackHole 2ch** (~80–90%) and Spotify's volume to 100%.
Open **SyncRoom** (Spotlight). A speaker icon appears in the menu bar; it starts, finds your
speakers and plays whatever the Mac plays. Allow the microphone when asked (for calibration).

New Bluetooth speakers connected to the Mac are used straight away; new Sonos / Google Home
speakers are added switched off (they might be someone else's) — tick them under **Speakers**.
Then **Calibrate all speakers…** with the Mac where you listen.

Terminal instead: `.venv/bin/python -m syncroom run [--calibrate]` (console + control page at
`http://<mac>:8765`). Config lives in `~/Library/Application Support/SyncRoom/syncroom.json`,
log in `~/Library/Logs/SyncRoom.log`.

## Things to know
- Recalibrate after a speaker reconnects: Sonos / Google Home pick a new buffer each time.
- Wi-Fi speakers drift a few ms per minute; recalibrate or nudge trims on long sessions.
- Add Google Homes individually, not as a group; ungroup the Sonos.
- Everything waits for the slowest speaker (~15 s for Google Home) — fine for music, not for TV.
