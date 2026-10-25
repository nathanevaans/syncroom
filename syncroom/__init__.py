"""
SyncRoom — play Spotify on Sonos, Google Home / Chromecast and Bluetooth speakers at once,
with per-speaker latency measured by playing a chirp and listening with a microphone.

How it works
  librespot (Spotify Connect)  ->  raw PCM  ->  hub (master clock, 40 s ring buffer)
     hub -> HTTP MP3 stream   -> Sonos (SoCo) / Chromecast & Google Home (pychromecast)
     hub -> pacat / sounddevice -> Bluetooth or wired speakers on this machine
  Every output reads the ring buffer at its own delay, so all speakers make sound together:
     offset_d = global_delay - latency_d + trim_d,   global_delay = max_d latency_d + 50 ms
  Calibration mutes the music, sends chirps to one speaker at a time, records the mic and
  finds the arrival time by matched filtering. Constant errors (mic latency etc.) hit every
  speaker equally, so they cancel out of the alignment.

Usage
  python -m syncroom discover                 # find speakers, print config entries
  python -m syncroom run                      # start; open http://<this-machine>:8765
  python -m syncroom run --calibrate          # measure latencies at start-up
  python -m syncroom run --file song.mp3      # test without Spotify
"""

__version__ = "0.2.0"
