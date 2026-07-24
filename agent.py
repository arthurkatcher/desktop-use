# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx>=0.27",
#     "python-xlib>=0.33",
# ]
# ///
"""Minimal local computer-use loop: screenshot -> VLM -> XTest input.

A from-scratch re-draft of the Holo runtime loop with no H Company gateway
dependency. Talks to any OpenAI-compatible /v1/chat/completions endpoint
(Ollama, vLLM, llama.cpp, LM Studio) with a vision model behind it.

Designed for a nested X display (Xephyr) so it never touches the real desktop:

    Xephyr :2 -screen 1280x800 &
    DISPLAY=:2 setxkbmap us
    DISPLAY=:2 openbox &
    uv run agent.py --display :2 --probe          # pipeline check, no model
    uv run agent.py --display :2 "open a terminal and run ls"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import httpx
from Xlib import XK, X, display as xdisplay
from Xlib.ext import xtest

SYSTEM_PROMPT = """\
You are a computer-use agent controlling a Linux desktop through screenshots.
Each turn you receive the current screenshot ({width}x{height} pixels) and the
history of your previous actions. Decide ONE next action.

Respond with ONLY a raw JSON object - no markdown fences, no tool-call or
<function_calls> syntax - in this shape:
{{"reasoning": "<one short sentence>", "action": {{...}}}}

Available actions:
  {{"type": "click", "x": <int>, "y": <int>}}
  {{"type": "double_click", "x": <int>, "y": <int>}}
  {{"type": "right_click", "x": <int>, "y": <int>}}
  {{"type": "move", "x": <int>, "y": <int>}}
  {{"type": "type", "text": "<text to type>"}}
  {{"type": "click_type", "x": <int>, "y": <int>, "text": "<text>"}}  (click a text field, then type into it - PREFER this for any input field)
  {{"type": "key", "combo": "<e.g. Return, ctrl+l, alt+F4>"}}
  {{"type": "scroll", "direction": "up"|"down", "amount": <clicks, default 3>}}
  {{"type": "wait", "seconds": <float, max 5>}}
  {{"type": "done", "success": true|false, "summary": "<what happened>"}}

Coordinates are absolute pixels in the screenshot: (0,0) is top-left.
Click precisely on the center of targets. Each history entry notes whether the
screen actually changed after that action - if it did not change, your click
missed or the action failed: re-examine the screenshot and try different
coordinates or a different approach, never repeat the same action blindly.

When you receive both a before and a current screenshot, compare them: if a
window you were using has disappeared, your own last action probably closed
it - be careful where you click (avoid title-bar close buttons).

Text fields do NOT visibly change when they gain focus - a screenshot will
look the same whether or not your click landed. Never click the same input
twice: use "click_type" to click and type in one action, or after one plain
click assume focus and type.

