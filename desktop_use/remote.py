"""Remote desktop client: talk to desktop-sandbox HTTP API.

Implements the same DesktopPort surface as local ``Desktop`` so
``execute(desk, action)`` works unchanged over HTTP.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800

# Agent-safe types only. Never forward spawn / done from the model path;
# agent.execute allowlists before calling us, and we re-check here.
REMOTE_ACTION_TYPES = frozenset({
    "click", "double_click", "right_click", "move", "type", "click_type",
    "key", "scroll",
})

# Per-type field allowlist for sandbox POST (drop unknown keys e.g. cmd).
_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "click": frozenset({"type", "x", "y"}),
    "double_click": frozenset({"type", "x", "y"}),
    "right_click": frozenset({"type", "x", "y"}),
    "move": frozenset({"type", "x", "y"}),
    "type": frozenset({"type", "text"}),
    "click_type": frozenset({"type", "x", "y", "text"}),
    "key": frozenset({"type", "combo"}),
    "scroll": frozenset({"type", "direction", "amount"}),
}

# Keep in sync with model_backends.SCROLL_AMOUNT_MAX.
_SCROLL_AMOUNT_MAX = 50


def sanitize_remote_action(action: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal action body: only whitelisted keys per type."""
    kind = action.get("type")
    allowed = _ACTION_FIELDS.get(str(kind) if kind is not None else "")
    if allowed is None:
        return {"type": kind}
    out: dict[str, Any] = {}
    for k in allowed:
        if k not in action:
            continue
        out[k] = action[k]
    if kind == "scroll" and "amount" in out:
        try:
            amt = int(out["amount"]) if out["amount"] is not None else 3
        except (TypeError, ValueError):
            amt = 3
        out["amount"] = max(1, min(_SCROLL_AMOUNT_MAX, amt))
    return out


def probe_health(
    base_url: str,
    token: str = "",
    timeout: float = 5.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Soft health check for ScreenStore create/retry.

    Never raises for transport/HTTP failure; returns
    ``{"ok": False, "error": "...", "code": "..."}`` instead.
    Success returns ok True plus width/height/display/stream_ws when present.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return {"ok": False, "error": "sandbox_url empty", "code": "bad_url"}
    headers: dict[str, str] = {}
    tok = (token or "").strip()
    if tok:
        headers["X-Sandbox-Token"] = tok
        headers["Authorization"] = f"Bearer {tok}"
    owns = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        try:
            r = http.get(f"{url}/health", headers=headers)
        except httpx.HTTPError as e:
            return {
                "ok": False,
                "error": f"unreachable: {e}",
                "code": "unreachable",
            }
        if r.status_code in (401, 403):
            return {
                "ok": False,
                "error": f"auth failed ({r.status_code})",
                "code": "auth",
            }
        if r.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {r.status_code}",
                "code": f"http_{r.status_code}",
            }
        try:
            body = r.json()
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "non-JSON health body",
                "code": "bad_body",
            }
        if isinstance(body, dict) and body.get("ok") is False:
            return {
                "ok": False,
                "error": str(body.get("error") or "unhealthy"),
                "code": "unhealthy",
            }
        out: dict[str, Any] = {"ok": True}
        if isinstance(body, dict):
            for k in ("display", "width", "height", "stream_ws"):
                if k in body and body[k] is not None:
                    out[k] = body[k]
        return out
    finally:
        if owns:
            http.close()


