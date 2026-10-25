"""Drive the control-page API against a hub with fake (HTTP-only) speakers and a silent source."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from syncroom.hub import Hub


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("cfg") / "syncroom.json"
    port = _free_port()
    cfg.write_text(
        json.dumps(
            {
                "http_port": port,
                "host_ip": "127.0.0.1",
                "outputs": [
                    {"name": "a", "type": "http"},
                    {"name": "b", "type": "http"},
                    {"name": "c", "type": "http", "enabled": False},
                ],
                "latency": {"a": 3.0},
                "trim_ms": {"a": 25},
            }
        )
    )
    h = Hub(str(cfg), ["cat", "/dev/zero"])
    threading.Thread(target=h.run, daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    h.base = f"http://127.0.0.1:{port}"
    return h


def post(hub, path, body=None):
    req = urllib.request.Request(
        hub.base + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def speaker(state, name):
    return next(s for s in state["speakers"] if s["name"] == name)


def test_state_shows_latency_and_delays(hub):
    with urllib.request.urlopen(hub.base + "/api/state") as r:
        s = json.load(r)
    a, b = speaker(s, "a"), speaker(s, "b")
    assert s["slowest"] == "a"
    assert (a["latency_ms"], a["suggested_ms"], a["delay_ms"]) == (3000, 50, 75)
    assert b["measured"] is False and b["suggested_ms"] == 1550
    assert speaker(s, "c") == {"name": "c", "type": "http", "enabled": False, "where": ""}


def test_edit_nudge_and_reset_delay(hub):
    _, s = post(hub, "/api/delay", {"name": "b", "ms": 1600})
    assert speaker(s, "b")["delay_ms"] == 1600
    _, s = post(hub, "/api/trim", {"name": "b", "ms": 10})
    assert speaker(s, "b")["delay_ms"] == 1610
    _, s = post(hub, "/api/delay", {"name": "b", "ms": "suggested"})
    assert speaker(s, "b")["delay_ms"] == speaker(s, "b")["suggested_ms"]


def test_speaker_on_off_add_remove(hub):
    _, s = post(hub, "/api/speaker/enable", {"name": "c", "on": True})
    assert speaker(s, "c")["enabled"] and "c" in hub.outputs
    _, s = post(hub, "/api/speaker/enable", {"name": "c", "on": False})
    assert not speaker(s, "c")["enabled"] and "c" not in hub.outputs
    _, s = post(hub, "/api/speaker/add", {"type": "local", "name": "Fake BT", "device": "Nope"})
    assert speaker(s, "fake-bt")["enabled"]
    code, err = post(hub, "/api/speaker/add", {"type": "local", "device": "Nope"})
    assert code == 400 and "already added" in err["error"]
    _, s = post(hub, "/api/speaker/remove", {"name": "fake-bt"})
    assert all(x["name"] != "fake-bt" for x in s["speakers"])


def test_beep_subset_only_touches_chosen_speakers(hub):
    before = {n: len(o.overlays) for n, o in hub.outputs.items()}
    post(hub, "/api/synctest", {"names": ["b"]})
    assert len(hub.outputs["b"].overlays) > before["b"]
    assert len(hub.outputs["a"].overlays) == before["a"]


def test_delays_locked_while_calibrating(hub):
    hub.calibrating = True
    try:
        code, err = post(hub, "/api/delay", {"name": "b", "ms": 1})
        assert code == 409
    finally:
        hub.calibrating = False


def test_calibration_without_connected_speakers_reports_why(hub):
    post(hub, "/api/calibrate", {"names": ["a"]})
    for _ in range(50):
        if not hub.calibrating and hub.cal.get("finished"):
            break
        time.sleep(0.1)
    assert hub.cal["results"]["a"] == {"ok": False, "why": "not connected"}
    assert hub.src.muted is False
