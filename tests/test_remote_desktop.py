"""Unit tests for RemoteDesktop HTTP mapping (mock transport, no sandbox)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("remote")
from remote import RemoteDesktop


def _mock_server(token: str = "", use_v1: bool = True):
    state = {"actions": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _auth(self):
            if not token:
                return True
            auth = self.headers.get("Authorization", "")
            return (
                self.headers.get("X-Sandbox-Token") == token
                or auth == f"Bearer {token}"
            )

        def do_GET(self):
            if not self._auth():
                self.send_response(401)
                self.end_headers()
                return
            path = self.path.split("?", 1)[0]
            if path == "/health":
                body = json.dumps({
                    "ok": True, "display": ":99",
                    "width": 1280, "height": 800,
                    "stream_ws": "ws://127.0.0.1:6080/websockify",
                    "browser": None,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path in ("/v1/screenshot", "/screenshot"):
                if use_v1 and path == "/screenshot":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = b"\x89PNG\r\n\x1a\nshot"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path in ("/v1/size", "/size"):
                body = json.dumps({"width": 1280, "height": 800}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if not self._auth():
                self.send_response(401)
                self.end_headers()
                return
            path = self.path.split("?", 1)[0]
            if path not in ("/v1/action", "/action"):
                self.send_response(404)
                self.end_headers()
                return
            if use_v1 and path == "/action":
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get("Content-Length", 0))
            action = json.loads(self.rfile.read(n))
            state["actions"].append(action)
            body = json.dumps({
                "ok": True, "result": json.dumps(action), "action": action,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, state


def test_remote_port_methods_map_to_http():
    srv, port, state = _mock_server()
    try:
        desk = RemoteDesktop(f"http://127.0.0.1:{port}")
        assert desk.width == 1280
        assert desk.height == 800
        assert desk.screenshot_png().startswith(b"\x89PNG")
        desk.move(10, 20)
        desk.click(1, 2)
        desk.click(3, 4, times=2)
        desk.click(5, 6, button=3)
        desk.type_text("hi")
        desk.key_combo("Return")
        desk.scroll("down", 2)
        types = [a["type"] for a in state["actions"]]
        assert types == [
            "move", "click", "double_click", "right_click",
            "type", "key", "scroll",
        ]
        desk.close()
    finally:
        srv.shutdown()


def test_remote_token_required():
    srv, port, _ = _mock_server(token="t0")
    try:
        with pytest.raises(Exception):
            RemoteDesktop(f"http://127.0.0.1:{port}")
        desk = RemoteDesktop(f"http://127.0.0.1:{port}", token="t0")
        assert desk.width == 1280
        desk.close()
    finally:
        srv.shutdown()


def test_remote_legacy_paths_fallback():
    srv, port, state = _mock_server(use_v1=False)
    try:
        desk = RemoteDesktop(f"http://127.0.0.1:{port}")
        assert desk.screenshot_png().startswith(b"\x89PNG")
        desk.move(0, 0)
        assert state["actions"][0]["type"] == "move"
        desk.close()
    finally:
        srv.shutdown()


def test_execute_shared_dispatcher_uses_port():
    """agent.execute should call DesktopPort methods on RemoteDesktop."""
    import agent as agent_mod

    srv, port, state = _mock_server()
    try:
        desk = RemoteDesktop(f"http://127.0.0.1:{port}")
        agent_mod.execute(desk, {"type": "click", "x": 7, "y": 8})
        agent_mod.execute(desk, {"type": "type", "text": "a"})
        assert any(a["type"] == "click" for a in state["actions"])
        assert any(a.get("text") == "a" for a in state["actions"])
        desk.close()
    finally:
        srv.shutdown()
