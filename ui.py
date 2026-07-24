# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.27",
#     "python-xlib>=0.33",
# ]
# ///
"""Browser console for local-loop: sessions, live VM view, streaming dialogue.

Serves a home page listing every session (persisted under sessions/) with a
prompt to launch a new one, and a per-session console at /s/<id> where the
left pane streams the agent's reasoning (SSE) and the right pane shows the
live desktop (x11vnc + websockify + noVNC) or per-step snapshots.

    uv run ui.py                       # open the printed URL
    uv run ui.py "open a terminal..."  # also launch this task immediately

Everything (display, VNC, websockify, server) is torn down on Ctrl+C.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from agent import Desktop, ManagedEnv, ask_model, execute

NOVNC_DIR = "/usr/share/novnc"
HERE = os.path.dirname(os.path.abspath(__file__))
SESS_DIR = os.path.join(HERE, "sessions")

MIME = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
        ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
        ".json": "application/json", ".wasm": "application/wasm"}


class SessionStore:
    """One directory per session: meta.json, events.jsonl, <step>.png."""

    def __init__(self):
        os.makedirs(SESS_DIR, exist_ok=True)
        self.lock = threading.Lock()

    def _dir(self, sid: str) -> str:
        return os.path.join(SESS_DIR, sid)

    def create(self, task: str, model: str) -> dict:
        with self.lock:
            sid = time.strftime("%Y%m%d-%H%M%S")
            n = 0
            while os.path.exists(self._dir(sid)):
                n += 1
                sid = time.strftime("%Y%m%d-%H%M%S") + f"-{n}"
            os.makedirs(self._dir(sid))
            meta = {"id": sid, "task": task, "model": model,
                    "status": "running", "started": time.time(),
                    "ended": None, "steps": 0}
            self._write_meta(meta)
            return meta

    def _write_meta(self, meta: dict):
        path = os.path.join(self._dir(meta["id"]), "meta.json")
        with open(path, "w") as f:
            json.dump(meta, f)

    def meta(self, sid: str) -> dict | None:
        if os.sep in sid or sid.startswith("."):
            return None
        try:
            with open(os.path.join(self._dir(sid), "meta.json")) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def update(self, sid: str, **kw):
        with self.lock:
            meta = self.meta(sid)
            if meta:
                meta.update(kw)
                self._write_meta(meta)

    def list(self) -> list[dict]:
        metas = []
        for name in os.listdir(SESS_DIR):
            meta = self.meta(name)
            if meta:
                metas.append(meta)
        return sorted(metas, key=lambda m: m["started"], reverse=True)

    def append_event(self, sid: str, event: dict):
        with open(os.path.join(self._dir(sid), "events.jsonl"), "a") as f:
            f.write(json.dumps(event) + "\n")

    def events(self, sid: str) -> list[dict]:
        try:
            with open(os.path.join(self._dir(sid), "events.jsonl")) as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []

    def save_shot(self, sid: str, name: str, png: bytes):
        with open(os.path.join(self._dir(sid), f"{name}.png"), "wb") as f:
            f.write(png)

    def shot(self, sid: str, name: str) -> bytes | None:
        if os.sep in sid or os.sep in name:
            return None
        try:
            with open(os.path.join(self._dir(sid), f"{name}.png"), "rb") as f:
                return f.read()
        except OSError:
            return None


class Bus:
    """Fan-out of live events to SSE clients subscribed per session."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients: list[tuple[queue.Queue, str]] = []

    def emit(self, event: dict):
        with self.lock:
            targets = [q for q, sid in self.clients if sid == event.get("sid")]
        for q in targets:
            q.put(event)

    def subscribe(self, sid: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.clients.append((q, sid))
        return q

    def unsubscribe(self, q: queue.Queue):
        with self.lock:
            self.clients = [(cq, s) for cq, s in self.clients if cq is not q]


def _similar(a: dict | None, b: dict | None) -> bool:
    """Same action modulo a few pixels of coordinate jitter."""
    if not a or not b or a.get("type") != b.get("type"):
        return False
    if "x" in a and "x" in b:
        return (abs(int(a["x"]) - int(b["x"])) <= 6
                and abs(int(a["y"]) - int(b["y"])) <= 6)
    return a == b


class Runner:
    """Owns the desktop; executes one session at a time on it."""

    def __init__(self, desk: Desktop, bus: Bus, store: SessionStore, cfg):
        self.desk = desk
        self.bus = bus
        self.store = store
        self.cfg = cfg
        self.busy = threading.Lock()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()  # user holds the desktop
        self.msg_lock = threading.Lock()
        self.pending_msgs: list[str] = []     # mid-flight user messages
        self.active_sid: str | None = None

    def launch(self, task: str) -> str | None:
        if not self.busy.acquire(blocking=False):
            return None
        self.stop_event.clear()
        self.pause_event.clear()
        with self.msg_lock:
            self.pending_msgs = []
        meta = self.store.create(task, self.cfg.model)
        self.active_sid = meta["id"]
        threading.Thread(target=self._run, args=(meta["id"], task),
                         daemon=True).start()
        return meta["id"]

    def _emit(self, sid: str, seq: int, **event) -> int:
        event.update(ts=time.time(), sid=sid, seq=seq)
        self.store.append_event(sid, event)
        self.bus.emit(event)
        return seq + 1

    def _shot(self, sid: str, name: str, png: bytes) -> str:
        self.store.save_shot(sid, name, png)
        return f"/shot/{sid}/{name}.png"

    def _wait_control(self, sid: str, seq: int, history: list[str]) -> int:
        """Block while the user holds the desktop; note the handback."""
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.3)
        resumed = not self.stop_event.is_set()
        if resumed:
            history.append(
                "NOTE: the user took manual control of the desktop and may "
                "have changed windows, pages or state - re-examine the "
                "current screenshot carefully before acting.")
        return self._emit(sid, seq, t="control_returned", resumed=resumed)

    def _drain_msgs(self, sid: str, seq: int, history: list[str]) -> int:
        with self.msg_lock:
            msgs, self.pending_msgs = self.pending_msgs, []
        for text in msgs:
            history.append(
                f'USER MESSAGE (mid-task, takes precedence over earlier '
                f'plans): "{text}"')
            seq = self._emit(sid, seq, t="user_message", text=text)
        return seq

    def _run(self, sid: str, task: str):
        desk, cfg, store = self.desk, self.cfg, self.store
        seq = 0
        seq = self._emit(sid, seq, t="run_start", task=task,
                         max_steps=cfg.max_steps)
        history: list[str] = []
        prev_png: bytes | None = None
        last_action: dict | None = None
        repeats = 0
        status = "incomplete"
        try:
            with httpx.Client() as http:
                for step in range(1, cfg.max_steps + 1):
                    if self.pause_event.is_set():
                        seq = self._wait_control(sid, seq, history)
                    seq = self._drain_msgs(sid, seq, history)
                    if self.stop_event.is_set():
                        status = "stopped"
                        seq = self._emit(sid, seq, t="done", ok=False,
                                         summary="Stopped by you.")
                        return
                    png = desk.screenshot_png()
                    store.update(sid, steps=step)
                    seq = self._emit(sid, seq, t="step", n=step,
                                     shot=self._shot(sid, str(step), png))
                    decision = None
                    complaint = None
                    for attempt in (1, 2, 3):
                        try:
                            decision = ask_model(
                                http, cfg.base_url, cfg.api_key, cfg.model,
                                task, png, history,
                                (desk.width, desk.height), prev_png=prev_png,
                                complaint=complaint)
                            break
                        except (httpx.HTTPError, ValueError) as e:
                            complaint = str(e)
                            seq = self._emit(sid, seq, t="note", n=step,
                                             msg=f"model call failed "
                                                 f"(try {attempt}): {e}")
                    if decision is None:
                        status = "error"
                        seq = self._emit(sid, seq, t="error",
                                         msg="model failed 3 times, "
                                             "run aborted")
                        return

                    action = decision.get("action", {})
                    seq = self._emit(sid, seq, t="decision", n=step,
                                     reasoning=decision.get("reasoning", ""),
                                     action=action)

                    if action.get("type") == "done":
                        ok = bool(action.get("success"))
                        status = "complete" if ok else "incomplete"
                        seq = self._emit(sid, seq, t="done", ok=ok,
                                         summary=action.get("summary", ""),
                                         shot=self._shot(
                                             sid, "final",
                                             desk.screenshot_png()))
                        return
                    if self.stop_event.is_set():
                        status = "stopped"
                        seq = self._emit(sid, seq, t="skipped", n=step)
                        seq = self._emit(sid, seq, t="done", ok=False,
                                         summary="Stopped by you.")
                        return
                    if self.pause_event.is_set():
                        # user grabbed the desktop mid-decision: discard the
                        # pending action, wait, then re-read the screen
                        seq = self._emit(sid, seq, t="skipped", n=step)
                        seq = self._wait_control(sid, seq, history)
                        prev_png = png
                        continue
                    if self.pending_msgs:
                        # user message arrived mid-decision: discard the
                        # pending action, inject the message, re-decide
                        seq = self._emit(sid, seq, t="skipped", n=step)
                        seq = self._drain_msgs(sid, seq, history)
                        prev_png = png
                        continue

                    if _similar(action, last_action):
                        repeats += 1
                    else:
                        repeats = 0
                    last_action = action

                    execute(desk, action)
                    self.stop_event.wait(0.8)  # settle; wakes early on stop
                    after = desk.screenshot_png()
                    seq = self._emit(sid, seq, t="result", n=step,
                                     changed=after != png)
                    history.append(
                        f"step {step}: {json.dumps(action)} -> "
                        + ("screen changed" if after != png
                           else "screen did NOT change"))
                    if repeats >= 2:
                        history.append(
                            f"NOTE: you have sent this exact action "
                            f"{repeats + 1} times in a row and it is not "
                            "working. Change strategy: different "
                            "coordinates, keyboard navigation, or a "
                            "different UI path.")
                    prev_png = png
            seq = self._emit(sid, seq, t="done", ok=False,
                             summary=f"stopped after {cfg.max_steps} steps "
                                     "without finishing",
                             shot=self._shot(sid, "final",
                                             desk.screenshot_png()))
        except Exception as e:
            status = "error"
            seq = self._emit(sid, seq, t="error", msg=str(e))
        finally:
            seq = self._emit(sid, seq, t="run_end")
            self.store.update(sid, status=status, ended=time.time())
            self.active_sid = None
            self.busy.release()


