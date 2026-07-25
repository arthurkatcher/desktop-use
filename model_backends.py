"""Model backend profiles: generic (pixels + prefill) vs Holo structured.

Pure helpers only. ask_model in agent.py owns HTTP and the public return
shape {reasoning, action} with pixel coordinates.
"""

from __future__ import annotations

import json
from typing import Any


# Model-facing action types (same set as agent.execute). spawn/shell never
# allowed through normalize — defense-in-depth before execute/remote.
AGENT_ACTION_TYPES = frozenset({
    "click", "double_click", "right_click", "move", "type", "click_type",
    "key", "scroll", "wait", "done",
})

# Cap scroll wheel iterations so a huge amount cannot hang the local runner.
SCROLL_AMOUNT_MAX = 50


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def resolve_model_backend(
    base_url: str,
    model: str,
    flag: str | None = None,
) -> str:
    """Return 'generic' or 'holo'. flag: auto|generic|holo|None.

    Auto: prefer URL (H Company API) first; model id only for known Holo3
    portal slugs (startswith holo3). Self-hosted Holo1.x names stay generic
    unless the operator passes --model-backend holo.
    """
    f = (flag or "auto").strip().lower()
    if f in ("generic", "holo"):
        return f
    bu = (base_url or "").lower()
    m = (model or "").lower()
    # Primary: Holo Models API host.
    if "hcompany.ai" in bu or "api.hcompany" in bu:
        return "holo"
    # Secondary: known Holo3 model ids only (not broad "holo*" / "*holo3*").
    if m.startswith("holo3") or m.startswith("hcompany/holo"):
        return "holo"
    return "generic"


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

def scale_norm_to_pixels(
    x: float | int,
    y: float | int,
    width: int,
    height: int,
) -> tuple[int, int]:
    """Scale Holo [0,1000] coords to absolute pixels; clamp to dim-1."""
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid size {width}x{height}")
    px = int((float(x) / 1000.0) * width)
    py = int((float(y) / 1000.0) * height)
    px = max(0, min(width - 1, px))
    py = max(0, min(height - 1, py))
    return px, py


# ---------------------------------------------------------------------------
# Tool map (coords stay normalized; normalize_decision scales)
# ---------------------------------------------------------------------------

_FORBIDDEN_TOOLS = frozenset({
    "spawn", "shell", "bash", "run", "exec", "system",
})


def _coerce_int(raw: Any, default: int, *, field: str) -> int:
    """null → default; invalid → ValueError (Runner retries on ValueError)."""
    if raw is None:
        raw = default
    try:
        return int(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} invalid: {raw!r}") from e


def _coerce_float(raw: Any, default: float, *, field: str) -> float:
    if raw is None:
        raw = default
    try:
        return float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} invalid: {raw!r}") from e


def clamp_scroll_amount(raw: Any, default: int = 3) -> int:
    """Coerce scroll amount; null→default; clamp to [1, SCROLL_AMOUNT_MAX]."""
    amount = _coerce_int(raw, default, field="scroll amount")
    return max(1, min(SCROLL_AMOUNT_MAX, amount))


def _reasoning_text(obj: dict, msg: dict) -> str:
    """Prefer reasoning, then thought, then message.reasoning."""
    for key in ("reasoning", "thought"):
        val = obj.get(key)
        if val is not None and str(val) != "":
            return str(val)
    mr = msg.get("reasoning")
    if mr is not None and str(mr) != "":
        return str(mr)
    return ""


def _finalize_action(action: dict) -> dict:
    """Allowlist type; clamp scroll; reject spawn/shell etc. with ValueError."""
    if not isinstance(action, dict) or "type" not in action:
        raise ValueError('mapped action lacks "type"')
    kind = action.get("type")
    if kind not in AGENT_ACTION_TYPES:
        raise ValueError(
            f"unknown or forbidden action type {kind!r}")
    if kind == "scroll":
        out = dict(action)
        out["amount"] = clamp_scroll_amount(out.get("amount", 3))
        if "direction" not in out or not out["direction"]:
            out["direction"] = "down"
        return out
    return action