class RemoteDesktop:
    """Desktop-compatible surface backed by a sandbox API.

    Methods map to:
      screenshot_png  -> GET  /screenshot (fallback /v1/screenshot, /shot)
      move/click/...  -> POST /action     (fallback /v1/action, /input)
      size on connect -> GET  /health (fallback /v1/size)
    """

    is_remote = True

    def __init__(
        self,
        base_url: str,
        token: str = "",
        stream_url: str | None = None,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        http: httpx.Client | None = None,  # alias used by unit tests
        *,
        skip_health: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = (token or "").strip()
        # Whitespace-only must not block health stream_ws fill.
        self.stream_url = (stream_url or "").strip() or None
        provided = client if client is not None else http
        self._owns_client = provided is None
        self._http = provided or httpx.Client(timeout=timeout)
        if skip_health:
            host = urlparse(self.base_url).hostname or "remote"
            port = urlparse(self.base_url).port or ""
            self.name = f"remote:{port}" if port else f"remote:{host}"
            self.width = DEFAULT_WIDTH
            self.height = DEFAULT_HEIGHT
            return
        health = self.health()
        if health.get("ok") is False:
            raise RuntimeError(
                f"sandbox unhealthy at {self.base_url}: "
                f"{health.get('error', health)}")
        host = urlparse(self.base_url).hostname or "remote"
        port = urlparse(self.base_url).port or ""
        label = f"remote:{port}" if port else f"remote:{host}"
        self.name = str(health.get("display") or label)
        self.width = int(
            health.get("width") or self._safe_size().get("width")
            or DEFAULT_WIDTH)
        self.height = int(
            health.get("height") or self._safe_size().get("height")
            or DEFAULT_HEIGHT)
        if not self.stream_url:
            ws = health.get("stream_ws")
            self.stream_url = (ws or "").strip() or None

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.token:
            h["X-Sandbox-Token"] = self.token
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _auth_hint(self, status: int) -> str:
        if status in (401, 403):
            return (" (check SANDBOX_TOKEN / --sandbox-token; send "
                    "Authorization: Bearer and X-Sandbox-Token)")
        return ""

    def _get(self, *paths: str) -> httpx.Response:
        last: httpx.Response | None = None
        for path in paths:
            r = self._http.get(
                f"{self.base_url}{path}", headers=self._headers())
            last = r
            if r.status_code != 404:
                if r.status_code in (401, 403):
                    raise RuntimeError(
                        f"sandbox auth failed ({r.status_code}) on {path}"
                        + self._auth_hint(r.status_code))
                r.raise_for_status()
                return r
        assert last is not None
        last.raise_for_status()
        return last

    def _post_action(self, action: dict[str, Any]) -> dict:
        kind = action.get("type")
        if kind not in REMOTE_ACTION_TYPES:
            raise RuntimeError(
                f"refusing to send action type {kind!r} to sandbox "
                f"(allowed: {sorted(REMOTE_ACTION_TYPES)})")
        headers = {**self._headers(), "Content-Type": "application/json"}
        body = json.dumps(sanitize_remote_action(action))
        last: httpx.Response | None = None
        # Prefer bare /action (MVP contract); keep /v1/action as alias.
        for path in ("/action", "/v1/action", "/input"):
            r = self._http.post(
                f"{self.base_url}{path}", headers=headers, content=body)
            last = r
            if r.status_code != 404:
                if r.status_code >= 400:
                    try:
                        err = r.json().get("error", r.text)
                    except Exception:
                        err = r.text
                    raise RuntimeError(
                        f"sandbox action failed ({r.status_code}): {err}"
                        + self._auth_hint(r.status_code))
                try:
                    return r.json()
                except json.JSONDecodeError:
                    return {"ok": True, "result": r.text or ""}
        assert last is not None
        last.raise_for_status()
        try:
            return last.json()
        except json.JSONDecodeError:
            return {"ok": True, "result": last.text or ""}

    def health(self) -> dict:
        try:
            r = self._http.get(
                f"{self.base_url}/health", headers=self._headers())
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"sandbox health unreachable at {self.base_url}: {e}") from e
        if r.status_code in (401, 403):
            raise RuntimeError(
                f"sandbox auth failed ({r.status_code}) on /health"
                + self._auth_hint(r.status_code))
        r.raise_for_status()
        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"sandbox /health returned non-JSON: {(r.text or '')[:200]!r}"
            ) from e

    def _safe_size(self) -> dict:
        try:
            r = self._get("/v1/size", "/size")
            return r.json()
        except Exception:
            return {}

    def screenshot_png(self) -> bytes:
        # Prefer bare /screenshot first (MVP); fall back to aliases.
        r = self._get("/screenshot", "/v1/screenshot", "/shot")
        return r.content

    def move(self, x: int, y: int) -> None:
        self._post_action({"type": "move", "x": int(x), "y": int(y)})

    def click(
        self, x: int, y: int, button: int = 1, times: int = 1,
    ) -> None:
        if button == 3:
            kind = "right_click"
        elif times >= 2:
            kind = "double_click"
        else:
            kind = "click"
        self._post_action({"type": kind, "x": int(x), "y": int(y)})

    def scroll(self, direction: str, amount: int = 3) -> None:
        try:
            amt = int(amount) if amount is not None else 3
        except (TypeError, ValueError):
            amt = 3
        self._post_action({
            "type": "scroll",
            "direction": direction,
            "amount": max(1, min(_SCROLL_AMOUNT_MAX, amt)),
        })

    def type_text(self, text: str) -> None:
        self._post_action({"type": "type", "text": str(text)})

    def key_combo(self, combo: str) -> None:
        self._post_action({"type": "key", "combo": str(combo)})

    def execute(self, action: dict) -> str:
        """POST a raw action (also used by --probe). Prefer execute() in agent."""
        body = self._post_action(action)
        if not body.get("ok", True):
            raise RuntimeError(body.get("error", "action failed"))
        return body.get("result") or json.dumps(action)

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
