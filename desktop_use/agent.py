"""Minimal local computer-use loop: screenshot -> VLM -> XTest input.

A from-scratch re-draft of the Holo runtime loop with no H Company gateway
dependency. Talks to any OpenAI-compatible /v1/chat/completions endpoint
(Ollama, vLLM, llama.cpp, LM Studio) with a vision model behind it.

Designed for a nested X display (Xephyr) so it never touches the real desktop:

    Xephyr :2 -screen 1280x800 &
    DISPLAY=:2 setxkbmap us
    DISPLAY=:2 openbox &
    uv run python -m desktop_use.agent --display :2 --probe   # pipeline check
    uv run python -m desktop_use.agent --display :2 "open a terminal and run ls"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
from Xlib import XK, X, display as xdisplay
from Xlib.ext import xtest

from .model_backends import (
    AGENT_ACTION_TYPES,
    build_generic_request_body,
    build_holo_request_body,
    clamp_scroll_amount,
    extract_message_content,
    normalize_decision,
    parse_json_object_from_text,
    resolve_model_backend,
    system_prompt_holo,
)

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
missed or the action failed: re-examine the screenshot and try a DIFFERENT
approach (not a 1-10px micro-adjust of the same click). Prefer keyboard
shortcuts when a click target is tiny.

When you receive both a before and a current screenshot, compare them: if a
window you were using has disappeared, your own last action probably closed
it.

Closing windows or apps (when the task asks to close/quit/dismiss):
- Prefer {{"type": "key", "combo": "alt+F4"}} after the target window is
  focused (click its title bar once if unsure).
- Or click the WINDOW frame close button (X) at the far right of the title
  bar - not a tab's small X, not mid-title-bar, not the page content.
- When the task is NOT to close anything, avoid accidental title-bar X clicks.

Text fields do NOT visibly change when they gain focus - a screenshot will
look the same whether or not your click landed. Never click the same input
twice: use "click_type" to click and type in one action, or after one plain
click assume focus and type.

Only emit "done" with success=true when the CURRENT screenshot visually
confirms the goal state (e.g. the requested output is readable on screen).
A mostly black or empty screen means nothing is open yet - typing would go
nowhere. Never assume an action worked without seeing the result.

Desktop environment (openbox sandbox):
- The desktop always has two app icons: Terminal and Browser (Chromium).
  Open them with double_click on the icon, or keyboard shortcuts only:
  Terminal = {{"type": "key", "combo": "ctrl+alt+t"}} ;
  Browser = {{"type": "key", "combo": "ctrl+alt+b"}}.
  Never use right-click or menus to launch apps (they will not start).
- If a Chromium window is already visible, use THAT window (address bar
  or ctrl+l). Do not launch a second browser. No Firefox.
- Other files in ~/Desktop show as icons. Create from a terminal
  (echo hello > ~/Desktop/notes.txt); double-click a .txt icon for
  mousepad.
- After a hotkey or icon open, confirm the new window before typing.
"""

# binary -> Debian/Ubuntu package, for the preflight error message
APT_PACKAGES = {
    "Xephyr": "xserver-xephyr", "openbox": "openbox", "scrot": "scrot",
    "xterm": "xterm", "x11vnc": "x11vnc", "websockify": "websockify",
}


def require_binaries(names: list[str]) -> None:
    """Fail fast with an install hint instead of dying mid-spawn."""
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        sys.exit(
            "missing required system binaries: " + ", ".join(missing)
            + "\ninstall on Debian/Ubuntu:  sudo apt install "
            + " ".join(APT_PACKAGES.get(n, n) for n in missing))


KEYSYM_ALIASES = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "ctrl": "Control_L", "control": "Control_L", "alt": "Alt_L",
    "shift": "Shift_L", "super": "Super_L", "meta": "Super_L",
    # X keysyms are F1..F12 (capital F). Models often emit alt+f4.
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
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
        n = clamp_scroll_amount(amount)
        for _ in range(n):
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


def _supports_assistant_prefill(model: str) -> bool:
    """Some Anthropic routes reject trailing assistant prefill entirely.

    Claude 5 family (sonnet-5 / opus-5 / haiku-5 and claude-*-5 slugs) must
    end on a user message. Claude 4.5 and earlier still accept prefill on
    typical OpenRouter/compat routes, so leave them alone.
    """
    m = model.lower()
    # Claude 5 family: conversation must end on user.
    if "sonnet-5" in m or "opus-5" in m or "haiku-5" in m:
        return False
    if re.search(r"claude-(sonnet|opus|haiku)-5(?:\b|[.-])", m):
        return False
    if re.search(r"claude-5(?:\b|[.-])", m):
        return False
    return True


