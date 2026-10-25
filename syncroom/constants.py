"""Audio format and per-speaker-type timing defaults."""

SR = 44100
CH = 2
FRAME_BYTES = 2 * CH
BUF_SEC = 40
LEAD = int(0.4 * SR)  # cushion between source writer and readers (absorbs bursty input)
DEFAULT_LAT = {"sonos": 2.0, "chromecast": 2.5, "http": 1.5, "pulse": 0.3, "local": 0.3}
MAX_LAT = {"sonos": 12.0, "chromecast": 20.0, "http": 20.0, "pulse": 1.5, "local": 1.5}
