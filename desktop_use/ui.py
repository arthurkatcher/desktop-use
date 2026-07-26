"""Browser console for desktop-use: sessions, screens, live VM view.

Serves a console home (sessions / screens / settings), a per-session view
at /s/<id> (SSE transcript + live desktop or snapshots), and a per-screen
view at /screen/<id> (live stream + operator control). Pages and their
css/js live in desktop_use/static/.

    uv run python -m desktop_use.ui                       # open printed URL
    uv run python -m desktop_use.ui "open a terminal..."  # launch task too

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

from .agent import (
    Desktop, ManagedEnv, ask_model, execute, require_binaries,
)
from .model_backends import resolve_model_backend
from .remote import probe_health
from .screen_store import ScreenStore
from .settings_store import SettingsStore, public_settings

NOVNC_DIR = "/usr/share/novnc"
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG_DIR)
STATIC_DIR = os.path.join(PKG_DIR, "static")
SESS_DIR = os.path.join(ROOT, "sessions")
SCREENS_DIR = os.path.join(ROOT, "screens")
SETTINGS_PATH = os.path.join(ROOT, "settings.json")

MIME = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
        ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
        ".json": "application/json", ".wasm": "application/wasm"}


def _safe_session_part(part: str) -> bool:
    """Reject empty, path separators, dots, and traversal components."""
    if not part or part in (".", "..") or part.startswith("."):
        return False
    if os.sep in part or (os.altsep and os.altsep in part):
        return False
    if "/" in part or "\\" in part:
        return False
    return True


def _js_str_escape(s: str) -> str:
    """Escape a string for embedding inside a single-quoted JS literal."""
    return (s.replace("\\", "\\\\")
             .replace("'", "\\'")
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("</", "<\\/"))


def inject_stream_url(page: bytes, stream_url: str | None,
                      ws_port: int, remote: bool) -> bytes:
    """Substitute page tokens for local ws port and optional remote stream.

    Only injects stream_url when remote is True (sandbox-url mode). The
    placeholder token ``__STREAM_URL__`` appears once as a JS string value;
    the client checks ``!injected.startsWith('__')`` so a real URL can never
    re-trigger the empty-placeholder guard.
    """
    page = page.replace(b"__WS_PORT__", str(ws_port).encode())
    if remote and (stream_url or "").strip():
        safe = _js_str_escape(stream_url.strip())
        page = page.replace(b"__STREAM_URL__", safe.encode())
    return page


class SessionStore:
    """One directory per session: meta.json, events.jsonl, <step>.png."""

    def __init__(self):
        os.makedirs(SESS_DIR, exist_ok=True)
        self.lock = threading.Lock()

    def _dir(self, sid: str) -> str:
        return os.path.join(SESS_DIR, sid)

    def _under_sess(self, path: str) -> bool:
        root = os.path.realpath(SESS_DIR)
        real = os.path.realpath(path)
        return real == root or real.startswith(root + os.sep)

    def create(self, task: str, model: str,
               screen_id: str | None = None) -> dict:
        with self.lock:
            sid = time.strftime("%Y%m%d-%H%M%S")
            n = 0
            while os.path.exists(self._dir(sid)):
                n += 1
                sid = time.strftime("%Y%m%d-%H%M%S") + f"-{n}"
            os.makedirs(self._dir(sid))
            meta = {"id": sid, "task": task, "model": model,
                    "status": "running", "started": time.time(),
                    "ended": None, "steps": 0,
                    "screen_id": screen_id}
            self._write_meta(meta)
            return meta

    def _write_meta(self, meta: dict):
        path = os.path.join(self._dir(meta["id"]), "meta.json")
        with open(path, "w") as f:
            json.dump(meta, f)

    def meta(self, sid: str) -> dict | None:
        if not _safe_session_part(sid):
            return None
        try:
            path = os.path.join(self._dir(sid), "meta.json")
            if not self._under_sess(path):
                return None
            with open(path) as f:
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
        try:
            names = os.listdir(SESS_DIR)
        except OSError:
            names = []
        for name in names:
            meta = self.meta(name)
            if meta:
                metas.append(meta)
        return sorted(metas, key=lambda m: m["started"], reverse=True)

    def list_page(self, *, limit: int | None = None, offset: int = 0,
                  status: str | None = None,
                  q: str | None = None) -> tuple[list[dict], int]:
        rows = self.list()
        if status:
            rows = [r for r in rows if r.get("status") == status]
        if q:
            ql = q.lower()
            rows = [r for r in rows if (
                ql in (r.get("task") or "").lower()
                or ql in (r.get("id") or "").lower()
            )]
        total = len(rows)
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[: max(0, int(limit))]
        return rows, total

    def append_event(self, sid: str, event: dict):
        with open(os.path.join(self._dir(sid), "events.jsonl"), "a") as f:
            f.write(json.dumps(event) + "\n")

    def events(self, sid: str) -> list[dict]:
        if not _safe_session_part(sid):
            return []
        try:
            with open(os.path.join(self._dir(sid), "events.jsonl")) as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []

    def save_shot(self, sid: str, name: str, png: bytes):
        with open(os.path.join(self._dir(sid), f"{name}.png"), "wb") as f:
            f.write(png)

    def shot(self, sid: str, name: str) -> bytes | None:
        if not _safe_session_part(sid) or not _safe_session_part(name):
            return None
        try:
            path = os.path.join(self._dir(sid), f"{name}.png")
            if not self._under_sess(path):
                return None
            with open(path, "rb") as f:
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


# Models often jitter title-bar / icon clicks by more than a few px between
# identical attempts (session 20260725-140943: ~8-13px). Keep this loose.
_SIMILAR_XY_PX = 15


def _similar(a: dict | None, b: dict | None) -> bool:
    """Same action modulo coordinate jitter (see _SIMILAR_XY_PX)."""
    if not a or not b or a.get("type") != b.get("type"):
        return False
    if "x" in a and "x" in b and "y" in a and "y" in b:
        return (abs(int(a["x"]) - int(b["x"])) <= _SIMILAR_XY_PX
                and abs(int(a["y"]) - int(b["y"])) <= _SIMILAR_XY_PX)
    return a == b


class Runner:
    """Owns the desktop; executes one session at a time on it.

    A session stays open after the agent emits ``done``: status becomes
    ``idle`` and the message bar can queue a follow-up. The session only
    ends on Stop, hard error, or idle timeout (default 60s since the
    last desktop action or user message).
    """

    def __init__(self, desk, bus: Bus, store: SessionStore, cfg,
                 screens: ScreenStore | None = None,
                 settings: SettingsStore | None = None):
        self.desk = desk  # boot desk (local or CLI sandbox); may be None later
        self.bus = bus
        self.store = store
        self.cfg = cfg
        self.screens = screens
        self.settings = settings
        self.busy = threading.Lock()
        self.stop_event = threading.Event()   # abort: status stopped
        self.end_event = threading.Event()    # clean close: status complete
        self.pause_event = threading.Event()  # user holds the desktop
        self.msg_lock = threading.Lock()
        self.pending_msgs: list[str] = []     # mid-flight user messages
        self.wake_event = threading.Event()   # message / control wake idle
        self.active_sid: str | None = None
        self.active_screen_id: str | None = None
        self._session_desk = None  # per-run desk override

    def _effective_run_cfg(self) -> dict:
        """Defaults from settings for model/max_steps/idle when set."""
        out = {
            "model": self.cfg.model,
            "base_url": self.cfg.base_url,
            "api_key": self.cfg.api_key,
            "model_backend": getattr(self.cfg, "model_backend", "auto"),
            "max_steps": self.cfg.max_steps,
            "idle_timeout": float(
                getattr(self.cfg, "idle_timeout", 60) or 60),
        }
        if self.settings is None:
            return out
        s = self.settings.get()
        # Prefer settings for next launch defaults (CLI still sets process
        # defaults at boot; settings.json updates apply without restart).
        if s.get("model"):
            out["model"] = s["model"]
        if s.get("base_url"):
            out["base_url"] = s["base_url"]
        if s.get("model_backend"):
            out["model_backend"] = s["model_backend"]
        if s.get("max_steps"):
            out["max_steps"] = int(s["max_steps"])
        if s.get("idle_timeout") is not None:
            out["idle_timeout"] = float(s["idle_timeout"])
        key = (s.get("api_key") or "").strip()
        if key:
            out["api_key"] = key
        return out

    def launch(self, task: str, screen_id: str | None = None) -> str | None:
        if not self.busy.acquire(blocking=False):
            return None
        self.stop_event.clear()
        self.end_event.clear()
        self.pause_event.clear()
        self.wake_event.clear()
        with self.msg_lock:
            self.pending_msgs = []
        run_cfg = self._effective_run_cfg()
        session_desk = self.desk
        acquired_screen = None
        sid = None
        try:
            if screen_id and self.screens is not None:
                probed = self.screens.probe(screen_id)
                if not probed or probed.get("status") != "on":
                    raise ValueError(
                        "screen not healthy/on; fix or pick another")
            meta = self.store.create(
                task, run_cfg["model"], screen_id=screen_id)
            sid = meta["id"]
            if screen_id and self.screens is not None:
                self.screens.acquire_lease(screen_id, sid)
                acquired_screen = screen_id
                sm = self.screens.get(screen_id)
                conn = (sm or {}).get("connection") or {}
                from .remote import RemoteDesktop
                session_desk = RemoteDesktop(
                    conn.get("sandbox_url") or "",
                    token=conn.get("token") or "",
                    stream_url=conn.get("stream_url"),
                )
            elif self.desk is None:
                raise RuntimeError(
                    "no desktop: provide screen_id or start with "
                    "--sandbox-url / local mode")
        except Exception:
            if acquired_screen and self.screens is not None:
                try:
                    self.screens.release_lease(acquired_screen, sid)
                except Exception:
                    pass
            if sid:
                try:
                    self.store.update(
                        sid, status="error", ended=time.time())
                except Exception:
                    pass
            self.busy.release()
            raise

        self.active_sid = sid
        self.active_screen_id = screen_id
        self._session_desk = session_desk
        self._run_overrides = run_cfg
        threading.Thread(target=self._run, args=(sid, task),
                         daemon=True).start()
        return sid

    def _user_close(self) -> str | None:
        """If the operator closed the session: ``stopped`` or ``ended``."""
        if self.stop_event.is_set():
            return "stopped"
        if self.end_event.is_set():
            return "ended"
        return None

    def _settle(self, seconds: float = 0.8) -> None:
        """Wait for action settle, returning early on Stop/End."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._user_close():
                return
            time.sleep(min(0.1, max(0.0, deadline - time.time())))

    def _emit(self, sid: str, seq: int, **event) -> int:
        event.update(ts=time.time(), sid=sid, seq=seq)
        self.store.append_event(sid, event)
        self.bus.emit(event)
        return seq + 1

    def _shot(self, sid: str, name: str, png: bytes) -> str:
        self.store.save_shot(sid, name, png)
        return f"/shot/{sid}/{name}.png"

    def _idle_timeout_s(self) -> float:
        ov = getattr(self, "_run_overrides", None) or {}
        if "idle_timeout" in ov:
            return float(ov["idle_timeout"] or 60)
        return float(getattr(self.cfg, "idle_timeout", 60) or 60)

    def _sync_pause_from_screen(self) -> None:
        """Bridge screen.control.holder == human → pause_event."""
        if not self.screens or not self.active_screen_id:
            return
        self.screens.expire_human_control()
        meta = self.screens.get(self.active_screen_id)
        if not meta:
            return
        holder = (meta.get("control") or {}).get("holder")
        if holder == "human":
            self.pause_event.set()
        else:
            if self.pause_event.is_set() and holder in ("ai", "none"):
                self.pause_event.clear()

    def _wait_control(self, sid: str, seq: int, history: list[str]) -> int:
        """Block while the user holds the desktop; note the handback.

        Re-syncs from the screen registry each tick: a human hold that
        expires via TTL must release the runner too, not only an
        explicit /control/release.
        """
        while self.pause_event.is_set() and not self._user_close():
            self._sync_pause_from_screen()
            time.sleep(0.3)
        resumed = not self._user_close()
        if resumed:
            history.append(
                "NOTE: the user took manual control of the desktop and may "
                "have changed windows, pages or state - re-examine the "
                "current screenshot carefully before acting.")
        return self._emit(sid, seq, t="control_returned", resumed=resumed)

    def _drain_msgs(self, sid: str, seq: int,
                    history: list[str]) -> tuple[int, list[str]]:
        with self.msg_lock:
            msgs, self.pending_msgs = self.pending_msgs, []
        for text in msgs:
            history.append(
                f'USER MESSAGE (mid-task, takes precedence over earlier '
                f'plans): "{text}"')
            seq = self._emit(sid, seq, t="user_message", text=text)
        return seq, msgs

    def _wait_idle(self, sid: str, seq: int, history: list[str],
                   last_activity: float) -> tuple[int, str, float]:
        """Park until a user message, Stop/End, or idle timeout.

        Returns ``(seq, reason, last_activity)`` with reason one of
        ``resume`` | ``stopped`` | ``ended`` | ``timeout``.
        """
        timeout = self._idle_timeout_s()
        self.store.update(sid, status="idle")
        seq = self._emit(sid, seq, t="idle", timeout_s=timeout)
        while True:
            close = self._user_close()
            if close:
                return seq, close, last_activity
            now = time.time()
            if now - last_activity >= timeout:
                return seq, "timeout", last_activity
            if self.pause_event.is_set():
                seq = self._wait_control(sid, seq, history)
                last_activity = time.time()
                close = self._user_close()
                if close:
                    return seq, close, last_activity
                # still idle after handback unless a message arrived
                self.store.update(sid, status="idle")
                seq = self._emit(sid, seq, t="idle", timeout_s=timeout)
                continue
            with self.msg_lock:
                has_msg = bool(self.pending_msgs)
            if has_msg:
                self.store.update(sid, status="running")
                history.append(
                    "NOTE: previous objective was parked (done or step "
                    "budget). New user message(s) are the current objective "
                    "— re-examine the screenshot carefully before acting.")
                seq = self._emit(sid, seq, t="resumed")
                return seq, "resume", time.time()
            remaining = timeout - (now - last_activity)
            self.wake_event.wait(timeout=min(0.5, max(0.05, remaining)))
            self.wake_event.clear()

    def _run(self, sid: str, task: str):
        desk = self._session_desk or self.desk
        cfg = self.cfg
        store = self.store
        ov = getattr(self, "_run_overrides", None) or {}
        model = ov.get("model", cfg.model)
        base_url = ov.get("base_url", cfg.base_url)
        api_key = ov.get("api_key", cfg.api_key)
        backend_flag = ov.get(
            "model_backend", getattr(cfg, "model_backend", "auto"))
        max_steps = int(ov.get("max_steps", cfg.max_steps))
        screen_id = self.active_screen_id
        seq = 0
        resolved = resolve_model_backend(base_url, model, backend_flag)
        idle_s = self._idle_timeout_s()
        seq = self._emit(sid, seq, t="run_start", task=task,
                         max_steps=max_steps, backend=resolved,
                         idle_timeout=idle_s, screen_id=screen_id)
        history: list[str] = []
        prev_png: bytes | None = None
        last_action: dict | None = None
        repeats = 0
        no_change_streak = 0
        status = "incomplete"
        last_ok: bool | None = None
        step = 0
        last_activity = time.time()
        try:
            with httpx.Client() as http:
                while True:
                    # One active burst: up to max_steps until done / stop /
                    # error. Then park idle instead of ending the session.
                    burst_end = None  # "done" | "budget" | "stop" | "error"
                    for _ in range(max_steps):
                        self._sync_pause_from_screen()
                        if self.pause_event.is_set():
                            seq = self._wait_control(sid, seq, history)
                            last_activity = time.time()
                        seq, _ = self._drain_msgs(sid, seq, history)
                        close = self._user_close()
                        if close:
                            status, ok, summary = (
                                ("stopped", False, "Stopped by you.")
                                if close == "stopped"
                                else ("complete", True,
                                      "Session ended by you."))
                            seq = self._emit(
                                sid, seq, t="done", ok=ok, terminal=True,
                                reason=close, summary=summary)
                            return
                        step += 1
                        # A human grab via the screens API may have landed
                        # after the sync above: honor it at the boundary.
                        scr_meta = (self.screens.get(screen_id)
                                    if self.screens and screen_id else None)
                        if ((scr_meta or {}).get("control") or {}
                                ).get("holder") == "human":
                            self.pause_event.set()
                            seq = self._emit(sid, seq, t="skipped", n=step)
                            seq = self._wait_control(sid, seq, history)
                            last_activity = time.time()
                            continue
                        png = desk.screenshot_png()
                        store.update(sid, steps=step, status="running")
                        if self.screens and screen_id:
                            self.screens.set_control_ai(screen_id, sid)
                        seq = self._emit(sid, seq, t="step", n=step,
                                         shot=self._shot(sid, str(step), png))
                        decision = None
                        complaint = None
                        for attempt in (1, 2, 3):
                            try:
                                decision = ask_model(
                                    http, base_url, api_key,
                                    model, task, png, history,
                                    (desk.width, desk.height),
                                    prev_png=prev_png, complaint=complaint,
                                    backend=backend_flag)
                                break
                            except (httpx.HTTPError, ValueError) as e:
                                complaint = str(e)
                                seq = self._emit(
                                    sid, seq, t="note", n=step,
                                    msg=f"model call failed "
                                        f"(try {attempt}): {e}")
                        if decision is None:
                            status = "error"
                            seq = self._emit(
                                sid, seq, t="error",
                                msg="model failed 3 times, run aborted")
                            return

                        action = decision.get("action", {})
                        seq = self._emit(
                            sid, seq, t="decision", n=step,
                            reasoning=decision.get("reasoning", ""),
                            action=action)

                        if action.get("type") == "done":
                            ok = bool(action.get("success"))
                            last_ok = ok
                            last_activity = time.time()
                            # task milestone only — session parks idle next
                            seq = self._emit(
                                sid, seq, t="done", ok=ok, terminal=False,
                                reason="task",
                                summary=action.get("summary", ""),
                                shot=self._shot(sid, "final",
                                                desk.screenshot_png()))
                            burst_end = "done"
                            break
                        close = self._user_close()
                        if close:
                            status, ok, summary = (
                                ("stopped", False, "Stopped by you.")
                                if close == "stopped"
                                else ("complete", True,
                                      "Session ended by you."))
                            seq = self._emit(sid, seq, t="skipped", n=step)
                            seq = self._emit(
                                sid, seq, t="done", ok=ok, terminal=True,
                                reason=close, summary=summary)
                            return
                        self._sync_pause_from_screen()
                        if self.pause_event.is_set():
                            # user grabbed the desktop mid-decision: discard
                            # the pending action, wait, then re-read screen
                            seq = self._emit(sid, seq, t="skipped", n=step)
                            seq = self._wait_control(sid, seq, history)
                            last_activity = time.time()
                            prev_png = png
                            continue
                        if self.pending_msgs:
                            # user message mid-decision: discard, re-decide
                            seq = self._emit(sid, seq, t="skipped", n=step)
                            seq, msgs = self._drain_msgs(
                                sid, seq, history)
                            last_activity = time.time()
                            if msgs:
                                task = msgs[-1]
                            prev_png = png
                            continue

                        if (self.screens and screen_id
                                and not self.screens.ai_may_act(
                                    screen_id, sid)):
                            seq = self._emit(sid, seq, t="skipped", n=step)
                            seq = self._wait_control(sid, seq, history)
                            last_activity = time.time()
                            prev_png = png
                            continue

                        if _similar(action, last_action):
                            repeats += 1
                        else:
                            repeats = 0
                        last_action = action

                        execute(desk, action)
                        last_activity = time.time()
                        self._settle(0.8)  # settle; early on Stop/End
                        after = desk.screenshot_png()
                        changed = after != png
                        seq = self._emit(sid, seq, t="result", n=step,
                                         changed=changed)
                        history.append(
                            f"step {step}: {json.dumps(action)} -> "
                            + ("screen changed" if changed
                               else "screen did NOT change"))
                        if changed:
                            no_change_streak = 0
                        else:
                            no_change_streak += 1
                        if repeats >= 2:
                            history.append(
                                f"NOTE: you have sent a near-identical "
                                f"action {repeats + 1} times in a row and "
                                "it is not working. Change strategy: "
                                "different coordinates (not a few-pixel "
                                "nudge), keyboard navigation (e.g. alt+F4 "
                                "to close a window), or a different UI path.")
                        elif no_change_streak >= 2:
                            history.append(
                                f"NOTE: the screen did not change for the "
                                f"last {no_change_streak} actions. Stop "
                                "micro-adjusting the same click. Try a "
                                "keyboard shortcut (alt+F4 to close the "
                                "focused window, ctrl+l for the address "
                                "bar, Escape to dismiss menus) or a clearly "
                                "different on-screen control.")
                        prev_png = png
                    else:
                        # max_steps for this burst, no done — park, not end
                        burst_end = "budget"
                        last_activity = time.time()
                        seq = self._emit(
                            sid, seq, t="note",
                            msg=(f"step budget ({max_steps}) reached "
                                 "— idle. Send a message to continue, "
                                 "End to finish, or Stop to abort."))

                    if burst_end is None:
                        # defensive: loop exited without setting reason
                        burst_end = "budget"

                    if self.screens and screen_id:
                        self.screens.set_control_idle(screen_id, sid)
                    seq, reason, last_activity = self._wait_idle(
                        sid, seq, history, last_activity)
                    if reason == "stopped":
                        status = "stopped"
                        seq = self._emit(
                            sid, seq, t="done", ok=False, terminal=True,
                            reason="stopped", summary="Stopped by you.")
                        return
                    if reason == "ended":
                        status = "complete"
                        seq = self._emit(
                            sid, seq, t="done", ok=True, terminal=True,
                            reason="ended",
                            summary="Session ended by you.",
                            shot=self._shot(sid, "final",
                                            desk.screenshot_png()))
                        return
                    if reason == "timeout":
                        # clean park-out, not success/error
                        status = "ended"
                        to_s = int(self._idle_timeout_s())
                        to_label = (f"{to_s // 60} min" if to_s >= 60
                                    else f"{to_s}s")
                        seq = self._emit(
                            sid, seq, t="done", ok=False, terminal=True,
                            reason="idle_timeout",
                            summary=(f"Idle timeout ({to_label} since last "
                                     "action) — session ended."),
                            shot=self._shot(sid, "final",
                                            desk.screenshot_png()))
                        return
                    # resume: inject queued messages and keep going
                    if self.screens and screen_id:
                        self.screens.set_control_ai(screen_id, sid)
                    seq, msgs = self._drain_msgs(sid, seq, history)
                    last_activity = time.time()
                    if msgs:
                        task = msgs[-1]
                    repeats = 0
                    no_change_streak = 0
                    last_action = None
                    prev_png = None
        except Exception as e:
            status = "error"
            seq = self._emit(sid, seq, t="error", msg=str(e))
        finally:
            seq = self._emit(sid, seq, t="run_end")
            self.store.update(sid, status=status, ended=time.time())
            if self.screens and screen_id:
                try:
                    self.screens.release_lease(screen_id, sid)
                except Exception:
                    pass
            if (self._session_desk is not None
                    and self._session_desk is not self.desk
                    and hasattr(self._session_desk, "close")):
                try:
                    self._session_desk.close()
                except Exception:
                    pass
            self._session_desk = None
            self.active_sid = None
            self.active_screen_id = None
            self.busy.release()


