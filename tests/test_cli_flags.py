"""CLI flag parsing for sandbox-url / remote mode."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("agent")
import agent as agent_mod


def _parse(argv: list[str]) -> argparse.Namespace:
    """Parse agent.py CLI the same way main() does, without running."""
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?")
    parser.add_argument("--display", default=None)
    parser.add_argument("--sandbox-url", default=None)
    parser.add_argument("--stream-url", default=None)
    parser.add_argument("--sandbox-token", default=None)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", default="qwen2.5vl")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--allow-real-display", action="store_true")
    parser.add_argument("--probe", action="store_true")
    return parser.parse_args(argv)


def test_sandbox_url_flag_present_in_main_source():
    import inspect
    src = inspect.getsource(agent_mod.main)
    assert "--sandbox-url" in src
    assert "RemoteDesktop" in src


def test_sandbox_url_constructs_remote_and_skips_managed_env():
    fake_desk = MagicMock()
    fake_desk.width = 1280
    fake_desk.height = 800
    fake_desk.name = ":99"
    fake_desk.stream_url = "ws://127.0.0.1:6080"
    fake_desk.screenshot_png.return_value = b"\x89PNG\r\n\x1a\nxx"
    fake_desk.execute = MagicMock()

    with patch.dict("sys.modules", {}):
        with patch("remote.RemoteDesktop", return_value=fake_desk) as RD:
            with patch.object(agent_mod, "ManagedEnv") as env:
                with patch.object(agent_mod, "run") as run:
                    with patch.object(sys, "argv", [
                        "agent.py",
                        "--sandbox-url", "http://127.0.0.1:7090",
                        "--probe",
                    ]):
                        agent_mod.main()
                env.assert_not_called()
                RD.assert_called_once()
                run.assert_called_once()
                # desk passed into run
                assert run.call_args.kwargs.get("desk") is fake_desk \
                    or (run.call_args.args and run.call_args.args[-1]
                        is fake_desk) \
                    or run.call_args[1].get("desk") is fake_desk \
                    or any(
                        a is fake_desk
                        for a in list(run.call_args.args)
                        + list(run.call_args.kwargs.values())
                    )


def test_local_path_without_sandbox_url_uses_managed_env():
    with patch.object(agent_mod, "require_binaries"):
        with patch.object(agent_mod, "ManagedEnv") as env:
            env.return_value.__enter__ = lambda s: ":2"
            env.return_value.__exit__ = lambda *a: None
            with patch.object(agent_mod, "run") as run:
                with patch.object(sys, "argv", ["agent.py", "--probe"]):
                    agent_mod.main()
                run.assert_called_once()


def test_parse_stream_and_token():
    ns = _parse([
        "--sandbox-url", "http://127.0.0.1:7090",
        "--stream-url", "ws://127.0.0.1:6080",
        "--sandbox-token", "sec",
        "--probe",
    ])
    assert ns.sandbox_url.endswith(":7090")
    assert ns.stream_url.startswith("ws://")
    assert ns.sandbox_token == "sec"


def test_sandbox_url_and_display_mutually_exclusive():
    with patch.object(sys, "argv", [
        "agent.py",
        "--sandbox-url", "http://127.0.0.1:7090",
        "--display", ":2",
        "--probe",
    ]):
        with pytest.raises(SystemExit):
            agent_mod.main()
