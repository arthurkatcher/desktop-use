"""Real HTTP: screens create/list/power/control with tiny health server."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx

import ui as ui_mod
from remote import probe_health
from screen_store import ScreenStore
from settings_store import SettingsStore


class HealthOK(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            "ok": True, "width": 10, "height": 10,
            "stream_ws": "ws://x",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeDesk:
    name = "fake"
    width = 100
    height = 80
    is_remote = False

    def close(self):
        pass


def start_health():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), HealthOK)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def start_console(tmp):
    ui_mod.SESS_DIR = tmp + "/sess"
    screens = ScreenStore(tmp + "/screens", health_fn=probe_health)
    settings = SettingsStore(tmp + "/settings.json")
    bus = ui_mod.Bus()
    store = ui_mod.SessionStore()
    cfg = SimpleNamespace(
        model="m", base_url="http://x/v1", api_key="k",
        model_backend="auto", max_steps=5, idle_timeout=60,
        sandbox_url=None, stream_url=None, ws_port=6080,
    )
    runner = ui_mod.Runner(
        FakeDesk(), bus, store, cfg, screens=screens, settings=settings)
    handler = ui_mod.make_handler(
        bus, runner, store, cfg, screens, settings)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class TestScreensAPI(unittest.TestCase):
    def setUp(self):
        self.td = TemporaryDirectory()
        self.hs, self.hurl = start_health()
        self.srv, self.base = start_console(self.td.name)

    def tearDown(self):
        self.srv.shutdown()
        self.hs.shutdown()
        self.td.cleanup()

    def test_create_and_list(self):
        r = httpx.post(f"{self.base}/api/screens", json={
            "name": "A",
            "connection": {"sandbox_url": self.hurl},
        })
        self.assertEqual(r.status_code, 201, r.text)
        sid = r.json()["id"]
        g = httpx.get(f"{self.base}/api/screens/{sid}")
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.json()["status"], "on")
        listing = httpx.get(f"{self.base}/api/screens?limit=10").json()
        self.assertEqual(listing["total"], 1)

    def test_create_bad_url(self):
        r = httpx.post(f"{self.base}/api/screens", json={
            "name": "B",
            "connection": {"sandbox_url": "http://127.0.0.1:1"},
        })
        self.assertEqual(r.status_code, 400)
        listing = httpx.get(f"{self.base}/api/screens").json()
        self.assertEqual(listing["total"], 0)

    def test_control_take_release_free(self):
        sid = httpx.post(f"{self.base}/api/screens", json={
            "name": "C",
            "connection": {"sandbox_url": self.hurl},
        }).json()["id"]
        t = httpx.post(
            f"{self.base}/api/screens/{sid}/control/take",
            json={"via": "screens", "ttl_s": 60})
        self.assertEqual(t.status_code, 200)
        self.assertEqual(t.json()["control"]["holder"], "human")
        rel = httpx.post(
            f"{self.base}/api/screens/{sid}/control/release",
            json={"continue": True})
        self.assertEqual(rel.json()["control"]["holder"], "none")

    def test_off_on(self):
        sid = httpx.post(f"{self.base}/api/screens", json={
            "name": "D",
            "connection": {"sandbox_url": self.hurl},
        }).json()["id"]
        off = httpx.post(f"{self.base}/api/screens/{sid}/off")
        self.assertEqual(off.json()["status"], "off")
        on = httpx.post(f"{self.base}/api/screens/{sid}/on")
        self.assertEqual(on.json()["status"], "on")


if __name__ == "__main__":
    unittest.main()
