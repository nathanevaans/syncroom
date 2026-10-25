"""Auto-setup folds a speaker scan into the config (no network: the scan is faked)."""

import json

import pytest

from syncroom import discovery, outputs
from syncroom.hub import Hub

SONOS = {"type": "sonos", "name": "Bedroom", "ip": "192.168.1.80", "uid": "RINCON_1"}
CAST = {"type": "chromecast", "name": "Kitchen speaker", "cast_name": "Kitchen speaker"}
JBL = {"type": "local", "name": "JBL Flip 5", "device": "JBL Flip 5"}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    started = []
    for cls in (outputs.HttpOutput, outputs.LocalOutput):
        monkeypatch.setattr(cls, "start", lambda self: started.append(self.name))
        monkeypatch.setattr(cls, "stop", lambda self, wait=False: None)
    monkeypatch.setattr(discovery, "scan_network", lambda: [dict(SONOS), dict(CAST)])
    monkeypatch.setattr(discovery, "bluetooth_speakers", lambda: [dict(JBL)])
    cfg = tmp_path / "syncroom.json"
    cfg.write_text(
        json.dumps(
            {
                "spotify_name": "SyncRoom",
                "librespot_args": ["--cache", "x"],
                "outputs": [{"name": "sonos-bedroom", "type": "sonos", "ip": "192.168.1.75"}],
                "latency": {"sonos-bedroom": 3.8},
            }
        )
    )
    h = Hub(str(cfg), ["cat", "/dev/zero"])
    h.started = started
    return h


def saved(hub):
    with open(hub.cfg_path) as f:
        return json.load(f)


def test_adds_new_speakers_and_drops_librespot_keys(hub):
    hub.outputs["sonos-bedroom"].cfg["uid"] = "RINCON_1"  # already known by uid
    added = hub.auto_setup()
    assert added == ["kitchen-speaker", "jbl-flip-5"]
    cfg = saved(hub)
    assert [c["name"] for c in cfg["outputs"]] == [
        "sonos-bedroom",
        "kitchen-speaker",
        "jbl-flip-5",
    ]
    assert "spotify_name" not in cfg and "librespot_args" not in cfg
    # Wi-Fi speakers might be a housemate's: added but off; this Mac's Bluetooth ones are on
    assert cfg["outputs"][1].get("enabled") is False and "kitchen-speaker" not in hub.outputs
    assert "jbl-flip-5" in hub.outputs
    assert hub.auto_setup() == []  # nothing new the second time


def test_follows_a_sonos_to_its_new_ip(hub):
    hub.outputs["sonos-bedroom"].cfg["uid"] = "RINCON_1"
    hub.auto_setup()
    c = saved(hub)["outputs"][0]
    assert (c["ip"], c["uid"]) == ("192.168.1.80", "RINCON_1")
    assert "sonos-bedroom" in hub.started  # told to play again at the new address
    assert saved(hub)["latency"]["sonos-bedroom"] == 3.8


def test_learns_sonos_uid_by_ip_without_adding_a_duplicate(hub, monkeypatch):
    monkeypatch.setattr(discovery, "scan_network", lambda: [dict(SONOS, ip="192.168.1.75")])
    hub.auto_setup()
    sonos = [c for c in saved(hub)["outputs"] if c["type"] == "sonos"]
    assert sonos == [
        {"name": "sonos-bedroom", "type": "sonos", "ip": "192.168.1.75", "uid": "RINCON_1"}
    ]


def test_removed_speakers_stay_removed(hub):
    hub.auto_setup()
    hub.remove_speaker("jbl-flip-5")
    hub.remove_speaker("kitchen-speaker")
    assert hub.auto_setup() == []
    assert set(saved(hub)["ignored"]) == {"local:JBL Flip 5", "chromecast:Kitchen speaker"}