def make_handler(bus: Bus, runner: Runner, store: SessionStore, cfg):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _page(self, name: str):
            with open(os.path.join(HERE, name), "rb") as f:
                page = f.read()
            self._send(200, page.replace(b"__WS_PORT__",
                                         str(cfg.ws_port).encode()),
                       "text/html")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._page("home.html")
            elif url.path.startswith("/s/"):
                sid = url.path[len("/s/"):]
                if store.meta(sid) is None:
                    self._send(404, b"unknown session", "text/plain")
                else:
                    self._page("ui.html")
            elif url.path == "/sessions":
                rows = store.list()
                for row in rows:
                    row["active"] = row["id"] == runner.active_sid
                self._send(200, json.dumps(rows).encode(), "application/json")
            elif url.path == "/events":
                sid = parse_qs(url.query).get("sid", [""])[0]
                self._sse(sid)
            elif url.path.startswith("/shot/"):
                parts = url.path[len("/shot/"):].removesuffix(".png").split("/")
                png = store.shot(*parts) if len(parts) == 2 else None
                if png is None:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, png, "image/png")
            elif url.path.startswith("/novnc/"):
                rel = os.path.normpath(url.path[len("/novnc/"):])
                full = os.path.join(NOVNC_DIR, rel)
                if rel.startswith("..") or not os.path.isfile(full):
                    self._send(404, b"not found", "text/plain")
                    return
                ext = os.path.splitext(full)[1]
                with open(full, "rb") as f:
                    self._send(200, f.read(),
                               MIME.get(ext, "application/octet-stream"))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/message":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    text = str(json.loads(self.rfile.read(length))
                               ["text"]).strip()
                    assert text
                except Exception:
                    self._send(400, b'{"error":"bad request"}',
                               "application/json")
                    return
                sid = runner.active_sid
                if sid:
                    with runner.msg_lock:
                        runner.pending_msgs.append(text)
                    bus.emit({"t": "message_sent", "sid": sid,
                              "text": text, "ts": time.time()})
                self._send(200, json.dumps({"queued": sid is not None})
                           .encode(), "application/json")
                return
            if self.path == "/control/take":
                sid = runner.active_sid
                if sid:
                    runner.pause_event.set()
                    bus.emit({"t": "control_taken", "sid": sid,
                              "ts": time.time()})
                self._send(200, json.dumps({"paused": sid is not None})
                           .encode(), "application/json")
                return
            if self.path == "/control/release":
                length = int(self.headers.get("Content-Length", 0))
                try:
                    resume = bool(json.loads(self.rfile.read(length))
                                  .get("continue", True))
                except Exception:
                    resume = True
                if not resume:
                    runner.stop_event.set()
                runner.pause_event.clear()
                self._send(200, b'{"ok":true}', "application/json")
                return
            if self.path == "/stop":
                sid = runner.active_sid
                if sid:
                    runner.stop_event.set()
                    bus.emit({"t": "stop_requested", "sid": sid,
                              "ts": time.time()})
                self._send(200, b'{"ok":true}', "application/json")
                return
            if self.path != "/run":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length))
                task = str(payload["task"]).strip()
                assert task
            except Exception:
                self._send(400, b'{"error":"bad request"}', "application/json")
                return
            sid = runner.launch(task)
            if sid is None:
                body = json.dumps({"error": "a session is already running",
                                   "active": runner.active_sid})
                self._send(409, body.encode(), "application/json")
            else:
                self._send(200, json.dumps({"id": sid}).encode(),
                           "application/json")

        def _sse_write(self, event: dict):
            self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.flush()

        def _sse(self, sid: str):
            meta = store.meta(sid)
            if meta is None:
                self._send(404, b"unknown session", "text/plain")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            live = runner.active_sid == sid
            q = bus.subscribe(sid) if live else None
            try:
                self._sse_write({"t": "hello", "model": meta["model"],
                                 "display": runner.desk.name,
                                 "width": runner.desk.width,
                                 "height": runner.desk.height,
                                 "max_steps": cfg.max_steps, "live": live,
                                 "status": meta["status"]})
                max_seq = -1
                for event in store.events(sid):
                    max_seq = max(max_seq, event.get("seq", -1))
                    self._sse_write(event)
                if q is None:
                    self._sse_write({"t": "eof"})
                while True:
                    if q is None:
                        time.sleep(15)
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    try:
                        event = q.get(timeout=15)
                        if "seq" not in event or event["seq"] > max_seq:
                            self._sse_write(event)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if q is not None:
                    bus.unsubscribe(q)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?", default="",
                        help="launch this task as a session on startup")
    parser.add_argument("--base-url",
                        default=os.environ.get("OPENAI_BASE_URL",
                                               "http://localhost:11434/v1"))
    parser.add_argument("--model",
                        default=os.environ.get("LOCAL_LOOP_MODEL", "qwen2.5vl"))
    parser.add_argument("--api-key",
                        default=os.environ.get("OPENAI_API_KEY", "local"))
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--port", type=int, default=7788)
    parser.add_argument("--vnc-port", type=int, default=5900)
    parser.add_argument("--ws-port", type=int, default=6080)
    cfg = parser.parse_args()

    if not os.path.isdir(NOVNC_DIR):
        sys.exit(f"noVNC not found at {NOVNC_DIR} (apt install novnc)")

    procs: list[subprocess.Popen] = []
    with ManagedEnv() as display:
        try:
            vnc_env = {k: v for k, v in os.environ.items()
                       if k != "WAYLAND_DISPLAY"}
            vnc_env["XDG_SESSION_TYPE"] = "x11"  # or x11vnc refuses to start
            procs.append(subprocess.Popen(
                ["x11vnc", "-display", display, "-localhost", "-nopw",
                 "-shared", "-forever", "-quiet",
                 "-rfbport", str(cfg.vnc_port)],
                env=vnc_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            procs.append(subprocess.Popen(
                ["websockify", str(cfg.ws_port),
                 f"localhost:{cfg.vnc_port}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.0)

            desk = Desktop(display)
            bus = Bus()
            store = SessionStore()
            runner = Runner(desk, bus, store, cfg)

            server = ThreadingHTTPServer(
                ("127.0.0.1", cfg.port),
                make_handler(bus, runner, store, cfg))
            print(f"console: http://localhost:{cfg.port}  (Ctrl+C to stop)")
            if cfg.task:
                sid = runner.launch(cfg.task)
                print(f"session: http://localhost:{cfg.port}/s/{sid}")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nshutting down")
        finally:
            for proc in reversed(procs):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()


if __name__ == "__main__":
    main()
