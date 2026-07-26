#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Live smoke against a running desktop-sandbox.

Skipped unless SANDBOX_URL is set (or --sandbox-url is passed):

    SANDBOX_URL=http://127.0.0.1:7090 uv run evals/remote_smoke.py
    uv run evals/remote_smoke.py --sandbox-url http://127.0.0.1:7090
"""

from __future__ import annotations

import argparse
import os
import sys

# repo root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from desktop_use.remote import RemoteDesktop  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sandbox-url",
                   default=os.environ.get("SANDBOX_URL", ""))
    p.add_argument("--sandbox-token",
                   default=os.environ.get("SANDBOX_TOKEN", ""))
    args = p.parse_args()
    if not args.sandbox_url:
        print("skip: set SANDBOX_URL or pass --sandbox-url")
        return 0

    print(f"connecting {args.sandbox_url} ...")
    desk = RemoteDesktop(
        args.sandbox_url, token=args.sandbox_token or "")
    try:
        print(f"  display={desk.name}  size={desk.width}x{desk.height}")
        if desk.stream_url:
            print(f"  stream={desk.stream_url}")
        png = desk.screenshot_png()
        magic = "png" if png.startswith(b"\x89PNG") else "raw"
        print(f"  screenshot: {len(png)} bytes ({magic})")
        if magic != "png":
            print("  warn: body does not start with PNG magic")
        desk.execute({
            "type": "move",
            "x": desk.width // 2,
            "y": desk.height // 2,
        })
        print("  move: ok")
        print("remote smoke passed")
        return 0
    finally:
        desk.close()


if __name__ == "__main__":
    sys.exit(main())
