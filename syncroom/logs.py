"""Console logging plus a ring buffer of recent lines for the control page."""

import collections
import contextlib
import itertools
import time

LOG = collections.deque(maxlen=300)  # (seq, line) — recent log lines for the control page
_log_seq = itertools.count(1)


def log(*a):
    line = " ".join([time.strftime("%H:%M:%S"), *map(str, a)])
    LOG.append((next(_log_seq), line))
    with contextlib.suppress(Exception):  # logging must never kill the thread calling it
        print(line, flush=True)
