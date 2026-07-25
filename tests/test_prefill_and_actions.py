"""Prefill slug matrix, spawn rejection, display refuse, API errors."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

import httpx

from agent import (
    AGENT_ACTION_TYPES,
    _is_real_display,
    _supports_assistant_prefill,
    ask_model,
    execute,
)
from remote import RemoteDesktop


class TestPrefill(unittest.TestCase):
    def test_claude5_skips_prefill(self):
        for slug in (
            "anthropic/claude-sonnet-5",
            "claude-opus-5",
            "claude-haiku-5",
            "sonnet-5-preview",
            "anthropic/claude-sonnet-5.0",
            "claude-5-sonnet",
        ):
            self.assertFalse(
                _supports_assistant_prefill(slug),
                f"expected no prefill for {slug}")

    def test_claude45_and_others_keep_prefill(self):
        for slug in (
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-sonnet-4.5",
            "claude-opus-4",
            "qwen2.5vl",
            "openai/gpt-4o",
            "google/gemini-2.0-flash",
        ):
            self.assertTrue(
                _supports_assistant_prefill(slug),
                f"expected prefill for {slug}")


class TestDisplayRefuse(unittest.TestCase):
    def test_real_displays_blocked(self):
        for d in (":0", ":1", ":0.0", ":0.1", ":1.0", "localhost:0.0"):
            self.assertTrue(_is_real_display(d), d)

    def test_nested_displays_allowed(self):
        for d in (":2", ":99", ":10.0", "localhost:2"):
            self.assertFalse(_is_real_display(d), d)


class TestSpawnRejected(unittest.TestCase):
    def test_spawn_not_in_agent_types(self):
        self.assertNotIn("spawn", AGENT_ACTION_TYPES)

    def test_execute_ignores_spawn_without_post(self):
        desk = MagicMock()
        desk.is_remote = True
        desk.execute = MagicMock(return_value="should-not-run")
        result = execute(desk, {"type": "spawn", "cmd": "id"})
        self.assertIn("unknown", result)
        desk.execute.assert_not_called()

    def test_remote_desktop_refuses_spawn(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 1, "height": 1})
            if req.url.path == "/action":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        desk = RemoteDesktop(
            "http://s",
            http=httpx.Client(transport=httpx.MockTransport(handler)))
        with self.assertRaises(RuntimeError) as ctx:
            desk.execute({"type": "spawn", "cmd": "id"})
        self.assertIn("spawn", str(ctx.exception).lower())
        desk.close()

    def test_remote_whole_action_click_type_still_posts(self):
        posted: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/health":
                return httpx.Response(
                    200, json={"ok": True, "width": 10, "height": 10})
            if req.url.path == "/action":
                posted.append(json.loads(req.content))
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        desk = RemoteDesktop(
            "http://s",
            http=httpx.Client(transport=httpx.MockTransport(handler)))
        action = {"type": "click_type", "x": 1, "y": 2, "text": "hi"}
        execute(desk, action)
        self.assertEqual(posted, [action])
        desk.close()


class TestAskModelErrors(unittest.TestCase):
    def test_non_json_error_becomes_value_error(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        http = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(ValueError) as ctx:
            ask_model(
                http, "http://api/v1", "k", "qwen2.5vl",
                "task", b"\x89PNG", [], (10, 10))
        self.assertIn("502", str(ctx.exception))
        self.assertIn("bad gateway", str(ctx.exception))
        http.close()

    def test_json_error_message_extracted(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"error": {"message": "prefill not allowed"}})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(ValueError) as ctx:
            ask_model(
                http, "http://api/v1", "k", "qwen2.5vl",
                "task", b"\x89PNG", [], (10, 10))
        self.assertIn("prefill not allowed", str(ctx.exception))
        http.close()


if __name__ == "__main__":
    unittest.main()