def _is_real_display(name: str) -> bool:
    """True for the user's real X session (:0, :0.0, :1.1, …)."""
    if not name:
        return False
    try:
        # ":0", ":0.0", "localhost:1.0" -> display number before optional .screen
        host_disp = name.rsplit(":", 1)[-1]
        num = int(host_disp.split(".", 1)[0])
    except (ValueError, IndexError):
        return False
    return num in (0, 1)


# AGENT_ACTION_TYPES imported from model_backends (shared allowlist).


def ask_model(
    http: httpx.Client, base_url: str, api_key: str, model: str,
    task: str, png: bytes, history: list[str], size: tuple[int, int],
    prev_png: bytes | None = None, complaint: str | None = None,
    backend: str | None = None,
) -> dict:
    """Call the model and return {reasoning, action} with pixel coords.

    backend: auto|generic|holo|None — resolved via resolve_model_backend.
    Holo path: no prefill, structured_outputs, scale [0,1000]→pixels.
    Generic path: unchanged pixels + prefill policy + OpenRouter gate.
    """
    resolved = resolve_model_backend(base_url, model, backend)
    width, height = size[0], size[1]
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
    if resolved == "holo":
        system = system_prompt_holo()
        use_prefill = False
    else:
        system = SYSTEM_PROMPT.format(width=width, height=height)
        # prefill forces JSON continuation on models that allow it; Claude 5
        # rejects trailing assistant messages ("must end with a user message").
        use_prefill = _supports_assistant_prefill(model)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    if use_prefill:
        # Mistral platform rejects trailing assistant without prefix=True
        # ("Expected last role User or Tool (or Assistant with prefix True)").
        prefill_msg: dict = {"role": "assistant", "content": "{"}
        mlow = model.lower()
        bu = (base_url or "").lower()
        if (
            "mistral.ai" in bu
            or mlow.startswith("mistral")
            or mlow.startswith("ministral")
            or mlow.startswith("pixtral")
        ):
            prefill_msg["prefix"] = True
        messages.append(prefill_msg)
    if resolved == "holo":
        body = build_holo_request_body(model=model, messages=messages)
    else:
        body = build_generic_request_body(
            model=model, messages=messages, base_url=base_url)
    resp = http.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=180,
    )
    try:
        payload = resp.json()
    except json.JSONDecodeError:
        raise ValueError(
            f"model API error (HTTP {resp.status_code}): "
            f"{(resp.text or '')[:400]!r}") from None
    if resp.status_code >= 400 or "choices" not in payload:
        err = payload.get("error", payload) if isinstance(payload, dict) \
            else payload
        if isinstance(err, dict):
            msg = err.get("message") or err.get("metadata", {}).get("raw") \
                or json.dumps(err)[:400]
        else:
            msg = str(err)[:400]
        raise ValueError(
            f"model API error (HTTP {resp.status_code}): {msg}")
    choice = payload["choices"][0]
    message = choice.get("message") or {}
    text = extract_message_content(message)
    try:
        obj = parse_json_object_from_text(text, use_prefill=use_prefill)
    except ValueError as e:
        raise ValueError(
            f"unparseable model reply "
            f"(finish_reason={choice.get('finish_reason')}): "
            f"{text[:200]!r}") from e
    return normalize_decision(
        resolved, obj, width=width, height=height, message=message)


def execute(desk, action: dict) -> str:
    """Dispatch one action to a local Desktop or RemoteDesktop.

    wait is always local so the control plane owns the delay. Remote
    sandboxes receive the full action dict via desk.execute(action).
    Unknown / sandbox-only types (e.g. spawn) are rejected, never POSTed.
    """
    kind = action.get("type")
    if kind == "wait":
        time.sleep(min(float(action.get("seconds", 1)), 5))
        return json.dumps(action)
    if kind not in AGENT_ACTION_TYPES:
        return f"unknown action {kind!r}, ignored"

    # RemoteDesktop: whole action over HTTP (see remote.py). Prefer an
    # explicit marker so wrappers/mocks still hit the whole-action path.
    if getattr(desk, "is_remote", False):
        return desk.execute(action)

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
        desk.scroll(action.get("direction", "down"),
                    clamp_scroll_amount(action.get("amount", 3)))
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