Only emit "done" with success=true when the CURRENT screenshot visually
confirms the goal state (e.g. the requested output is readable on screen).
A mostly black or empty screen means nothing is open yet - typing would go
nowhere. Never assume an action worked without seeing the result.
"""

KEYSYM_ALIASES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "ctrl": "Control_L", "control": "Control_L", "alt": "Alt_L",
    "shift": "Shift_L", "super": "Super_L", "meta": "Super_L",
}


class Desktop:
    """Capture + synthetic input for one X display."""

    def __init__(self, display_name: str):
        self.name = display_name
        self.dpy = xdisplay.Display(display_name)
        if not self.dpy.has_extension("XTEST"):
            sys.exit(f"error: display {display_name} lacks the XTEST extension")
        screen = self.dpy.screen()
        self.width = screen.width_in_pixels
        self.height = screen.height_in_pixels

    def screenshot_png(self) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            subprocess.run(
                ["scrot", "--overwrite", path],
                env={**os.environ, "DISPLAY": self.name},
                check=True, capture_output=True,
            )
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    def _flush(self):
        self.dpy.sync()

    def move(self, x: int, y: int):
        xtest.fake_input(self.dpy, X.MotionNotify, x=x, y=y)
        self._flush()

    def click(self, x: int, y: int, button: int = 1, times: int = 1):
        self.move(x, y)
        time.sleep(0.05)
        for _ in range(times):
            xtest.fake_input(self.dpy, X.ButtonPress, button)
            xtest.fake_input(self.dpy, X.ButtonRelease, button)
            self._flush()
            time.sleep(0.06)

    def scroll(self, direction: str, amount: int = 3):
        button = 4 if direction == "up" else 5
        for _ in range(max(1, amount)):
            xtest.fake_input(self.dpy, X.ButtonPress, button)
            xtest.fake_input(self.dpy, X.ButtonRelease, button)
            self._flush()
            time.sleep(0.03)

    def _keysym_to_keycode(self, keysym: int) -> tuple[int, bool]:
        """Return (keycode, needs_shift) for a keysym, or (0, False)."""
        keycode = self.dpy.keysym_to_keycode(keysym)
        if keycode == 0:
            return 0, False
        plain = self.dpy.keycode_to_keysym(keycode, 0)
        return keycode, plain != keysym

    def _press_keycode(self, keycode: int, shift: bool):
        shift_code = self.dpy.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        if shift:
            xtest.fake_input(self.dpy, X.KeyPress, shift_code)
        xtest.fake_input(self.dpy, X.KeyPress, keycode)
        xtest.fake_input(self.dpy, X.KeyRelease, keycode)
        if shift:
            xtest.fake_input(self.dpy, X.KeyRelease, shift_code)
        self._flush()

    def type_text(self, text: str):
        for ch in text:
            if ch == "\n":
                keysym = XK.string_to_keysym("Return")
            else:
                keysym = XK.string_to_keysym(ch) or ord(ch)
            keycode, shift = self._keysym_to_keycode(keysym)
            if keycode == 0:
                print(f"    ! cannot type {ch!r} with current keymap, skipping")
                continue
            self._press_keycode(keycode, shift)
            time.sleep(0.02)

    def key_combo(self, combo: str):
        parts = [p.strip() for p in combo.split("+") if p.strip()]
        if not parts:
            return
        keysyms = []
        for part in parts:
            name = KEYSYM_ALIASES.get(part.lower(), part)
            keysym = XK.string_to_keysym(name)
            if keysym == 0 and len(part) == 1:
                keysym = ord(part)
            if keysym == 0:
                print(f"    ! unknown key {part!r} in combo {combo!r}, skipping combo")
                return
            keysyms.append(keysym)
        codes = []
        for keysym in keysyms:
            keycode, _ = self._keysym_to_keycode(keysym)
            if keycode == 0:
                print(f"    ! keysym for {combo!r} not in keymap, skipping combo")
                return
            codes.append(keycode)
        for code in codes:
            xtest.fake_input(self.dpy, X.KeyPress, code)
        for code in reversed(codes):
            xtest.fake_input(self.dpy, X.KeyRelease, code)
        self._flush()


def ask_model(
    http: httpx.Client, base_url: str, api_key: str, model: str,
    task: str, png: bytes, history: list[str], size: tuple[int, int],
    prev_png: bytes | None = None, complaint: str | None = None,
) -> dict:
    history_text = "\n".join(history[-20:]) or "(no actions yet)"
    content: list[dict] = [
        {"type": "text",
         "text": f"Task: {task}\n\nActions so far:\n{history_text}"},
    ]
    if complaint:
        content.append(
            {"type": "text",
             "text": "IMPORTANT: your previous reply was rejected: "
                     f"{complaint}\nAnswer again with ONLY one syntactically "
                     "valid JSON object in the required shape (every action "
                     "field present, e.g. click needs both \"x\" and \"y\")."})
    if prev_png is not None:
        content += [
            {"type": "text",
             "text": "Screenshot from BEFORE your previous action "
                     "(compare with the current one to judge what your "
                     "action really did):"},
            {"type": "image_url", "image_url": {"url":
                "data:image/png;base64,"
                + base64.b64encode(prev_png).decode()}},
        ]
    content += [
        {"type": "text", "text": "Current screenshot:"},
        {"type": "image_url", "image_url": {"url":
            "data:image/png;base64," + base64.b64encode(png).decode()}},
    ]
    messages = [
        {"role": "system",
         "content": SYSTEM_PROMPT.format(width=size[0], height=size[1])},
        {"role": "user", "content": content},
        # prefill: the reply can only continue as JSON, which stops
        # tool-call-syntax slips (<function_calls>...) at the source
        {"role": "assistant", "content": "{"},
    ]
    body = {"model": model, "messages": messages, "temperature": 0,
            "max_tokens": 4000}
    if "openrouter" in base_url:
        # some hosted models have mandatory hidden thinking that counts
        # against max_tokens; keep it minimal so the JSON always fits
        body["reasoning"] = {"effort": "low"}
    resp = http.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=180,
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    text = choice["message"]["content"]
    # backends that honor the prefill return a continuation ("..."} ), those
    # that ignore it return a full object ({...}) - try both readings
    obj = None
    for candidate in ("{" + text, text):
        start = candidate.find("{")
        if start == -1:
            continue
        try:
            obj, _ = json.JSONDecoder().raw_decode(candidate[start:])
            break
        except json.JSONDecodeError:
            continue
    if obj is None:
        raise ValueError(f"unparseable model reply "
                         f"(finish_reason={choice.get('finish_reason')}): "
                         f"{text[:200]!r}")
    action = obj.get("action") if isinstance(obj, dict) else None
    if not isinstance(action, dict) or "type" not in action:
        raise ValueError('reply JSON lacks an "action" object with a "type" '
                         f"field: {text[:200]!r}")
    return obj


def execute(desk: Desktop, action: dict) -> str:
    kind = action.get("type")
    if kind == "click":
        desk.click(int(action["x"]), int(action["y"]))
    elif kind == "double_click":
        desk.click(int(action["x"]), int(action["y"]), times=2)
    elif kind == "right_click":
        desk.click(int(action["x"]), int(action["y"]), button=3)
    elif kind == "move":
        desk.move(int(action["x"]), int(action["y"]))
    elif kind == "type":
        desk.type_text(str(action["text"]))
    elif kind == "click_type":
        desk.click(int(action["x"]), int(action["y"]))
        time.sleep(0.2)
        desk.type_text(str(action["text"]))
    elif kind == "key":
        desk.key_combo(str(action["combo"]))
    elif kind == "scroll":
        desk.scroll(action.get("direction", "down"), int(action.get("amount", 3)))
    elif kind == "wait":
        time.sleep(min(float(action.get("seconds", 1)), 5))
    else:
        return f"unknown action {kind!r}, ignored"
    return json.dumps(action)


class ManagedEnv:
    """Own Xephyr + openbox lifecycle: spawned on enter, killed on exit."""

    def __init__(self, display: str = ":2", size: str = "1280x800"):
        self.display = display
        self.procs: list[subprocess.Popen] = []

    def __enter__(self) -> str:
        socket = f"/tmp/.X11-unix/X{self.display.lstrip(':')}"
        if os.path.exists(socket):
            raise SystemExit(
                f"display {self.display} already exists; attach to it with "
                f"--display {self.display} or stop it first")
        self.procs.append(subprocess.Popen(
            ["Xephyr", self.display, "-screen", "1280x800"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(50):
            if os.path.exists(socket):
                break
            time.sleep(0.1)
        else:
            self.__exit__(None, None, None)
            raise SystemExit("Xephyr failed to start")
        # scrub Wayland so apps launched inside the session (via the openbox
        # menu) bind to the nested display instead of the real desktop
        env = {k: v for k, v in os.environ.items()
               if k not in ("WAYLAND_DISPLAY", "GNOME_SETUP_DISPLAY")}
        env.update(DISPLAY=self.display, XDG_SESSION_TYPE="x11")
        subprocess.run(["setxkbmap", "us"], env=env, check=False,
                       capture_output=True)
        self.procs.append(subprocess.Popen(
            ["openbox"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(0.5)
        print(f"managed env: Xephyr {self.display} + openbox up")
        return self.display

    def __exit__(self, *exc) -> None:
        for proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print(f"managed env: {self.display} torn down")


def probe(desk: Desktop):
    print(f"display {desk.name}: {desk.width}x{desk.height}")
    png = desk.screenshot_png()
    out = os.path.join(tempfile.gettempdir(), "local-loop-probe.png")
    with open(out, "wb") as f:
        f.write(png)
    print(f"screenshot ok ({len(png)} bytes) -> {out}")
    desk.move(desk.width // 2, desk.height // 2)
    print("pointer moved to screen center via XTest")
    print("probe passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("task", nargs="?", help="natural-language task")
    parser.add_argument("--display", default=None,
                        help="attach to an existing X display and leave it "
                             "running; default is to spawn a private Xephyr "
                             ":2 and tear it down on exit")
    parser.add_argument("--base-url",
                        default=os.environ.get("OPENAI_BASE_URL",
                                               "http://localhost:11434/v1"))
    parser.add_argument("--model",
                        default=os.environ.get("LOCAL_LOOP_MODEL", "qwen2.5vl"))
    parser.add_argument("--api-key",
                        default=os.environ.get("OPENAI_API_KEY", "local"))
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--allow-real-display", action="store_true",
                        help="permit running on :0 / :1 (the real session)")
    parser.add_argument("--probe", action="store_true",
                        help="verify screenshot + XTest input, no model needed")
    args = parser.parse_args()

    if args.display in (":0", ":1") and not args.allow_real_display:
        sys.exit(f"refusing to drive {args.display} (the real session); "
                 "use a nested display like Xephyr :2, "
                 "or pass --allow-real-display")

    if not args.probe and not args.task:
        parser.error("a task is required unless --probe is given")

    if args.display is None:
        with ManagedEnv() as display:
            run(argparse.Namespace(**{**vars(args), "display": display}))
    else:
        run(args)


def run(args):
    desk = Desktop(args.display)

    if args.probe:
        probe(desk)
        return

    print(f"task: {args.task}")
    print(f"display {desk.name} ({desk.width}x{desk.height}), "
          f"model {args.model} @ {args.base_url}, max {args.max_steps} steps")

    history: list[str] = []
    prev_png: bytes | None = None
    with httpx.Client() as http:
        for step in range(1, args.max_steps + 1):
            png = desk.screenshot_png()
            decision = None
            complaint = None
            for attempt in (1, 2, 3):
                try:
                    decision = ask_model(http, args.base_url, args.api_key,
                                         args.model, args.task, png, history,
                                         (desk.width, desk.height),
                                         prev_png=prev_png,
                                         complaint=complaint)
                    break
                except (httpx.HTTPError, ValueError) as e:
                    complaint = str(e)
                    print(f"    ! model call failed (attempt {attempt}): {e}")
            if decision is None:
                sys.exit(f"step {step}: model call failed 3 times, giving up")

            reasoning = decision.get("reasoning", "")
            action = decision.get("action", {})
            print(f"[{step}/{args.max_steps}] {reasoning}")
            print(f"    -> {json.dumps(action)}")

            if action.get("type") == "done":
                status = "success" if action.get("success") else "FAILED"
                final = os.path.join(tempfile.gettempdir(),
                                     "local-loop-final.png")
                with open(final, "wb") as f:
                    f.write(desk.screenshot_png())
                print(f"done ({status}): {action.get('summary', '')}")
                print(f"final screenshot: {final}")
                return

            result = execute(desk, action)
            time.sleep(0.8)  # let the UI settle before the next screenshot
            after = desk.screenshot_png()
            changed = "screen changed" if after != png else "screen did NOT change"
            history.append(f"step {step}: {result} -> {changed}")
            prev_png = png

    print(f"stopped: hit --max-steps {args.max_steps} without a done action")
    sys.exit(2)


if __name__ == "__main__":
    main()