def make_handler(bus: Bus, runner: Runner, store: SessionStore, cfg,
                 screens: ScreenStore | None = None,
                 settings: SettingsStore | None = None):
    screens = screens or getattr(runner, "screens", None)
    settings = settings or getattr(runner, "settings", None)

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

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _read_json(self):
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def _page(self, name: str, stream_url: str | None = None):
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                page = f.read()
            remote = bool(getattr(cfg, "sandbox_url", None) or stream_url)
            stream = (stream_url
                      or (getattr(cfg, "stream_url", None) or "")).strip()
            page = inject_stream_url(
                page, stream if remote else None, cfg.ws_port, remote)
            self._send(200, page, "text/html")

        def _qs(self, url):
            return {k: v[0] for k, v in parse_qs(url.query).items() if v}

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            qs = self._qs(url)
            if path in ("/", "/index.html"):
                self._page("home.html")
            elif path.startswith("/s/"):
                sid = path[len("/s/"):]
                meta = store.meta(sid)
                if meta is None:
                    self._send(404, b"unknown session", "text/plain")
                else:
                    stream = None
                    if meta.get("screen_id") and screens:
                        sm = screens.get(meta["screen_id"])
                        if sm:
                            stream = ((sm.get("connection") or {})
                                      .get("stream_url")
                                      or (sm.get("health") or {})
                                      .get("stream_ws"))
                    self._page("session.html", stream_url=stream)
            elif path.startswith("/screen/"):
                scr_id = path[len("/screen/"):]
                sm = screens.get(scr_id) if screens else None
                if sm is None:
                    self._send(404, b"unknown screen", "text/plain")
                else:
                    with open(os.path.join(STATIC_DIR, "screen.html"),
                              "rb") as f:
                        page = f.read()
                    # Only this screen's own stream; never the boot stream.
                    page = inject_stream_url(
                        page,
                        (sm.get("connection") or {}).get("stream_url")
                        or (sm.get("health") or {}).get("stream_ws"),
                        cfg.ws_port, True)
                    self._send(200, page, "text/html")
            elif path in ("/sessions", "/api/sessions"):
                limit = qs.get("limit")
                offset = int(qs.get("offset") or 0)
                status = qs.get("status") or None
                q = qs.get("q") or None
                lim = int(limit) if limit not in (None, "") else None
                rows, total = store.list_page(
                    limit=lim, offset=offset, status=status, q=q)
                for row in rows:
                    row["active"] = row["id"] == runner.active_sid
                # legacy /sessions: bare list when no pagination params
                if path == "/sessions" and limit is None and offset == 0 \
                        and not status and not q:
                    self._json(200, rows)
                else:
                    self._json(200, {
                        "items": rows, "total": total,
                        "limit": lim, "offset": offset,
                    })
            elif path == "/api/settings":
                if settings is None:
                    self._json(503, {"error": "settings unavailable"})
                else:
                    self._json(200, public_settings(settings.get()))
            elif path == "/api/screens":
                if screens is None:
                    self._json(503, {"error": "screens unavailable"})
                    return
                limit = qs.get("limit")
                lim = int(limit) if limit not in (None, "") else 10
                offset = int(qs.get("offset") or 0)
                rows, total = screens.list(
                    limit=lim, offset=offset,
                    status=qs.get("status") or None,
                    lease=qs.get("lease") or None,
                    q=qs.get("q") or None)
                self._json(200, {
                    "items": rows, "total": total,
                    "limit": lim, "offset": offset,
                })
            elif path.startswith("/api/screens/") and path.endswith(
                    "/stream-info"):
                sid = path[len("/api/screens/"):-len("/stream-info")]
                if not screens:
                    self._json(503, {"error": "screens unavailable"})
                    return
                meta = screens.get(sid)
                if not meta:
                    self._json(404, {"error": "unknown screen"})
                    return
                stream = ((meta.get("connection") or {}).get("stream_url")
                          or (meta.get("health") or {}).get("stream_ws"))
                self._json(200, {
                    "stream_url": stream,
                    "sandbox_url": (meta.get("connection") or {})
                    .get("sandbox_url"),
                })
            elif path.startswith("/api/screens/"):
                sid = path[len("/api/screens/"):]
                if "/" in sid or not screens:
                    self._json(404, {"error": "not found"})
                    return
                meta = screens.get(sid)
                if not meta:
                    self._json(404, {"error": "unknown screen"})
                else:
                    self._json(200, meta)
            elif path == "/events":
                sid = parse_qs(url.query).get("sid", [""])[0]
                self._sse(sid)
            elif path.startswith("/shot/"):
                parts = path[len("/shot/"):].removesuffix(".png").split("/")
                png = store.shot(*parts) if len(parts) == 2 else None
                if png is None:
                    self._send(404, b"not found", "text/plain")
                else:
                    self._send(200, png, "image/png")
            elif path.startswith("/static/"):
                rel = path[len("/static/"):]
                if not rel or rel.startswith("/") or "\\" in rel:
                    self._send(404, b"not found", "text/plain")
                    return
                rel = os.path.normpath(rel)
                if rel.startswith("..") or os.path.isabs(rel):
                    self._send(404, b"not found", "text/plain")
                    return
                root = os.path.realpath(STATIC_DIR)
                full = os.path.realpath(os.path.join(STATIC_DIR, rel))
                if not (full == root or full.startswith(root + os.sep)):
                    self._send(404, b"not found", "text/plain")
                    return
                if not os.path.isfile(full):
                    self._send(404, b"not found", "text/plain")
                    return
                ext = os.path.splitext(full)[1]
                with open(full, "rb") as f:
                    self._send(200, f.read(),
                               MIME.get(ext, "application/octet-stream"))
            elif path.startswith("/novnc/"):
                rel = path[len("/novnc/"):]
                if not rel or rel.startswith("/") or "\\" in rel:
                    self._send(404, b"not found", "text/plain")
                    return
                rel = os.path.normpath(rel)
                if rel.startswith("..") or os.path.isabs(rel):
                    self._send(404, b"not found", "text/plain")
                    return
                root = os.path.realpath(NOVNC_DIR)
                full = os.path.realpath(os.path.join(NOVNC_DIR, rel))
                if not (full == root or full.startswith(root + os.sep)):
                    self._send(404, b"not found", "text/plain")
                    return
                if not os.path.isfile(full):
                    self._send(404, b"not found", "text/plain")
                    return
                ext = os.path.splitext(full)[1]
                with open(full, "rb") as f:
                    self._send(200, f.read(),
                               MIME.get(ext, "application/octet-stream"))
            else:
                self._send(404, b"not found", "text/plain")

        def do_PUT(self):
            if self.path == "/api/settings":
                if settings is None:
                    self._json(503, {"error": "settings unavailable"})
                    return
                try:
                    payload = self._read_json()
                    data = settings.save(payload)
                    self._json(200, public_settings(data))
                except ValueError as e:
                    self._json(400, {"error": str(e)})
                except Exception:
                    self._json(400, {"error": "bad request"})
                return
            self._send(404, b"not found", "text/plain")

        def do_PATCH(self):
            if not self.path.startswith("/api/screens/") or not screens:
                self._send(404, b"not found", "text/plain")
                return
            sid = self.path[len("/api/screens/"):]
            if "/" in sid:
                self._send(404, b"not found", "text/plain")
                return
            try:
                payload = self._read_json()
                kw = {}
                if "name" in payload:
                    kw["name"] = str(payload["name"]).strip()
                if "connection" in payload:
                    kw["connection"] = payload["connection"]
                if "profile" in payload:
                    kw["profile"] = payload["profile"]
                meta = screens.update_fields(sid, **kw)
                if meta is None:
                    self._json(404, {"error": "unknown screen"})
                else:
                    self._json(200, meta)
            except Exception as e:
                self._json(400, {"error": str(e)})

        def do_DELETE(self):
            if not self.path.startswith("/api/screens/") or not screens:
                self._send(404, b"not found", "text/plain")
                return
            sid = self.path[len("/api/screens/"):]
            if "/" in sid:
                self._send(404, b"not found", "text/plain")
                return
            try:
                ok = screens.delete(sid)
                if not ok:
                    self._json(404, {"error": "unknown screen"})
                else:
                    self._json(200, {"ok": True})
            except ValueError as e:
                self._json(409, {"error": str(e)})

        def do_POST(self):
            path = self.path
            if path == "/message":
                try:
                    text = str(self._read_json()["text"]).strip()
                    assert text
                except Exception:
                    self._json(400, {"error": "bad request"})
                    return
                sid = runner.active_sid
                if sid:
                    with runner.msg_lock:
                        runner.pending_msgs.append(text)
                    runner.wake_event.set()
                    bus.emit({"t": "message_sent", "sid": sid,
                              "text": text, "ts": time.time()})
                self._json(200, {"queued": sid is not None})
                return
            if path == "/control/take":
                sid = runner.active_sid
                screen_id = runner.active_screen_id
                if sid:
                    runner.pause_event.set()
                    runner.wake_event.set()
                    expires = None
                    if screens and screen_id:
                        ttl = 120
                        if settings:
                            ttl = int(settings.get().get(
                                "control_ttl_s") or 120)
                        try:
                            sm = screens.take_control(
                                screen_id, via="session",
                                session_id=sid, ttl_s=ttl)
                            expires = (sm.get("control") or {}).get(
                                "expires_at")
                        except Exception:
                            pass
                    bus.emit({"t": "control_taken", "sid": sid,
                              "ts": time.time(), "screen_id": screen_id,
                              "expires_at": expires})
                self._json(200, {
                    "paused": sid is not None,
                    "screen_id": screen_id,
                })
                return
            if path == "/control/release":
                try:
                    body = self._read_json()
                    resume = bool(body.get("continue", True))
                except Exception:
                    resume = True
                sid = runner.active_sid
                screen_id = runner.active_screen_id
                if not resume:
                    runner.stop_event.set()
                if screens and screen_id:
                    try:
                        screens.release_control(
                            screen_id, resume_ai=resume and bool(sid))
                    except Exception:
                        pass
                runner.pause_event.clear()
                runner.wake_event.set()
                self._json(200, {"ok": True})
                return
            if path == "/stop":
                sid = runner.active_sid
                if sid:
                    runner.stop_event.set()
                    runner.wake_event.set()
                    bus.emit({"t": "stop_requested", "sid": sid,
                              "ts": time.time()})
                self._json(200, {"ok": True})
                return
            if path == "/end":
                sid = runner.active_sid
                if sid:
                    runner.end_event.set()
                    runner.wake_event.set()
                    bus.emit({"t": "end_requested", "sid": sid,
                              "ts": time.time()})
                self._json(200, {"ok": True})
                return
            if path == "/api/settings/preset":
                if settings is None:
                    self._json(503, {"error": "settings unavailable"})
                    return
                try:
                    pid = str(self._read_json()["id"])
                    data = settings.apply_preset(pid)
                    self._json(200, public_settings(data))
                except KeyError as e:
                    self._json(404, {"error": str(e)})
                except Exception as e:
                    self._json(400, {"error": str(e)})
                return
            if path == "/api/screens" and screens is not None:
                try:
                    payload = self._read_json()
                    name = str(payload.get("name") or "").strip()
                    connection = payload.get("connection") or {
                        "sandbox_url": payload.get("sandbox_url"),
                        "stream_url": payload.get("stream_url"),
                        "token": payload.get("token") or "",
                        "mode": "external",
                    }
                    ttl = 120
                    if settings:
                        ttl = int(settings.get().get("control_ttl_s") or 120)
                    meta = screens.create(
                        name, connection,
                        profile=payload.get("profile"),
                        ttl_s=ttl)
                    self._json(201, meta)
                except ValueError as e:
                    self._json(400, {"error": str(e)})
                except Exception as e:
                    self._json(400, {"error": str(e)})
                return
            if screens and path.startswith("/api/screens/"):
                rest = path[len("/api/screens/"):]
                parts = rest.split("/")
                sid = parts[0]
                action = parts[1] if len(parts) > 1 else ""
                try:
                    if action == "on":
                        self._json(200, screens.turn_on(sid))
                        return
                    if action == "off":
                        self._json(200, screens.turn_off(sid))
                        return
                    if action == "health":
                        out = screens.probe(sid)
                        if out is None:
                            self._json(404, {"error": "unknown screen"})
                        else:
                            self._json(200, out)
                        return
                    if action == "control" and len(parts) >= 3:
                        sub = parts[2]
                        body = {}
                        try:
                            body = self._read_json()
                        except Exception:
                            body = {}
                        if sub == "take":
                            ttl = body.get("ttl_s")
                            if ttl is None and settings:
                                ttl = settings.get().get("control_ttl_s")
                            meta = screens.take_control(
                                sid, via=body.get("via") or "ui",
                                session_id=body.get("session_id"),
                                ttl_s=ttl)
                            # if leased to active session, pause runner
                            lease = (meta.get("lease") or {}).get(
                                "session_id")
                            if (lease and lease == runner.active_sid
                                    and runner.active_screen_id == sid):
                                runner.pause_event.set()
                                runner.wake_event.set()
                                bus.emit({
                                    "t": "control_taken", "sid": lease,
                                    "ts": time.time(), "screen_id": sid,
                                    "expires_at": (meta.get("control") or {}
                                                   ).get("expires_at"),
                                })
                            self._json(200, meta)
                            return
                        if sub == "release":
                            cont = bool(body.get("continue", True))
                            meta = screens.release_control(
                                sid, resume_ai=cont)
                            if (runner.active_screen_id == sid
                                    and runner.active_sid):
                                if not cont:
                                    runner.stop_event.set()
                                runner.pause_event.clear()
                                runner.wake_event.set()
                            self._json(200, meta)
                            return
                except KeyError:
                    self._json(404, {"error": "unknown screen"})
                    return
                except ValueError as e:
                    self._json(409, {"error": str(e)})
                    return
                except Exception as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(404, {"error": "not found"})
                return
            if path != "/run":
                self._send(404, b"not found", "text/plain")
                return
            try:
                payload = self._read_json()
                task = str(payload["task"]).strip()
                assert task
                screen_id = payload.get("screen_id") or None
                if screen_id:
                    screen_id = str(screen_id)
            except Exception:
                self._json(400, {"error": "bad request"})
                return
            # default screen from settings when screens registered
            if not screen_id and settings and screens:
                ds = settings.get().get("default_screen_id")
                if ds and screens.get(ds):
                    screen_id = ds
            try:
                sid = runner.launch(task, screen_id=screen_id)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:
                self._json(500, {"error": str(e)})
                return
            if sid is None:
                self._json(409, {
                    "error": "a session is already running",
                    "active": runner.active_sid,
                })
            else:
                self._json(200, {"id": sid, "screen_id": screen_id})

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
                desk = (runner._session_desk if live and runner._session_desk
                        else runner.desk)
                remote = bool(
                    getattr(cfg, "sandbox_url", None)
                    or meta.get("screen_id")
                    or getattr(desk, "is_remote", False))
                dname = getattr(desk, "name", "?") if desk else "?"
                dwidth = getattr(desk, "width", 1280) if desk else 1280
                dheight = getattr(desk, "height", 800) if desk else 800
                hello = {"t": "hello", "model": meta["model"],
                         "display": dname,
                         "width": dwidth,
                         "height": dheight,
                         "max_steps": cfg.max_steps, "live": live,
                         "status": meta["status"],
                         "idle_timeout": float(
                             getattr(cfg, "idle_timeout", 60) or 60),
                         "mode": "remote" if remote else "local",
                         "screen_id": meta.get("screen_id")}
                stream = (getattr(cfg, "stream_url", None) or "").strip()
                if meta.get("screen_id") and screens:
                    sm = screens.get(meta["screen_id"])
                    if sm:
                        stream = (
                            (sm.get("connection") or {}).get("stream_url")
                            or (sm.get("health") or {}).get("stream_ws")
                            or stream or "")
                        stream = (stream or "").strip()
                if remote and stream:
                    hello["stream_url"] = stream
                self._sse_write(hello)
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
                        default=(os.environ.get("OPENAI_API_KEY")
                                 or os.environ.get("HAI_API_KEY")
                                 or "local"))
    parser.add_argument(
        "--model-backend",
        choices=("auto", "generic", "holo"),
        default=(os.environ.get("MODEL_BACKEND")
                 or os.environ.get("DESKTOP_USE_MODEL_BACKEND")
                 or "auto"),
        help="model harness: auto|generic|holo (see agent.py)")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument(
        "--idle-timeout", type=float,
        default=float(os.environ.get("IDLE_TIMEOUT", "60")),
        help="seconds since last action/message before ending an idle "
             "session (default 60 = 1 min; env IDLE_TIMEOUT)")
    parser.add_argument("--port", type=int, default=7788)
    parser.add_argument("--vnc-port", type=int, default=5900)
    parser.add_argument("--ws-port", type=int, default=6080)
    parser.add_argument("--sandbox-url",
                        default=(os.environ.get("SANDBOX_URL")
                                 or os.environ.get("DESKTOP_SANDBOX_URL")
                                 or "") or None,
                        help="remote desktop-sandbox API "
                             "(skips local Xephyr/VNC spawn)")
    parser.add_argument("--stream-url",
                        default=(os.environ.get("STREAM_URL")
                                 or os.environ.get("DESKTOP_STREAM_URL")
                                 or "") or None,
                        help="websocket URL for noVNC "
                             "(default: from sandbox health or local ws-port)")
    parser.add_argument("--sandbox-token",
                        default=(os.environ.get("SANDBOX_TOKEN")
                                 or os.environ.get("DESKTOP_SANDBOX_TOKEN")
                                 or "") or None,
                        help="token for sandbox API")
    cfg = parser.parse_args()

    if not os.path.isdir(NOVNC_DIR):
        sys.exit(f"noVNC not found at {NOVNC_DIR} (apt install novnc)")

    settings = SettingsStore(SETTINGS_PATH)
    screens = ScreenStore(SCREENS_DIR, health_fn=probe_health)

    def _control_timer():
        # Sweeps expired human holds. The runner also self-syncs inside
        # _wait_control, so an expiring hold unblocks a waiting session.
        while True:
            time.sleep(1.0)
            try:
                screens.expire_human_control()
            except Exception:
                pass

    threading.Thread(target=_control_timer, daemon=True).start()

    # Seed default external screen from CLI sandbox if registry empty
    if cfg.sandbox_url:
        rows, total = screens.list()
        if total == 0:
            try:
                screens.create(
                    "Default sandbox",
                    {
                        "mode": "external",
                        "sandbox_url": cfg.sandbox_url,
                        "stream_url": cfg.stream_url,
                        "token": cfg.sandbox_token or "",
                    },
                    ttl_s=int(settings.get().get("control_ttl_s") or 120),
                )
                print("seeded screen registry from --sandbox-url")
            except Exception as e:
                print(f"warning: could not seed screen: {e}",
                      file=sys.stderr)

    if cfg.sandbox_url:
        from .remote import RemoteDesktop
        # Normalize whitespace-only stream so health stream_ws can fill in.
        cfg.stream_url = (cfg.stream_url or "").strip() or None
        desk = RemoteDesktop(
            cfg.sandbox_url,
            token=cfg.sandbox_token or "",
            stream_url=cfg.stream_url,
        )
        if not cfg.stream_url:
            cfg.stream_url = (desk.stream_url or "").strip() or ""
        bus = Bus()
        store = SessionStore()
        runner = Runner(desk, bus, store, cfg, screens=screens,
                        settings=settings)
        server = ThreadingHTTPServer(
            ("127.0.0.1", cfg.port),
            make_handler(bus, runner, store, cfg, screens, settings))
        print(f"console: http://localhost:{cfg.port}  (remote sandbox)")
        print(f"sandbox: {cfg.sandbox_url}")
        if cfg.stream_url:
            print(f"stream:  {cfg.stream_url}")
        else:
            print(
                "warning: no stream_url after sandbox health "
                "(stream_ws empty and --stream-url/STREAM_URL unset); "
                "live noVNC will not work. Set --stream-url or fix "
                "sandbox VNC/websockify.",
                file=sys.stderr,
                flush=True,
            )
        if cfg.task:
            sid = runner.launch(cfg.task)
            print(f"session: http://localhost:{cfg.port}/s/{sid}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down")
        finally:
            desk.close()
        return

    require_binaries(["Xephyr", "openbox", "scrot", "xterm",
                      "x11vnc", "websockify"])

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
            runner = Runner(desk, bus, store, cfg, screens=screens,
                            settings=settings)

            server = ThreadingHTTPServer(
                ("127.0.0.1", cfg.port),
                make_handler(bus, runner, store, cfg, screens, settings))
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
