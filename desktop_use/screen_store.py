"""Screen registry: connection, power, lease, control (input mutex).

Control lives on the screen. Sessions lease screens exclusively.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from copy import deepcopy
from typing import Any, Callable

# Health probe: (sandbox_url, token) -> dict with ok bool + fields
HealthFn = Callable[[str, str], dict[str, Any]]


def default_meta(screen_id: str, name: str, connection: dict,
                 profile: dict | None = None) -> dict:
    now = time.time()
    return {
        "id": screen_id,
        "name": name,
        "created": now,
        "updated": now,
        "status": "on",
        "connection": {
            "mode": connection.get("mode") or "external",
            "sandbox_url": (connection.get("sandbox_url") or "").rstrip("/"),
            "stream_url": (connection.get("stream_url") or "").strip() or None,
            "token": connection.get("token") or "",
        },
        "profile": profile or {
            "size": "1280x800",
            "icons": {"terminal": True, "browser": True},
            "pcmanfm_desktop": True,
            "auto_launch_browser": False,
            "auto_launch_xterm": False,
        },
        "lease": {"session_id": None, "held_since": None},
        "control": {
            "holder": "none",
            "via": None,
            "session_id": None,
            "expires_at": None,
            "ttl_s": 120,
        },
        "health": {
            "ok": True,
            "last_check": None,
            "last_ok_at": None,
            "display": None,
            "width": None,
            "height": None,
            "stream_ws": None,
        },
        "last_error": {"at": None, "code": None, "message": None},
    }


class ScreenStore:
    def __init__(self, root: str, health_fn: HealthFn | None = None):
        self.root = root
        self.lock = threading.Lock()
        self.health_fn = health_fn
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, sid: str) -> str:
        return os.path.join(self.root, sid)

    def _safe(self, part: str) -> bool:
        if not part or part in (".", "..") or part.startswith("."):
            return False
        if os.sep in part or (os.altsep and os.altsep in part):
            return False
        if "/" in part or "\\" in part:
            return False
        return True

    def _write(self, meta: dict) -> None:
        d = self._dir(meta["id"])
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "meta.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)

    def get(self, sid: str) -> dict | None:
        if not self._safe(sid):
            return None
        path = os.path.join(self._dir(sid), "meta.json")
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def list(self, *, limit: int | None = None, offset: int = 0,
             status: str | None = None, lease: str | None = None,
             q: str | None = None) -> tuple[list[dict], int]:
        rows: list[dict] = []
        try:
            names = os.listdir(self.root)
        except OSError:
            names = []
        for name in names:
            meta = self.get(name)
            if meta:
                rows.append(meta)
        rows.sort(key=lambda m: m.get("updated") or m.get("created") or 0,
                  reverse=True)
        if status:
            rows = [r for r in rows if r.get("status") == status]
        if lease == "free":
            rows = [r for r in rows
                    if not (r.get("lease") or {}).get("session_id")]
        elif lease == "leased":
            rows = [r for r in rows
                    if (r.get("lease") or {}).get("session_id")]
        if q:
            ql = q.lower()
            rows = [r for r in rows if (
                ql in (r.get("name") or "").lower()
                or ql in (r.get("id") or "").lower()
                or ql in ((r.get("connection") or {})
                          .get("sandbox_url") or "").lower()
            )]
        total = len(rows)
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows, total

    def create(self, name: str, connection: dict,
               profile: dict | None = None,
               ttl_s: int = 120) -> dict:
        """Create only if health succeeds. Raises ValueError on fail."""
        name = (name or "").strip()
        if not name:
            raise ValueError("name required")
        url = (connection.get("sandbox_url") or "").strip().rstrip("/")
        if not url:
            raise ValueError("sandbox_url required")
        token = connection.get("token") or ""
        if self.health_fn is None:
            raise RuntimeError("no health_fn configured")
        health = self.health_fn(url, token)
        if not health.get("ok"):
            raise ValueError(
                health.get("error") or health.get("message")
                or "health check failed")
        with self.lock:
            sid = "scr-" + secrets.token_hex(4)
            while self.get(sid) is not None:
                sid = "scr-" + secrets.token_hex(4)
            meta = default_meta(sid, name, {
                **connection, "sandbox_url": url, "token": token,
            }, profile)
            meta["control"]["ttl_s"] = int(ttl_s)
            now = time.time()
            meta["health"] = {
                "ok": True,
                "last_check": now,
                "last_ok_at": now,
                "display": health.get("display"),
                "width": health.get("width"),
                "height": health.get("height"),
                "stream_ws": health.get("stream_ws"),
            }
            if not meta["connection"].get("stream_url") and health.get(
                    "stream_ws"):
                meta["connection"]["stream_url"] = health.get("stream_ws")
            meta["status"] = "on"
            meta["last_error"] = {"at": None, "code": None, "message": None}
            self._write(meta)
            return deepcopy(meta)

    def update_fields(self, sid: str, **kw) -> dict | None:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                return None
            for k, v in kw.items():
                if k in ("name", "connection", "profile"):
                    meta[k] = v
            meta["updated"] = time.time()
            self._write(meta)
            return deepcopy(meta)

    def delete(self, sid: str) -> bool:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                return False
            if (meta.get("lease") or {}).get("session_id"):
                raise ValueError("cannot delete leased screen")
            path = os.path.join(self._dir(sid), "meta.json")
            try:
                os.remove(path)
                try:
                    os.rmdir(self._dir(sid))
                except OSError:
                    pass
            except OSError:
                return False
            return True

    def apply_health(self, sid: str, health: dict) -> dict | None:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                return None
            now = time.time()
            meta["health"]["last_check"] = now
            if health.get("ok"):
                meta["health"]["ok"] = True
                meta["health"]["last_ok_at"] = now
                for k in ("display", "width", "height", "stream_ws"):
                    if health.get(k) is not None:
                        meta["health"][k] = health.get(k)
                meta["last_error"] = {
                    "at": None, "code": None, "message": None}
                if meta["status"] in ("on", "error"):
                    meta["status"] = "on"
            else:
                meta["health"]["ok"] = False
                msg = (health.get("error") or health.get("message")
                       or "health failed")
                meta["last_error"] = {
                    "at": now, "code": health.get("code") or "health",
                    "message": str(msg)}
                if meta["status"] == "on":
                    meta["status"] = "error"
            meta["updated"] = now
            self._write(meta)
            return deepcopy(meta)

    def probe(self, sid: str) -> dict | None:
        meta = self.get(sid)
        if not meta or self.health_fn is None:
            return meta
        conn = meta.get("connection") or {}
        health = self.health_fn(
            conn.get("sandbox_url") or "", conn.get("token") or "")
        return self.apply_health(sid, health)

    def turn_on(self, sid: str) -> dict:
        meta = self.get(sid)
        if not meta:
            raise KeyError("unknown screen")
        if self.health_fn is None:
            raise RuntimeError("no health_fn")
        conn = meta.get("connection") or {}
        health = self.health_fn(
            conn.get("sandbox_url") or "", conn.get("token") or "")
        if not health.get("ok"):
            self.apply_health(sid, health)
            raise ValueError(
                health.get("error") or "health check failed")
        out = self.apply_health(sid, health)
        with self.lock:
            meta = self.get(sid)
            if meta:
                meta["status"] = "on"
                meta["updated"] = time.time()
                self._write(meta)
                return deepcopy(meta)
        return out  # type: ignore

    def turn_off(self, sid: str, *, force: bool = False) -> dict:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                raise KeyError("unknown screen")
            if (meta.get("lease") or {}).get("session_id") and not force:
                raise ValueError("cannot turn off leased screen")
            meta["status"] = "off"
            meta["updated"] = time.time()
            # Soft off: clear human hold if free
            if not (meta.get("lease") or {}).get("session_id"):
                meta["control"] = {
                    "holder": "none", "via": None, "session_id": None,
                    "expires_at": None,
                    "ttl_s": meta["control"].get("ttl_s", 120),
                }
            self._write(meta)
            return deepcopy(meta)

    # ── lease ──────────────────────────────────────────

    def acquire_lease(self, sid: str, session_id: str) -> dict:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                raise KeyError("unknown screen")
            if meta.get("status") != "on":
                raise ValueError(
                    f"screen not available (status={meta.get('status')})")
            if (meta.get("lease") or {}).get("session_id"):
                raise ValueError("screen already leased")
            if (meta.get("control") or {}).get("holder") == "human":
                raise ValueError("human holds free screen; release control first")
            now = time.time()
            meta["lease"] = {"session_id": session_id, "held_since": now}
            meta["control"] = {
                "holder": "ai",
                "via": "session",
                "session_id": session_id,
                "expires_at": None,
                "ttl_s": meta["control"].get("ttl_s", 120),
            }
            meta["updated"] = now
            self._write(meta)
            return deepcopy(meta)

    def release_lease(self, sid: str, session_id: str | None = None) -> dict | None:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                return None
            cur = (meta.get("lease") or {}).get("session_id")
            if session_id is not None and cur and cur != session_id:
                return deepcopy(meta)
            meta["lease"] = {"session_id": None, "held_since": None}
            meta["control"] = {
                "holder": "none", "via": None, "session_id": None,
                "expires_at": None,
                "ttl_s": meta["control"].get("ttl_s", 120),
            }
            meta["updated"] = time.time()
            self._write(meta)
            return deepcopy(meta)

    def release_lease_for_session(self, session_id: str) -> None:
        rows, _ = self.list()
        for r in rows:
            if (r.get("lease") or {}).get("session_id") == session_id:
                self.release_lease(r["id"], session_id)

    def set_control_idle(self, sid: str, session_id: str) -> dict | None:
        """Lease kept, control → none (session parked idle)."""
        with self.lock:
            meta = self.get(sid)
            if not meta:
                return None
            if (meta.get("lease") or {}).get("session_id") != session_id:
                return deepcopy(meta)
            meta["control"] = {
                "holder": "none", "via": None, "session_id": session_id,
                "expires_at": None,
                "ttl_s": meta["control"].get("ttl_s", 120),
            }
            meta["updated"] = time.time()
            self._write(meta)
            return deepcopy(meta)

    def set_control_ai(self, sid: str, session_id: str) -> dict | None:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                return None
            if (meta.get("lease") or {}).get("session_id") != session_id:
                return deepcopy(meta)
            meta["control"] = {
                "holder": "ai", "via": "session", "session_id": session_id,
                "expires_at": None,
                "ttl_s": meta["control"].get("ttl_s", 120),
            }
            meta["updated"] = time.time()
            self._write(meta)
            return deepcopy(meta)

    def take_control(self, sid: str, *, via: str = "ui",
                     session_id: str | None = None,
                     ttl_s: int | None = None) -> dict:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                raise KeyError("unknown screen")
            if meta.get("status") == "off":
                raise ValueError("screen is off")
            lease_sid = (meta.get("lease") or {}).get("session_id")
            if lease_sid and session_id and lease_sid != session_id:
                raise ValueError("session does not hold this screen")
            ttl = int(ttl_s if ttl_s is not None
                      else meta["control"].get("ttl_s", 120))
            now = time.time()
            meta["control"] = {
                "holder": "human",
                "via": via,
                "session_id": lease_sid or session_id,
                "expires_at": now + ttl,
                "ttl_s": ttl,
            }
            meta["updated"] = now
            self._write(meta)
            return deepcopy(meta)

    def release_control(self, sid: str, *, resume_ai: bool = True) -> dict:
        with self.lock:
            meta = self.get(sid)
            if not meta:
                raise KeyError("unknown screen")
            lease_sid = (meta.get("lease") or {}).get("session_id")
            now = time.time()
            if lease_sid and resume_ai:
                meta["control"] = {
                    "holder": "ai", "via": "session",
                    "session_id": lease_sid, "expires_at": None,
                    "ttl_s": meta["control"].get("ttl_s", 120),
                }
            else:
                meta["control"] = {
                    "holder": "none", "via": None,
                    "session_id": lease_sid, "expires_at": None,
                    "ttl_s": meta["control"].get("ttl_s", 120),
                }
            meta["updated"] = now
            self._write(meta)
            return deepcopy(meta)

    def expire_human_control(self, now: float | None = None) -> list[dict]:
        """Clear expired human holds. Returns list of changed metas."""
        now = now if now is not None else time.time()
        changed: list[dict] = []
        with self.lock:
            try:
                names = os.listdir(self.root)
            except OSError:
                names = []
            for name in names:
                meta = self.get(name)
                if not meta:
                    continue
                ctrl = meta.get("control") or {}
                if ctrl.get("holder") != "human":
                    continue
                exp = ctrl.get("expires_at")
                if exp is None or exp > now:
                    continue
                lease_sid = (meta.get("lease") or {}).get("session_id")
                if lease_sid:
                    # return to ai if session still holds
                    meta["control"] = {
                        "holder": "ai", "via": "session",
                        "session_id": lease_sid, "expires_at": None,
                        "ttl_s": ctrl.get("ttl_s", 120),
                    }
                else:
                    meta["control"] = {
                        "holder": "none", "via": None,
                        "session_id": None, "expires_at": None,
                        "ttl_s": ctrl.get("ttl_s", 120),
                    }
                meta["updated"] = now
                self._write(meta)
                changed.append(deepcopy(meta))
        return changed

    def ai_may_act(self, sid: str, session_id: str) -> bool:
        meta = self.get(sid)
        if not meta:
            return False
        if meta.get("status") != "on":
            return False
        if (meta.get("lease") or {}).get("session_id") != session_id:
            return False
        return (meta.get("control") or {}).get("holder") == "ai"

    def eligible_for_launch(self) -> list[dict]:
        rows, _ = self.list(status="on", lease="free")
        return [r for r in rows
                if (r.get("control") or {}).get("holder") != "human"]