def probe(desk):
    print(f"display {desk.name}: {desk.width}x{desk.height}")
    png = desk.screenshot_png()
    out = os.path.join(tempfile.gettempdir(), "desktop-use-probe.png")
    with open(out, "wb") as f:
        f.write(png)
    print(f"screenshot ok ({len(png)} bytes) -> {out}")
    cx, cy = desk.width // 2, desk.height // 2
    desk.move(cx, cy)
    kind = "sandbox API" if getattr(desk, "is_remote", False) else "XTest"
    print(f"pointer moved to screen center via {kind}")
    print("probe passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("task", nargs="?", help="natural-language task")
    parser.add_argument("--display", default=None,
                        help="attach to an existing X display and leave it "
                             "running; default is to spawn a private Xephyr "
                             ":2 and tear it down on exit")
    parser.add_argument("--sandbox-url",
                        default=(os.environ.get("SANDBOX_URL")
                                 or os.environ.get("DESKTOP_SANDBOX_URL")
                                 or "") or None,
                        help="desktop-sandbox API base "
                             "(e.g. http://127.0.0.1:7090); skips local Xephyr")
    parser.add_argument("--stream-url",
                        default=(os.environ.get("STREAM_URL")
                                 or os.environ.get("DESKTOP_STREAM_URL")
                                 or "") or None,
                        help="noVNC/websockify websocket URL for live view "
                             "(console; accepted here for env symmetry)")
    parser.add_argument("--sandbox-token",
                        default=(os.environ.get("SANDBOX_TOKEN")
                                 or os.environ.get("DESKTOP_SANDBOX_TOKEN")
                                 or "") or None,
                        help="Bearer / X-Sandbox-Token for the sandbox API")
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
        help="model harness: auto (detect Holo from URL/model), "
             "generic (pixels + prefill), or holo (structured [0,1000])")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--allow-real-display", action="store_true",
                        help="permit running on :0 / :1 (the real session)")
    parser.add_argument("--probe", action="store_true",
                        help="verify screenshot + input, no model needed")
    args = parser.parse_args()

    if not args.probe and not args.task:
        parser.error("a task is required unless --probe is given")

    if args.sandbox_url and args.display is not None:
        parser.error("--sandbox-url and --display are mutually exclusive")

    if args.sandbox_url:
        from remote import RemoteDesktop
        desk = RemoteDesktop(
            args.sandbox_url,
            token=args.sandbox_token or "",
            stream_url=args.stream_url,
        )
        print(f"remote sandbox: {args.sandbox_url}  "
              f"({desk.width}x{desk.height})")
        if desk.stream_url:
            print(f"stream: {desk.stream_url}")
        try:
            run(args, desk=desk)
        finally:
            desk.close()
        return

    if (args.display is not None and _is_real_display(args.display)
            and not args.allow_real_display):
        sys.exit(f"refusing to drive {args.display} (the real session); "
                 "use a nested display like Xephyr :2, "
                 "or pass --allow-real-display")

    require_binaries(["scrot"] if args.display is not None
                     else ["Xephyr", "openbox", "scrot", "xterm"])
    if args.display is None:
        with ManagedEnv() as display:
            run(argparse.Namespace(**{**vars(args), "display": display}))
    else:
        run(args)


def run(args, desk=None):
    if desk is None:
        desk = Desktop(args.display)

    if args.probe:
        probe(desk)
        return

    backend_flag = getattr(args, "model_backend", "auto")
    resolved = resolve_model_backend(
        args.base_url, args.model, backend_flag)
    print(f"task: {args.task}")
    print(f"display {desk.name} ({desk.width}x{desk.height}), "
          f"model {args.model} @ {args.base_url}, backend {resolved}, "
          f"max {args.max_steps} steps")

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
                                         complaint=complaint,
                                         backend=backend_flag)
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
                                     "desktop-use-final.png")
                with open(final, "wb") as f:
                    f.write(desk.screenshot_png())
                print(f"done ({status}): {action.get('summary', '')}")
                print(f"final screenshot: {final}")
                return

            result = execute(desk, action)
            time.sleep(0.8)  # let the UI settle before the next screenshot
            after = desk.screenshot_png()
            changed = ("screen changed" if after != png
                       else "screen did NOT change")
            history.append(f"step {step}: {result} -> {changed}")
            prev_png = png

    print(f"stopped: hit --max-steps {args.max_steps} without a done action")
    sys.exit(2)


if __name__ == "__main__":
    main()
