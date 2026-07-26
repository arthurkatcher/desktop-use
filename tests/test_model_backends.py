"""Unit tests for dual model backends (generic + Holo). No live network."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

import httpx

from desktop_use.model_backends import (
    AGENT_ACTION_TYPES,
    SCROLL_AMOUNT_MAX,
    build_generic_request_body,
    build_holo_request_body,
    clamp_scroll_amount,
    extract_message_content,
    holo_step_json_schema,
    holo_tool_to_action,
    normalize_decision,
    parse_json_object_from_text,
    resolve_model_backend,
    scale_norm_to_pixels,
    system_prompt_holo,
)


class TestResolveBackend(unittest.TestCase):
    """T1 — resolve_model_backend."""

    def test_auto_hcompany_url(self):
        self.assertEqual(
            resolve_model_backend(
                "https://api.hcompany.ai/v1", "anything", "auto"),
            "holo")

    def test_auto_openrouter_claude_generic(self):
        self.assertEqual(
            resolve_model_backend(
                "https://openrouter.ai/api/v1",
                "anthropic/claude-haiku-4.5",
                "auto"),
            "generic")

    def test_auto_empty_url_holo_model(self):
        self.assertEqual(
            resolve_model_backend("", "holo3-1-35b-a3b", "auto"),
            "holo")

    def test_auto_ollama_qwen_generic(self):
        self.assertEqual(
            resolve_model_backend(
                "http://localhost:11434/v1", "qwen2.5vl", "auto"),
            "generic")

    def test_flag_generic_overrides_hcompany(self):
        self.assertEqual(
            resolve_model_backend(
                "https://api.hcompany.ai/v1", "holo3-1-35b-a3b", "generic"),
            "generic")

    def test_flag_holo_overrides_openrouter(self):
        self.assertEqual(
            resolve_model_backend(
                "https://openrouter.ai/api/v1", "claude", "holo"),
            "holo")

    def test_flag_auto_and_none(self):
        self.assertEqual(
            resolve_model_backend(
                "https://api.hcompany.ai/v1", "x", "auto"),
            "holo")
        self.assertEqual(
            resolve_model_backend(
                "https://api.hcompany.ai/v1", "x", None),
            "holo")
        self.assertEqual(
            resolve_model_backend(
                "http://localhost:11434/v1", "qwen", None),
            "generic")

    def test_holo1x_selfhost_stays_generic(self):
        """Broad holo* names are not forced to Holo harness (use generic)."""
        for model in ("holo1.5", "Holo1.5-7B", "something-holo3-x"):
            self.assertEqual(
                resolve_model_backend(
                    "http://localhost:8000/v1", model, "auto"),
                "generic",
                model)

    def test_holo3_model_id_still_holo(self):
        self.assertEqual(
            resolve_model_backend("", "holo3-1-35b-a3b", "auto"),
            "holo")
        self.assertEqual(
            resolve_model_backend(
                "http://localhost:8000/v1", "holo3-4b", "auto"),
            "holo")


class TestScaleNorm(unittest.TestCase):
    """T2 — scale_norm_to_pixels."""

    def test_origin(self):
        self.assertEqual(
            scale_norm_to_pixels(0, 0, 1280, 800), (0, 0))

    def test_max_clamped(self):
        self.assertEqual(
            scale_norm_to_pixels(1000, 1000, 1280, 800), (1279, 799))

    def test_mid(self):
        self.assertEqual(
            scale_norm_to_pixels(500, 500, 1280, 800), (640, 400))

    def test_floats_floor_via_int(self):
        self.assertEqual(
            scale_norm_to_pixels(250.9, 100, 1280, 800),
            (int((250.9 / 1000) * 1280), int((100 / 1000) * 800)))

    def test_negative_clamps_to_zero(self):
        self.assertEqual(
            scale_norm_to_pixels(-10, -5, 1280, 800), (0, 0))

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            scale_norm_to_pixels(100, 100, 0, 800)
        with self.assertRaises(ValueError):
            scale_norm_to_pixels(100, 100, 1280, -1)


class TestHoloToolMap(unittest.TestCase):
    """T3 — holo_tool_to_action."""

    def test_click(self):
        a = holo_tool_to_action(
            {"tool_name": "click", "x": 100, "y": 200})
        self.assertEqual(a, {"type": "click", "x": 100, "y": 200})

    def test_write_to_type(self):
        a = holo_tool_to_action(
            {"tool_name": "write", "content": "hello"})
        self.assertEqual(a, {"type": "type", "text": "hello"})

    def test_write_press_enter_no_crash(self):
        a = holo_tool_to_action({
            "tool_name": "write",
            "content": "hi",
            "press_enter": True,
        })
        self.assertEqual(a["type"], "type")
        self.assertEqual(a["text"], "hi")
        self.assertNotIn("\n", a["text"])

    def test_answer_to_done(self):
        a = holo_tool_to_action(
            {"tool_name": "answer", "content": "done here"})
        self.assertEqual(a["type"], "done")
        self.assertTrue(a["success"])
        self.assertEqual(a["summary"], "done here")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            holo_tool_to_action({"tool_name": "drag", "x": 1, "y": 2})

    def test_spawn_shell_raise(self):
        for name in ("spawn", "shell", "bash", "run"):
            with self.assertRaises(ValueError):
                holo_tool_to_action({"tool_name": name, "cmd": "id"})

    def test_scroll_defaults(self):
        a = holo_tool_to_action({"tool_name": "scroll"})
        self.assertEqual(a["type"], "scroll")
        self.assertEqual(a["direction"], "down")
        self.assertEqual(a["amount"], 3)

    def test_scroll_amount_null_defaults(self):
        """JSON null must not raise TypeError; coerce to default 3."""
        a = holo_tool_to_action(
            {"tool_name": "scroll", "amount": None, "direction": "up"})
        self.assertEqual(a["amount"], 3)
        self.assertEqual(a["direction"], "up")

    def test_scroll_amount_invalid_raises_value_error(self):
        with self.assertRaises(ValueError):
            holo_tool_to_action(
                {"tool_name": "scroll", "amount": "many"})

    def test_scroll_amount_clamped(self):
        a = holo_tool_to_action(
            {"tool_name": "scroll", "amount": 10**9})
        self.assertEqual(a["amount"], SCROLL_AMOUNT_MAX)
        self.assertEqual(
            clamp_scroll_amount(0), 1)
        self.assertEqual(clamp_scroll_amount(None), 3)

    def test_wait_seconds_null_defaults(self):
        a = holo_tool_to_action(
            {"tool_name": "wait", "seconds": None})
        self.assertEqual(a["seconds"], 1.0)

    def test_wait_seconds_invalid_raises_value_error(self):
        with self.assertRaises(ValueError):
            holo_tool_to_action(
                {"tool_name": "wait", "seconds": object()})

    def test_key_aliases(self):
        self.assertEqual(
            holo_tool_to_action(
                {"tool_name": "key", "combo": "Return"})["combo"],
            "Return")
        self.assertEqual(
            holo_tool_to_action(
                {"tool_name": "hotkey", "keys": "ctrl+l"})["combo"],
            "ctrl+l")

    def test_empty_key_combo_raises(self):
        with self.assertRaises(ValueError):
            holo_tool_to_action({"tool_name": "key", "combo": ""})
        with self.assertRaises(ValueError):
            holo_tool_to_action({"tool_name": "key", "combo": "  "})


class TestNormalizeHolo(unittest.TestCase):
    """T4 — normalize_decision holo."""

    def test_click_scaled(self):
        obj = {
            "note": "menu open",
            "thought": "click terminal",
            "tool_call": {
                "tool_name": "click", "x": 500, "y": 500,
            },
        }
        out = normalize_decision(
            "holo", obj, width=1280, height=800)
        self.assertEqual(out["reasoning"], "click terminal")
        self.assertEqual(out["action"]["type"], "click")
        self.assertEqual(out["action"]["x"], 640)
        self.assertEqual(out["action"]["y"], 400)
        self.assertEqual(out.get("note"), "menu open")

    def test_answer_to_done(self):
        obj = {
            "thought": "finished",
            "tool_call": {
                "tool_name": "answer", "content": "ok",
            },
        }
        out = normalize_decision(
            "holo", obj, width=1280, height=800)
        self.assertEqual(out["action"]["type"], "done")
        self.assertEqual(out["action"]["summary"], "ok")

    def test_tool_call_preferred_over_action(self):
        """When both present, use tool_call and scale (not raw action)."""
        obj = {
            "thought": "prefer tool",
            "tool_call": {
                "tool_name": "click", "x": 500, "y": 500,
            },
            "action": {"type": "click", "x": 1, "y": 2},
        }
        out = normalize_decision(
            "holo", obj, width=1280, height=800)
        self.assertEqual(out["action"]["x"], 640)
        self.assertEqual(out["action"]["y"], 400)

    def test_reasoning_from_thought_and_message(self):
        obj = {
            "tool_call": {
                "tool_name": "click", "x": 0, "y": 0,
            },
        }
        out = normalize_decision(
            "holo", obj, width=100, height=100,
            message={"reasoning": "from msg"})
        self.assertEqual(out["reasoning"], "from msg")

        obj2 = {
            "thought": "from thought",
            "tool_call": {
                "tool_name": "click", "x": 0, "y": 0,
            },
        }
        out2 = normalize_decision(
            "holo", obj2, width=100, height=100)
        self.assertEqual(out2["reasoning"], "from thought")

        obj3 = {
            "reasoning": "from reasoning",
            "thought": "ignored thought",
            "tool_call": {
                "tool_name": "click", "x": 0, "y": 0,
            },
        }
        out3 = normalize_decision(
            "holo", obj3, width=100, height=100)
        self.assertEqual(out3["reasoning"], "from reasoning")

    def test_fallback_keeps_thought_as_reasoning(self):
        obj = {
            "thought": "hybrid thought",
            "action": {"type": "wait", "seconds": 0.1},
        }
        out = normalize_decision(
            "holo", obj, width=1280, height=800)
        self.assertEqual(out["reasoning"], "hybrid thought")
        self.assertEqual(out["action"]["type"], "wait")


class TestNormalizeGeneric(unittest.TestCase):
    """T5 — normalize_decision generic (no rescale)."""

    def test_pass_through_pixels(self):
        obj = {
            "reasoning": "click mid",
            "action": {"type": "click", "x": 640, "y": 400},
        }
        out = normalize_decision(
            "generic", obj, width=1280, height=800)
        self.assertEqual(out["action"]["x"], 640)
        self.assertEqual(out["action"]["y"], 400)
        self.assertEqual(out["reasoning"], "click mid")

    def test_spawn_rejected_with_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_decision(
                "generic",
                {"reasoning": "x", "action": {"type": "spawn", "cmd": "id"}},
                width=10, height=10)
        self.assertIn("spawn", str(ctx.exception).lower())

    def test_scroll_amount_clamped_on_generic(self):
        out = normalize_decision(
            "generic",
            {"reasoning": "s", "action": {
                "type": "scroll", "direction": "down", "amount": 9999,
            }},
            width=10, height=10)
        self.assertEqual(out["action"]["amount"], SCROLL_AMOUNT_MAX)


class TestParseFallback(unittest.TestCase):
    """T6 — Holo backend accepts generic {reasoning,action} without scale."""

    def test_generic_shape_on_holo_no_scale(self):
        obj = {
            "reasoning": "already pixels",
            "action": {"type": "click", "x": 640, "y": 400},
        }
        out = normalize_decision(
            "holo", obj, width=1280, height=800)
        self.assertEqual(out["action"]["x"], 640)
        self.assertEqual(out["action"]["y"], 400)

    def test_spawn_via_generic_shape_on_holo_raises(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_decision(
                "holo",
                {"action": {"type": "spawn", "cmd": "id"}},
                width=10, height=10)
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "spawn" in msg or "forbidden" in msg or "unknown" in msg)

    def test_shell_via_generic_shape_on_holo_raises(self):
        for kind in ("shell", "bash", "run", "exec", "system"):
            with self.assertRaises(ValueError):
                normalize_decision(
                    "holo",
                    {"action": {"type": kind, "cmd": "id"}},
                    width=10, height=10)

    def test_allowlist_matches_agent_types(self):
        self.assertEqual(
            AGENT_ACTION_TYPES,
            frozenset({
                "click", "double_click", "right_click", "move", "type",
                "click_type", "key", "scroll", "wait", "done",
            }))


class TestBuildHoloBody(unittest.TestCase):
    """T7 — build_holo_request_body."""

    def test_fields(self):
        body = build_holo_request_body(
            model="holo3-1-35b-a3b",
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertIn("structured_outputs", body)
        self.assertIn("json", body["structured_outputs"])
        self.assertTrue(
            body["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(body["reasoning_effort"], "medium")
        self.assertEqual(body["temperature"], 0.8)
        self.assertNotIn("reasoning", body)
        self.assertNotIn("extra_body", body)
        self.assertNotIn("tools", body)


class TestBuildGenericBody(unittest.TestCase):
    """T8 — build_generic_request_body."""

    def test_temp_zero(self):
        body = build_generic_request_body(
            model="qwen", messages=[], base_url="http://localhost:11434/v1")
        self.assertEqual(body["temperature"], 0)
        self.assertNotIn("structured_outputs", body)
        self.assertNotIn("reasoning", body)

    def test_openrouter_reasoning(self):
        body = build_generic_request_body(
            model="claude",
            messages=[],
            base_url="https://openrouter.ai/api/v1",
        )
        self.assertEqual(body["reasoning"]["effort"], "low")
        self.assertNotIn("structured_outputs", body)

    def test_non_openrouter_no_reasoning(self):
        body = build_generic_request_body(
            model="x",
            messages=[],
            base_url="https://api.example.com/v1",
        )
        self.assertNotIn("reasoning", body)


class TestPrefillPolicy(unittest.TestCase):
    """T9 — Holo never ends messages with assistant prefill '{'."""

    def test_holo_ask_model_no_prefill(self):
        from agent import ask_model

        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            captured.append(body)
            content = json.dumps({
                "note": None,
                "thought": "click",
                "tool_call": {
                    "tool_name": "click", "x": 500, "y": 500,
                },
            })
            return httpx.Response(200, json={
                "choices": [{
                    "message": {"content": content},
                    "finish_reason": "stop",
                }],
            })

        http = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            out = ask_model(
                http,
                "https://api.hcompany.ai/v1",
                "k",
                "holo3-1-35b-a3b",
                "task",
                b"\x89PNG",
                [],
                (1280, 800),
                backend="auto",
            )
        finally:
            http.close()

        self.assertEqual(len(captured), 1)
        body = captured[0]
        roles = [m["role"] for m in body["messages"]]
        self.assertNotEqual(roles[-1], "assistant")
        for m in body["messages"]:
            if m["role"] == "assistant":
                self.fail("Holo path must not send assistant prefill")
        self.assertIn("structured_outputs", body)
        self.assertEqual(body["temperature"], 0.8)
        self.assertEqual(out["action"]["type"], "click")
        self.assertEqual(out["action"]["x"], 640)
        self.assertEqual(out["action"]["y"], 400)

    def test_generic_still_prefills_when_allowed(self):
        from agent import ask_model

        captured: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            captured.append(body)
            # continuation after prefill
            return httpx.Response(200, json={
                "choices": [{
                    "message": {
                        "content": (
                            '"reasoning":"ok",'
                            '"action":{"type":"wait","seconds":0.1}}'
                        ),
                    },
                    "finish_reason": "stop",
                }],
            })

        http = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            out = ask_model(
                http,
                "http://localhost:11434/v1",
                "k",
                "qwen2.5vl",
                "task",
                b"\x89PNG",
                [],
                (10, 10),
                backend="generic",
            )
        finally:
            http.close()

        body = captured[0]
        self.assertEqual(body["messages"][-1]["role"], "assistant")
        self.assertEqual(body["messages"][-1]["content"], "{")
        self.assertEqual(body["temperature"], 0)
        self.assertNotIn("structured_outputs", body)
        self.assertEqual(out["action"]["type"], "wait")


class TestParseHelpers(unittest.TestCase):
    def test_extract_list_content(self):
        text = extract_message_content({
            "content": [
                {"type": "text", "text": "ab"},
                {"type": "text", "text": "cd"},
            ],
        })
        self.assertEqual(text, "abcd")

    def test_parse_with_prefill(self):
        obj = parse_json_object_from_text(
            '"a":1}', use_prefill=True)
        self.assertEqual(obj, {"a": 1})

    def test_parse_without_prefill(self):
        obj = parse_json_object_from_text(
            '{"a": 2}', use_prefill=False)
        self.assertEqual(obj, {"a": 2})

    def test_schema_has_no_spawn(self):
        raw = json.dumps(holo_step_json_schema())
        self.assertNotIn("spawn", raw)
        self.assertNotIn("shell", raw)

    def test_system_prompt_embeds_schema(self):
        p = system_prompt_holo()
        self.assertIn("<output_format>", p)
        self.assertIn("tool_name", p)
        self.assertIn("[0, 1000]", p)

    def test_system_prompt_close_guidance(self):
        p = system_prompt_holo()
        self.assertIn("alt+F4", p)
        self.assertIn("far right of the title", p)
        # Must not blanket-ban title-bar close (broke close-browser tasks).
        self.assertNotIn("avoid title-bar close buttons", p)


class TestGenericBodyNoHoloFields(unittest.TestCase):
    """T10 partial — generic body never injects Holo fields."""

    def test_openrouter_body_snapshot(self):
        body = build_generic_request_body(
            model="anthropic/claude-haiku-4.5",
            messages=[{"role": "user", "content": "x"}],
            base_url="https://openrouter.ai/api/v1",
        )
        for k in (
            "structured_outputs",
            "chat_template_kwargs",
            "reasoning_effort",
            "extra_body",
        ):
            self.assertNotIn(k, body)


if __name__ == "__main__":
    unittest.main()
