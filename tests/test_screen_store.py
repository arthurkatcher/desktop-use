"""ScreenStore power/lease/control with real files + real tiny health HTTP."""

from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory

from remote import probe_health
from screen_store import ScreenStore


class _HealthHandler(BaseHTTPRequestHandler):
    mode = "ok"  # ok | fail | auth | refuse

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        if self.mode == "auth":
            self.send_response(401)
            self.end_headers()
            return
        if self.mode == "fail":
            body = json.dumps({"ok": False, "error": "broken"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({
            "ok": True, "display": ":99",
            "width": 1280, "height": 800,
            "stream_ws": "ws://127.0.0.1:6080",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_health(mode="ok"):
    _HealthHandler.mode = mode
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    return srv, f"http://127.0.0.1:{port}"


class TestProbeHealth(unittest.TestCase):
    def test_ok(self):
        srv, url = start_health("ok")
        try:
            h = probe_health(url, timeout=2.0)
            self.assertTrue(h["ok"])
            self.assertEqual(h["width"], 1280)
        finally:
            srv.shutdown()

    def test_unreachable(self):
        h = probe_health("http://127.0.0.1:1", timeout=0.5)
        self.assertFalse(h["ok"])
        self.assertEqual(h.get("code"), "unreachable")

    def test_auth(self):
        srv, url = start_health("auth")
        try:
            h = probe_health(url, token="x", timeout=2.0)
            self.assertFalse(h["ok"])
            self.assertEqual(h.get("code"), "auth")
        finally:
            srv.shutdown()


class TestScreenStore(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.srv, self.url = start_health("ok")
        self.store = ScreenStore(self.tmp.name, health_fn=probe_health)

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def test_create_ok_saved_on(self):
        meta = self.store.create("Work", {"sandbox_url": self.url})
        self.assertEqual(meta["status"], "on")
        self.assertTrue(meta["health"]["ok"])
        self.assertIsNotNone(self.store.get(meta["id"]))

    def test_create_fail_not_saved(self):
        _HealthHandler.mode = "fail"
        with self.assertRaises(ValueError):
            self.store.create("Bad", {"sandbox_url": self.url})
        rows, total = self.store.list()
        self.assertEqual(total, 0)
        self.assertEqual(rows, [])

    def test_create_unreachable_not_saved(self):
        with self.assertRaises(ValueError):
            self.store.create("X", {"sandbox_url": "http://127.0.0.1:1"})
        self.assertEqual(self.store.list()[1], 0)

    def test_on_to_error_on_health_fail(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        _HealthHandler.mode = "fail"
        out = self.store.probe(meta["id"])
        self.assertEqual(out["status"], "error")
        self.assertFalse(out["health"]["ok"])

    def test_error_retry_ok(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        _HealthHandler.mode = "fail"
        self.store.probe(meta["id"])
        _HealthHandler.mode = "ok"
        out = self.store.turn_on(meta["id"])
        self.assertEqual(out["status"], "on")

    def test_soft_off_on(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        off = self.store.turn_off(meta["id"])
        self.assertEqual(off["status"], "off")
        on = self.store.turn_on(meta["id"])
        self.assertEqual(on["status"], "on")

    def test_refuse_off_while_leased(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.acquire_lease(meta["id"], "sess-1")
        with self.assertRaises(ValueError):
            self.store.turn_off(meta["id"])

    def test_lease_acquire_release(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        leased = self.store.acquire_lease(meta["id"], "s1")
        self.assertEqual(leased["lease"]["session_id"], "s1")
        self.assertEqual(leased["control"]["holder"], "ai")
        with self.assertRaises(ValueError):
            self.store.acquire_lease(meta["id"], "s2")
        free = self.store.release_lease(meta["id"], "s1")
        self.assertIsNone(free["lease"]["session_id"])
        self.assertEqual(free["control"]["holder"], "none")

    def test_refuse_lease_when_human_free(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.take_control(meta["id"], via="screens")
        with self.assertRaises(ValueError):
            self.store.acquire_lease(meta["id"], "s1")

    def test_refuse_lease_when_off(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.turn_off(meta["id"])
        with self.assertRaises(ValueError):
            self.store.acquire_lease(meta["id"], "s1")

    def test_idle_keeps_lease_control_none(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.acquire_lease(meta["id"], "s1")
        idle = self.store.set_control_idle(meta["id"], "s1")
        self.assertEqual(idle["lease"]["session_id"], "s1")
        self.assertEqual(idle["control"]["holder"], "none")

    def test_forbidden_free_ai_never_via_take(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        # free screen take only sets human
        taken = self.store.take_control(meta["id"])
        self.assertEqual(taken["control"]["holder"], "human")
        self.assertIsNone(taken["lease"]["session_id"])

    def test_take_ttl_expiry_free(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.take_control(meta["id"], ttl_s=1)
        changed = self.store.expire_human_control(now=time.time() + 5)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["control"]["holder"], "none")

    def test_take_ttl_expiry_leased_returns_ai(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.acquire_lease(meta["id"], "s1")
        self.store.take_control(meta["id"], session_id="s1", ttl_s=1)
        changed = self.store.expire_human_control(now=time.time() + 5)
        self.assertEqual(changed[0]["control"]["holder"], "ai")

    def test_ai_may_act_gate(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.acquire_lease(meta["id"], "s1")
        self.assertTrue(self.store.ai_may_act(meta["id"], "s1"))
        self.store.take_control(meta["id"], session_id="s1")
        self.assertFalse(self.store.ai_may_act(meta["id"], "s1"))
        self.assertFalse(self.store.ai_may_act(meta["id"], "other"))

    def test_pagination_filters(self):
        a = self.store.create("Alpha desk", {"sandbox_url": self.url})
        b = self.store.create("Beta desk", {"sandbox_url": self.url})
        self.store.turn_off(b["id"])
        rows, total = self.store.list(status="on")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["id"], a["id"])
        rows, total = self.store.list(q="beta")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["id"], b["id"])
        self.store.acquire_lease(a["id"], "s1")
        rows, total = self.store.list(lease="free")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["id"], b["id"])
        page, total = self.store.list(limit=1, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual(len(page), 1)

    def test_delete_refuses_leased(self):
        meta = self.store.create("W", {"sandbox_url": self.url})
        self.store.acquire_lease(meta["id"], "s1")
        with self.assertRaises(ValueError):
            self.store.delete(meta["id"])
        self.store.release_lease(meta["id"], "s1")
        self.assertTrue(self.store.delete(meta["id"]))
        self.assertIsNone(self.store.get(meta["id"]))


if __name__ == "__main__":
    unittest.main()
