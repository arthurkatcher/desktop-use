"""Unit tests for RemoteDesktop (httpx MockTransport, no live sandbox)."""

from __future__ import annotations

import json
import unittest

import httpx

from remote import RemoteDesktop, sanitize_remote_action


PNG = b"\x89PNG\r\n\x1a\n" + b"fake"


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestRemoteDesktop(unittest.TestCase):
    def test_health_sets_size_and_name(self):
        def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(req.url.path, "/health")
            return httpx.Response(200, json={
                "ok": True, "display": ":99",
                "width": 1024, "height": 768,
                "stream_ws": "ws://127.0.0.1:6080",
            })

        desk = RemoteDesktop("http://sandbox:7090/", http=client(handler))
        self.assertEqual(desk.base_url, "http://sandbox:7090")
        self.assertEqual(desk.name, ":99")
        self.assertEqual((desk.width, desk.height), (1024, 768))
        self.assertEqual(desk.stream_url, "ws://127.0.0.1:6080")
        desk.close()

    def test_stream_url_cli_overrides_health(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "ok": True, "width": 10, "height": 10,
                "stream_ws": "ws://from-health:6080",
            })

        desk = RemoteDesktop(
            "http://s", stream_url="ws://cli:6080",
            http=client(handler))
        self.assertEqual(desk.stream_url, "ws://cli:6080")
        desk.close()

    def test_whitespace_stream_url_falls_back_to_health(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "ok": True, "width": 10, "height": 10,
                "stream_ws": "ws://from-health:6080",
            })

        desk = RemoteDesktop(
            "http://s", stream_url="   ", http=client(handler))
        self.assertEqual(desk.stream_url, "ws://from-health:6080")
        desk.close()

    def test_default_size_when_missing(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        desk = RemoteDesktop("http://s", http=client(handler))
        self.assertEqual((desk.width, desk.height), (1280, 800))
        desk.close()

    def test_screenshot_png(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 1, "height": 1})
            if req.url.path == "/screenshot":
                return httpx.Response(200, content=PNG)
            return httpx.Response(404)

        desk = RemoteDesktop("http://s", http=client(handler))
        self.assertEqual(desk.screenshot_png(), PNG)
        desk.close()

    def test_execute_posts_action_json(self):
        seen: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 1, "height": 1})
            if req.url.path == "/action":
                seen["method"] = req.method
                seen["body"] = json.loads(req.content)
                return httpx.Response(
                    200, json={"ok": True, "result": '{"type":"click"}'})
            return httpx.Response(404)

        desk = RemoteDesktop("http://s", http=client(handler))
        action = {"type": "click", "x": 3, "y": 4}
        result = desk.execute(action)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"], action)
        self.assertIn("click", result)
        desk.close()

    def test_execute_raises_on_ok_false(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 1, "height": 1})
            return httpx.Response(
                200, json={"ok": False, "error": "bad coords"})

        desk = RemoteDesktop("http://s", http=client(handler))
        with self.assertRaises(RuntimeError) as ctx:
            desk.execute({"type": "click", "x": -1, "y": -1})
        self.assertIn("bad coords", str(ctx.exception))
        desk.close()

    def test_token_sent_on_all_routes(self):
        headers: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            headers.append({
                "auth": req.headers.get("Authorization"),
                "tok": req.headers.get("X-Sandbox-Token"),
                "path": req.url.path,
            })
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 1, "height": 1})
            if req.url.path == "/screenshot":
                return httpx.Response(200, content=PNG)
            if req.url.path == "/action":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        desk = RemoteDesktop(
            "http://s", token="sekret", http=client(handler))
        desk.screenshot_png()
        desk.execute({"type": "key", "combo": "Return"})
        desk.close()
        self.assertGreaterEqual(len(headers), 3)
        for h in headers:
            self.assertEqual(h["auth"], "Bearer sekret")
            self.assertEqual(h["tok"], "sekret")

    def test_no_token_omits_auth(self):
        def handler(req: httpx.Request) -> httpx.Response:
            self.assertIsNone(req.headers.get("Authorization"))
            self.assertIsNone(req.headers.get("X-Sandbox-Token"))
            return httpx.Response(
                200, json={"ok": True, "width": 1, "height": 1})

        desk = RemoteDesktop("http://s", http=client(handler))
        desk.close()

    def test_unhealthy_raises(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"ok": False, "error": "display down"})

        with self.assertRaises(RuntimeError):
            RemoteDesktop("http://s", http=client(handler))

    def test_agent_execute_routes_to_remote(self):
        """Module-level execute() must POST the whole action dict."""
        from agent import execute

        posted: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 100, "height": 50})
            if req.url.path == "/action":
                posted.append(json.loads(req.content))
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        desk = RemoteDesktop("http://s", http=client(handler))
        action = {"type": "click_type", "x": 1, "y": 2, "text": "hi"}
        result = execute(desk, action)
        self.assertEqual(posted, [action])
        self.assertIn("click_type", result)

        # wait stays local
        posted.clear()
        execute(desk, {"type": "wait", "seconds": 0})
        self.assertEqual(posted, [])
        desk.close()

    def test_extra_fields_stripped_from_post(self):
        """Security F2: unknown keys like cmd must never reach the sandbox."""
        posted: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 1, "height": 1})
            if req.url.path == "/action":
                posted.append(json.loads(req.content))
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        desk = RemoteDesktop("http://s", http=client(handler))
        desk.execute({
            "type": "click", "x": 1, "y": 2,
            "cmd": "id", "extra": True, "shell": "rm -rf /",
        })
        self.assertEqual(len(posted), 1)
        body = posted[0]
        self.assertEqual(body, {"type": "click", "x": 1, "y": 2})
        self.assertNotIn("cmd", body)
        self.assertNotIn("extra", body)
        self.assertNotIn("shell", body)
        desk.close()

    def test_sanitize_remote_action_per_type(self):
        self.assertEqual(
            sanitize_remote_action({
                "type": "type", "text": "hi", "cmd": "nope",
            }),
            {"type": "type", "text": "hi"})
        self.assertEqual(
            sanitize_remote_action({
                "type": "key", "combo": "Return", "x": 9,
            }),
            {"type": "key", "combo": "Return"})
        scroll = sanitize_remote_action({
            "type": "scroll", "direction": "up", "amount": 99999,
            "cmd": "x",
        })
        self.assertEqual(scroll["type"], "scroll")
        self.assertEqual(scroll["direction"], "up")
        self.assertEqual(scroll["amount"], 50)
        self.assertNotIn("cmd", scroll)


