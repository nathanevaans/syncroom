"""HTTP server: the control page, its JSON API and the per-speaker MP3 streams."""

import json
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .constants import SR
from .logs import LOG, log

UI_HTML = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


def make_handler(hub):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_headers(self):
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("icy-name", "SyncRoom")
            self.end_headers()

        def do_HEAD(self):
            if self.path.startswith("/s/"):
                log(f"[{self.path[3:-4]}] HEAD probe from {self.client_address[0]}")
                self._stream_headers()
            else:
                self.send_response(200)
                self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                body = UI_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/state":
                self._json(hub.state())
            elif u.path == "/api/log":
                since = int(parse_qs(u.query).get("since", ["0"])[0])
                self._json([[i, line] for i, line in list(LOG) if i > since])
            elif u.path == "/api/devices":
                self._json(hub.devices())
            elif u.path == "/api/discover":
                self._json(hub.discover())
            elif u.path.startswith("/s/") and u.path.endswith(".mp3"):
                o = hub.outputs.get(u.path[3:-4])
                if not o:
                    return self._json({"error": "no such output"}, 404)
                import lameenc

                enc = lameenc.Encoder()
                enc.set_bit_rate(320)
                enc.set_in_sample_rate(SR)
                enc.set_channels(2)
                enc.set_quality(2)
                self._stream_headers()
                self.connection.settimeout(
                    10
                )  # a speaker that silently vanished frees its slot fast
                o.clients += 1
                if o.name in hub.cfg["latency"] and not hub.calibrating:
                    o.stale = True
                log(f"[{o.name}] stream connected from {self.client_address[0]}")
                try:
                    o.stream(lambda pcm: self.wfile.write(enc.encode(pcm.tobytes())))
                except (TimeoutError, BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    o.clients -= 1
                    log(f"[{o.name}] stream closed")
            else:
                self._json({"error": "not found"}, 404)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                body = self._body()
            except ValueError:
                return self._json({"error": "bad JSON"}, 400)
            name = q.get("name") or body.get("name")
            names = body.get("names")
            p = u.path
            if p in ("/api/speaker/enable", "/api/speaker/remove"):
                if not hub.out_cfg(name):
                    return self._json({"error": "no such speaker"}, 404)
            elif p != "/api/speaker/add" and name is not None and name not in hub.outputs:
                return self._json({"error": f"'{name}' isn't switched on"}, 404)
            locked = (
                "/api/trim",
                "/api/delay",
                "/api/latency",
                "/api/speaker/add",
                "/api/speaker/enable",
                "/api/speaker/remove",
                "/api/capture",
            )
            if hub.calibrating and p in locked:
                return self._json({"error": "calibrating — try again when it finishes"}, 409)
            try:
                if p == "/api/calibrate":
                    sel = names or ([name] if name else None)
                    if hub.calibrating:
                        return self._json({"error": "already calibrating"}, 409)
                    threading.Thread(target=hub.calibrate, args=(sel,), daemon=True).start()
                elif p == "/api/calibrate/cancel":
                    hub.cal_cancel = True
                elif p == "/api/trim":
                    hub.cfg["trim_ms"][name] = hub.cfg["trim_ms"].get(name, 0) + float(
                        q.get("ms", body.get("ms", 0))
                    )
                    hub.save()
                elif p == "/api/latency":
                    ms = float(q.get("ms", body.get("ms")))
                    hub.cfg["latency"][name] = max(0.0, ms / 1000)
                    hub.outputs[name].stale = False
                    hub.save()
                    log(f"[{name}] latency set by hand to {ms:.0f} ms")
                elif p == "/api/delay":
                    ms = q.get("ms", body.get("ms"))
                    if ms == "suggested":
                        hub.cfg["trim_ms"][name] = 0
                        hub.save()
                        log(f"[{name}] added delay reset to suggested")
                    else:
                        hub.set_added_delay(name, ms)
                        log(f"[{name}] added delay set by hand to {float(ms):.0f} ms")
                elif p == "/api/gain":
                    hub.cfg["gain"][name] = max(0.0, min(2.0, float(q.get("v", body.get("v", 1)))))
                    hub.save()
                elif p == "/api/synctest":
                    hub.sync_test(names or ([name] if name else None))
                elif p == "/api/restart":
                    hub.reconnect(name)
                elif p == "/api/speaker/add":
                    hub.add_speaker(body)
                elif p == "/api/speaker/enable":
                    hub.set_enabled(name, body.get("on", q.get("on") in ("1", "true")))
                elif p == "/api/speaker/remove":
                    hub.remove_speaker(name)
                elif p == "/api/mic":
                    hub.set_mic(body.get("device"))
                elif p == "/api/capture":
                    hub.set_capture(body.get("device"))
                else:
                    return self._json({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)}, 400)
            self._json(hub.state())

    return H