def holo_tool_to_action(tool_call: dict) -> dict:
    """Map Holo tool_call object → internal action (coords still normalized).

    write + press_enter: emit type only (one action per step); ignore
    press_enter on the wire action. Model can key/Return next step.
    Never maps spawn/shell/bash/run.
    """
    if not isinstance(tool_call, dict):
        raise ValueError(f"tool_call must be a dict, got {type(tool_call)}")
    name = (
        tool_call.get("tool_name")
        or tool_call.get("name")
        or tool_call.get("type")
        or ""
    )
    name = str(name).strip().lower()
    if not name:
        raise ValueError("tool_call missing tool_name")
    if name in _FORBIDDEN_TOOLS:
        raise ValueError(
            f"refusing tool {name!r}: not allowed on the agent path")

    def _xy() -> tuple[Any, Any]:
        if "x" not in tool_call or "y" not in tool_call:
            raise ValueError(f"{name} requires x and y")
        return tool_call["x"], tool_call["y"]

    if name == "click":
        x, y = _xy()
        return {"type": "click", "x": x, "y": y}
    if name == "double_click":
        x, y = _xy()
        return {"type": "double_click", "x": x, "y": y}
    if name == "right_click":
        x, y = _xy()
        return {"type": "right_click", "x": x, "y": y}
    if name == "move":
        x, y = _xy()
        return {"type": "move", "x": x, "y": y}
    if name == "click_type":
        x, y = _xy()
        text = tool_call.get("text", tool_call.get("content", ""))
        return {"type": "click_type", "x": x, "y": y, "text": str(text)}
    if name == "write":
        # press_enter is ignored for wire action (single-action Runner).
        content = tool_call.get("content", tool_call.get("text", ""))
        return {"type": "type", "text": str(content)}
    if name == "type":
        text = tool_call.get("text", tool_call.get("content", ""))
        return {"type": "type", "text": str(text)}
    if name == "scroll":
        return {
            "type": "scroll",
            "direction": str(tool_call.get("direction") or "down"),
            "amount": clamp_scroll_amount(tool_call.get("amount", 3)),
        }
    if name in ("key", "hotkey"):
        combo = (
            tool_call.get("combo")
            or tool_call.get("keys")
            or tool_call.get("key")
            or ""
        )
        combo = str(combo).strip()
        if not combo:
            raise ValueError("key combo must be a non-empty string")
        return {"type": "key", "combo": combo}
    if name == "wait":
        return {
            "type": "wait",
            "seconds": _coerce_float(
                tool_call.get("seconds", 1), 1.0, field="wait seconds"),
        }
    if name in ("answer", "done"):
        summary = (
            tool_call.get("content")
            or tool_call.get("summary")
            or ""
        )
        success = tool_call.get("success", True)
        return {
            "type": "done",
            "success": bool(success),
            "summary": str(summary),
        }
    raise ValueError(f"unknown Holo tool_name {name!r}")


def _scale_action_xy(action: dict, width: int, height: int) -> dict:
    """Return a copy of action with x/y scaled when both present."""
    out = dict(action)
    if "x" in out and "y" in out:
        out["x"], out["y"] = scale_norm_to_pixels(
            out["x"], out["y"], width, height)
    return out


def normalize_decision(
    backend: str,
    obj: dict,
    *,
    width: int,
    height: int,
    message: dict | None = None,
) -> dict:
    """Return {reasoning, action} with pixel coords and action.type present.

    Both backends allowlist action.type against AGENT_ACTION_TYPES so spawn
    and other non-agent types raise ValueError (corrective retry), not pass
    through to execute.
    """
    if not isinstance(obj, dict):
        raise ValueError(f"decision must be a dict, got {type(obj)}")

    backend = (backend or "generic").strip().lower()
    msg = message if isinstance(message, dict) else {}

    if backend == "holo":
        # Prefer tool_call when present; only then scale [0,1000]→pixels.
        tool = obj.get("tool_call")
        if isinstance(tool, dict):
            action = holo_tool_to_action(tool)
            action = _scale_action_xy(action, width, height)
            action = _finalize_action(action)
            out: dict[str, Any] = {
                "reasoning": _reasoning_text(obj, msg),
                "action": action,
            }
            if "note" in obj:
                out["note"] = obj.get("note")
            return out
        # Fallback: model ignored Holo schema and emitted generic shape.
        # Coords treated as already-pixels (no scale).
        action_g = obj.get("action")
        if isinstance(action_g, dict) and "type" in action_g:
            return {
                "reasoning": _reasoning_text(obj, msg),
                "action": _finalize_action(action_g),
            }
        raise ValueError(
            'Holo reply needs "tool_call" or generic "action" with type')

    # generic
    action = obj.get("action")
    if not isinstance(action, dict) or "type" not in action:
        raise ValueError(
            'reply JSON lacks an "action" object with a "type" field')
    return {
        "reasoning": str(obj.get("reasoning") or ""),
        "action": _finalize_action(action),
    }


# ---------------------------------------------------------------------------
# JSON Schema for structured_outputs (hand-written, no pydantic)
# ---------------------------------------------------------------------------

