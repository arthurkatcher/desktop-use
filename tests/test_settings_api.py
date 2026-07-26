"""Real HTTP against make_handler for settings (Part B)."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx

from desktop_use import ui as ui_mod
from desktop_use.settings_store import SettingsStore


class FakeDesk:
    name = "fake"
    width = 100
    height = 80
    is_remote = False

    def screenshot_png(self):
        return b"png"

    def close(self):
        pass


def start_console(tmp_sess, tmp_settings):
    ui_mod.SESS_DIR = tmp_sess
    store = ui_mod.SessionStore()
    settings = SettingsStore(tmp_settings)
    bus = ui_mod.Bus()
    cfg = SimpleNamespace(
        model="m", base_url="http://x/v1", api_key="k",
        model_backend="auto", max_steps=5, idle_timeout=60,
        sandbox_url=None, stream_url=None, ws_port=6080,
    )
    runner = ui_mod.Runner(FakeDesk(), bus, store, cfg, settings=settings)
    handler = ui_mod.make_handler(
        bus, runner, store, cfg, screens=None, settings=settings)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    return srv, f"http://127.0.0.1:{port}", settings


class TestSettingsAPI(unittest.TestCase):
    def setUp(self):
        self.td_sess = TemporaryDirectory()
        self.td_set = TemporaryDirectory()
        path = self.td_set.name + "/settings.json"
        self.srv, self.base, self.settings = start_console(
            self.td_sess.name, path)

    def tearDown(self):
        self.srv.shutdown()
        self.td_sess.cleanup()
        self.td_set.cleanup()

    def test_get_defaults(self):
        r = httpx.get(f"{self.base}/api/settings")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["max_steps"], 15)
        self.assertIn("api_key_set", d)

    def test_put_and_get(self):
        r = httpx.put(f"{self.base}/api/settings", json={
            "model": "new-model",
            "max_steps": 9,
            "control_ttl_s": 30,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["model"], "new-model")
        g = httpx.get(f"{self.base}/api/settings").json()
        self.assertEqual(g["model"], "new-model")
        self.assertEqual(g["max_steps"], 9)
        self.assertEqual(g["control_ttl_s"], 30)


if __name__ == "__main__":
    unittest.main()