class TestCliArgs(unittest.TestCase):
    def test_agent_parser_sandbox_flags(self):
        src = open("agent.py").read()
        self.assertIn("--sandbox-url", src)
        self.assertIn("--stream-url", src)
        self.assertIn("--sandbox-token", src)
        self.assertIn("SANDBOX_URL", src)
        self.assertIn("RemoteDesktop", src)

    def test_ui_parser_sandbox_flags(self):
        src = open("ui.py").read()
        self.assertIn("--sandbox-url", src)
        self.assertIn("--stream-url", src)
        self.assertIn("RemoteDesktop", src)
        self.assertIn("__STREAM_URL__", src)
        self.assertIn("DESKTOP_SANDBOX_URL", src)
        self.assertIn("inject_stream_url", src)
        # Must not reintroduce the self-defeating replace token.
        self.assertNotIn("STREAM_URL_INJECT", src)

    def test_token_stripped(self):
        def handler(req: httpx.Request) -> httpx.Response:
            self.assertEqual(
                req.headers.get("Authorization"), "Bearer sekret")
            return httpx.Response(
                200, json={"ok": True, "width": 1, "height": 1})

        desk = RemoteDesktop(
            "http://s", token="  sekret  ", http=client(handler))
        desk.close()


if __name__ == "__main__":
    unittest.main()
