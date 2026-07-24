# GOAL (desktop-use-hosted)

Private **control-plane** fork of [desktop-use](https://github.com/arthurkatcher/desktop-use).
Paired data plane: **desktop-sandbox** (headless Xvfb desktop + Desktop API + noVNC).

Full two-repo design: see `desktop-sandbox/docs/GOAL.md`, `ARCHITECTURE.md`, and
`RESEARCH.md` in the sibling checkout (or the sandbox repo).

## Product goal

Accept a **screen link** so the operator console and agent loop can drive a
remote (or local-loopback) sandbox without spawning Xephyr:

```bash
uv run ui.py \
  --sandbox-url http://127.0.0.1:7090 \
  --stream-url  ws://127.0.0.1:6080 \
  --base-url "$OPENAI_BASE_URL" \
  --model "$MODEL" \
  --api-key "$OPENAI_API_KEY"
# console: http://127.0.0.1:7788
```

Headless probe (no model):

```bash
uv run agent.py --sandbox-url http://127.0.0.1:7090 --probe
```

## Ports (local E2E)

| Service | Port | Owner |
|---|---|---|
| Desktop API | 7090 | desktop-sandbox |
| noVNC / websockify | 6080 | desktop-sandbox |
| Operator console | 7788 | this repo |

## Desktop API contract (client view)

Base URL = `--sandbox-url`. Optional token via `--sandbox-token` /
`SANDBOX_TOKEN` (`Authorization: Bearer` or `X-Sandbox-Token`).

| Method | Path | Use |
|---|---|---|
| `GET` | `/health` | size, display, `stream_ws`, browser |
| `GET` | `/screenshot` | PNG for VLM + session store |
| `POST` | `/action` | full action dict (same schema as model, no `done`) |

Prefer one POST per model action (including `click_type`).

## Changes vs upstream desktop-use

| Area | Change |
|---|---|
| `remote.py` (or in `agent.py`) | `RemoteDesktop` HTTP client |
| `agent.py` CLI | `--sandbox-url`, `--sandbox-token`; remote `--probe` |
| `ui.py` | `--sandbox-url`, `--stream-url`, `--sandbox-token`; skip ManagedEnv + local VNC when remote |
| `ui.html` | `__STREAM_URL__` for noVNC; fallback to local `__WS_PORT__` |
| SSE `hello` | include `stream_url`, `mode: "remote"\|"local"` |
| Local path | unchanged when sandbox-url unset (Xephyr + local x11vnc) |

## Invariants (do not break)

From upstream `AGENTS.md`:

1. Step-boundary interrupt: stop / take-control / message → `skipped`, never
   half-apply. If interrupt after decision and before `POST /action`, do not
   call the sandbox.
2. Events append-only and replayable (`events.jsonl` + `seq`).
3. Local path still refuses `:0`/`:1` and scrubs Wayland.
4. Dependency budget: `httpx` for remote; `python-xlib` only for local Desktop.
5. Model-agnostic OpenAI chat completions shape.

## E2E success (with sandbox running)

1. `agent.py --sandbox-url … --probe` passes.
2. Console at `:7788` shows live RFB from `:6080`.
3. Session task opens terminal and runs `date` (screenshot confirms).
4. Session task opens browser to `example.com` (screenshot confirms).
5. Without `--sandbox-url`, nested Xephyr path still works.

## Non-goals (this repo)

- Owning Xvfb/browser install matrix (sandbox repo)
- Multi-sandbox orchestration
- Public exposure / auth product (loopback + tunnel only for MVP)
