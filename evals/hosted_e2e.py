#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""End-to-end checks: control plane RemoteDesktop -> live sandbox.

Requires a running desktop-sandbox (Docker or host).

    SANDBOX_URL=http://127.0.0.1:7090 uv run evals/hosted_e2e.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote import RemoteDesktop  # noqa: E402


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise AssertionError(name)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sandbox-url",
                   default=os.environ.get("SANDBOX_URL",
                                          "http://127.0.0.1:7090"))
    p.add_argument("--sandbox-token",
                   default=os.environ.get("SANDBOX_TOKEN", ""))
    p.add_argument("--stream-url",
                   default=os.environ.get("STREAM_URL", ""))
    args = p.parse_args()

    print(f"hosted e2e -> {args.sandbox_url}")
    desk = RemoteDesktop(
        args.sandbox_url,
        token=args.sandbox_token or "",
        stream_url=args.stream_url or None,
    )
    fails = 0
    try:
        # 1. geometry
        try:
            check("geometry", desk.width >= 640 and desk.height >= 480,
                  f"{desk.width}x{desk.height}")
            check("display label", bool(desk.name))
        except AssertionError:
            fails += 1

        # 2. screenshot PNG
        try:
            png = desk.screenshot_png()
            check("png magic", png[:8] == b"\x89PNG\r\n\x1a\n")
            check("png size", len(png) > 500, f"{len(png)} bytes")
        except AssertionError:
            fails += 1

        # 3. stream hint
        try:
            stream = desk.stream_url or ""
            check("stream_ws present", "ws" in stream.lower(), stream or "(none)")
        except AssertionError:
            fails += 1

        # 4. input actions
        cx, cy = desk.width // 2, desk.height // 2
        for action in (
            {"type": "move", "x": cx, "y": cy},
            {"type": "click", "x": cx, "y": cy},
            {"type": "wait", "seconds": 0.2},
            {"type": "key", "combo": "Escape"},
            {"type": "scroll", "direction": "down", "amount": 1},
        ):
            try:
                if action["type"] == "wait":
                    # wait is control-plane local in agent.execute; remote
                    # may still accept it. Prefer desk path when available.
                    t0 = time.time()
                    try:
                        desk.execute(action)
                    except RuntimeError:
                        time.sleep(float(action["seconds"]))
                    check(f"action {action['type']}", True,
                          f"{time.time() - t0:.2f}s")
                else:
                    desk.execute(action)
                    check(f"action {action['type']}", True)
            except Exception as e:
                print(f"  [FAIL] action {action['type']} — {e}")
                fails += 1

        # 5. second screenshot still works after input
        try:
            png2 = desk.screenshot_png()
            check("post-input screenshot",
                  png2[:8] == b"\x89PNG\r\n\x1a\n", f"{len(png2)} bytes")
        except AssertionError:
            fails += 1

        if fails:
            print(f"hosted_e2e FAILED ({fails} check group(s))")
            return 1
        print("hosted_e2e PASSED")
        return 0
    finally:
        desk.close()


if __name__ == "__main__":
    sys.exit(main())