def holo_step_json_schema() -> dict:
    """JSON Schema for structured_outputs.json — union on tool_name."""

    def _xy_props(extra: dict | None = None) -> dict:
        props = {
            "x": {"type": "integer", "minimum": 0, "maximum": 1000},
            "y": {"type": "integer", "minimum": 0, "maximum": 1000},
            "element": {"type": ["string", "null"]},
        }
        if extra:
            props.update(extra)
        return props

    tools = [
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "click"},
                **_xy_props(),
            },
            "required": ["tool_name", "x", "y"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "double_click"},
                **_xy_props(),
            },
            "required": ["tool_name", "x", "y"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "right_click"},
                **_xy_props(),
            },
            "required": ["tool_name", "x", "y"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "move"},
                **_xy_props(),
            },
            "required": ["tool_name", "x", "y"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "write"},
                "content": {"type": "string"},
                "press_enter": {"type": "boolean"},
            },
            "required": ["tool_name", "content"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "type"},
                "text": {"type": "string"},
            },
            "required": ["tool_name", "text"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "scroll"},
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                },
                "amount": {"type": "integer", "minimum": 1},
            },
            "required": ["tool_name"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "key"},
                "combo": {"type": "string"},
            },
            "required": ["tool_name", "combo"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "wait"},
                "seconds": {"type": "number", "minimum": 0},
            },
            "required": ["tool_name"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "answer"},
                "content": {"type": "string"},
            },
            "required": ["tool_name", "content"],
            "additionalProperties": True,
        },
        {
            "type": "object",
            "properties": {
                "tool_name": {"const": "click_type"},
                **_xy_props({"text": {"type": "string"}}),
            },
            "required": ["tool_name", "x", "y", "text"],
            "additionalProperties": True,
        },
    ]
    return {
        "type": "object",
        "properties": {
            "note": {"type": ["string", "null"]},
            "thought": {"type": "string"},
            "tool_call": {"oneOf": tools},
        },
        "required": ["thought", "tool_call"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

def build_holo_request_body(
    *,
    model: str,
    messages: list,
    max_tokens: int = 4000,
) -> dict:
    """Holo Models API body: structured_outputs + thinking, temp 0.8."""
    return {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": max_tokens,
        "reasoning_effort": "medium",
        "chat_template_kwargs": {"enable_thinking": True},
        "structured_outputs": {"json": holo_step_json_schema()},
    }


def build_generic_request_body(
    *,
    model: str,
    messages: list,
    base_url: str,
    max_tokens: int = 4000,
) -> dict:
    """Generic OpenAI-compat body: temp 0; OpenRouter reasoning if needed."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if "openrouter" in (base_url or "").lower():
        body["reasoning"] = {"effort": "low"}
    return body


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def extract_message_content(message: dict) -> str:
    """content str or join text parts from list."""
    if not isinstance(message, dict):
        return str(message or "")
    text = message.get("content")
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts: list[str] = []
        for p in text:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or ""))
            else:
                parts.append(str(p))
        return "".join(parts)
    return str(text or "")


def parse_json_object_from_text(
    text: str,
    *,
    use_prefill: bool,
) -> dict:
    """Parse a JSON object from model content; dual-candidate if prefill."""
    if not isinstance(text, str):
        text = str(text or "")
    candidates = (text,) if not use_prefill else ("{" + text, text)
    for candidate in candidates:
        start = candidate.find("{")
        if start == -1:
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(candidate[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError(f"unparseable model reply: {text[:200]!r}")


def system_prompt_holo(schema: dict | None = None) -> str:
    """Holo system prompt: [0,1000] coords + embedded structured schema."""
    schema = schema if schema is not None else holo_step_json_schema()
    schema_json = json.dumps(schema, indent=2)
    return f"""\
You are a computer-use agent controlling a Linux desktop through screenshots.
Each turn you receive the current screenshot and the history of your previous
actions. Decide ONE next tool_call.

Coordinates are integers in [0, 1000] inclusive. Origin (0,0) is top-left of
the screenshot image you receive. Scale is relative to that image, not fixed
pixels.

Respond with a single JSON object matching the schema below (note, thought,
tool_call). No markdown fences. No tool-call XML.

Available tools (tool_name):
  click / double_click / right_click / move — x, y in [0,1000]; element optional
  write — content string; press_enter optional (prefer a follow-up key step)
  type — text string (alias of write content)
  click_type — x, y, text (PREFER for any input field: click then type)
  scroll — direction up|down, amount (default 3)
  key — combo e.g. Return, ctrl+l, alt+F4
  wait — seconds (float, small)
  answer — content string when the task is finished (terminal)

Each history entry notes whether the screen actually changed after that
action. If it did not change, the click missed or the action failed:
re-examine and try different coordinates or a different approach; never
repeat the same action blindly.

When you receive both a before and a current screenshot, compare them: if a
window you were using has disappeared, your own last action probably closed
it - be careful where you click (avoid title-bar close buttons).

Text fields do NOT visibly change when they gain focus. Never click the same
input twice: use click_type, or after one plain click assume focus and write.

Only use answer when the CURRENT screenshot visually confirms the goal state.
A mostly black or empty screen means nothing is open yet.

Desktop environment (openbox sandbox):
- To open apps from an empty desktop (grey/plain background with no useful
  windows), RIGHT-CLICK empty desktop space to open the root menu, then click
  "Terminal emulator" or "Web browser". Do NOT type "firefox &" in a terminal
  and wait - that often fails here.
- Prefer the menu "Web browser" (Chromium). There is usually no Firefox.
- If a Chromium/browser window is already visible, use THAT window (click the
  address bar or use ctrl+l) instead of launching another browser.
- After opening a menu item, wait briefly and confirm the new window on the
  next screenshot before typing URLs or search queries.

<output_format>
```json
{schema_json}
```
</output_format>
"""
