"""Regression tests for remote stream URL injection into ui.html."""

from __future__ import annotations

import os
import re
import unittest

from ui import inject_stream_url, _safe_session_part, SessionStore

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_HTML = os.path.join(HERE, "ui.html")


class TestStreamInject(unittest.TestCase):
    def setUp(self):
        with open(UI_HTML, "rb") as f:
            self.page = f.read()

    def test_placeholder_present_once_as_value(self):
        # Only the JS value token should appear (not also in a includes-check).
        self.assertEqual(self.page.count(b"__STREAM_URL__"), 1)
        self.assertIn(b"const injected = '__STREAM_URL__';", self.page)
        self.assertIn(b"startsWith('__')", self.page)
        # Old self-defeating token must not return.
        self.assertNotIn(b"STREAM_URL_INJECT", self.page)

    def test_remote_inject_uses_stream_not_ws_port(self):
        stream = "ws://127.0.0.1:6080/websockify"
        out = inject_stream_url(
            self.page, stream, ws_port=9999, remote=True).decode()
        # Value is substituted; guard no longer sees the placeholder.
        self.assertIn(f"const injected = '{stream}';", out)
        self.assertNotIn("__STREAM_URL__", out)
        # Local port still substituted for the fallback template, but the
        # chosen streamUrl must be the remote one (guard passes).
        m = re.search(
            r"const injected = '([^']*)';\s*"
            r"const streamUrl = \(injected && !injected\.startsWith\('__'\)\)\s*"
            r"\? injected\s*"
            r": `ws://\$\{location\.hostname\}:(\d+)`",
            out,
        )
        self.assertIsNotNone(m, "streamUrl assignment pattern missing")
        self.assertEqual(m.group(1), stream)
        self.assertEqual(m.group(2), "9999")
        # Simulated JS guard: injected does not start with __ → use remote.
        injected = m.group(1)
        chosen = injected if (injected and not injected.startswith("__")) \
            else f"ws://localhost:{m.group(2)}"
        self.assertEqual(chosen, stream)

    def test_local_mode_never_injects_even_if_stream_set(self):
        out = inject_stream_url(
            self.page,
            "ws://evil:6080/websockify",
            ws_port=6080,
            remote=False,
        ).decode()
        self.assertIn("const injected = '__STREAM_URL__';", out)
        self.assertNotIn("ws://evil", out)
        # Fallback still gets the local port.
        self.assertIn(":6080`", out)

    def test_remote_empty_stream_keeps_placeholder(self):
        out = inject_stream_url(
            self.page, "", ws_port=6080, remote=True).decode()
        self.assertIn("const injected = '__STREAM_URL__';", out)
        injected = "__STREAM_URL__"
        chosen = injected if (injected and not injected.startswith("__")) \
            else "ws://localhost:6080"
        self.assertEqual(chosen, "ws://localhost:6080")

    def test_js_escapes_quotes_in_stream_url(self):
        # A malicious stream_url must not break out of the JS string.
        evil = "ws://x/';alert(1)//"
        out = inject_stream_url(
            self.page, evil, ws_port=1, remote=True).decode()
        # Escaped form keeps a single single-quoted literal.
        self.assertIn(r"const injected = 'ws://x/\';alert(1)//';", out)
        # Unescaped breakout would look like: '...';alert... (quote ends early).
        self.assertNotRegex(out, r"const injected = '[^'\\]*';alert")


class TestPathSafety(unittest.TestCase):
    def test_safe_session_part(self):
        self.assertTrue(_safe_session_part("20260724-204623"))
        self.assertTrue(_safe_session_part("1"))
        self.assertFalse(_safe_session_part(""))
        self.assertFalse(_safe_session_part("."))
        self.assertFalse(_safe_session_part(".."))
        self.assertFalse(_safe_session_part("../etc"))
        self.assertFalse(_safe_session_part(".hidden"))
        self.assertFalse(_safe_session_part("a/b"))
        self.assertFalse(_safe_session_part("a\\b"))

    def test_shot_rejects_traversal(self):
        store = SessionStore()
        self.assertIsNone(store.shot("..", "passwd"))
        self.assertIsNone(store.shot("../x", "1"))
        self.assertIsNone(store.meta(".."))
        self.assertIsNone(store.meta(".git"))


if __name__ == "__main__":
    unittest.main()
