"""Regression tests for remote stream URL injection into session.html."""

from __future__ import annotations

import os
import re
import unittest

from desktop_use.ui import inject_stream_url, _safe_session_part, SessionStore

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_HTML = os.path.join(
    HERE, "desktop_use", "static", "session.html")
SESSION_JS = os.path.join(
    HERE, "desktop_use", "static", "js", "session.js")


class TestStreamInject(unittest.TestCase):
    def setUp(self):
        with open(SESSION_HTML, "rb") as f:
            self.page = f.read()

    def test_placeholder_present_once_as_value(self):
        # Only the JS value token should appear (not also in a includes-check).
        self.assertEqual(self.page.count(b"__STREAM_URL__"), 1)
        self.assertIn(b"streamUrl: '__STREAM_URL__'", self.page)
        self.assertIn(b"wsPort: '__WS_PORT__'", self.page)
        # Old self-defeating token must not return.
        self.assertNotIn(b"STREAM_URL_INJECT", self.page)

    def test_guard_lives_in_session_js(self):
        with open(SESSION_JS) as f:
            js = f.read()
        self.assertIn("startsWith('__')", js)
        self.assertIn("window.DU.streamUrl", js)
        self.assertIn("window.DU.wsPort", js)
        # The js file ships with no tokens; injection is page-side only.
        self.assertNotIn("__STREAM_URL__", js)
        self.assertNotIn("__WS_PORT__", js)

    def test_remote_inject_uses_stream_not_ws_port(self):
        stream = "ws://127.0.0.1:6080/websockify"
        out = inject_stream_url(
            self.page, stream, ws_port=9999, remote=True).decode()
        # Value is substituted; guard no longer sees the placeholder.
        self.assertIn(f"streamUrl: '{stream}'", out)
        self.assertNotIn("__STREAM_URL__", out)
        # Local port still substituted for the fallback in the js module.
        self.assertIn("wsPort: '9999'", out)
        # Simulated JS guard: injected does not start with __ → use remote.
        injected = stream
        chosen = injected if (injected and not injected.startswith("__")) \
            else "ws://localhost:9999"
        self.assertEqual(chosen, stream)

    def test_local_mode_never_injects_even_if_stream_set(self):
        out = inject_stream_url(
            self.page,
            "ws://evil:6080/websockify",
            ws_port=6080,
            remote=False,
        ).decode()
        self.assertIn("streamUrl: '__STREAM_URL__'", out)
        self.assertNotIn("ws://evil", out)
        # Fallback still gets the local port.
        self.assertIn("wsPort: '6080'", out)

    def test_remote_empty_stream_keeps_placeholder(self):
        out = inject_stream_url(
            self.page, "", ws_port=6080, remote=True).decode()
        self.assertIn("streamUrl: '__STREAM_URL__'", out)
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
        self.assertIn(r"streamUrl: 'ws://x/\';alert(1)//'", out)
        # Unescaped breakout would look like: '...';alert... (quote ends early).
        self.assertNotRegex(out, r"streamUrl: '[^'\\]*';alert")


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
