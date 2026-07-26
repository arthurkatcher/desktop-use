# desktop-use-hosted

**Control plane for computer-use: a readable agent loop, an operator console, and optional remote Desktop API sandboxes.**

[![License: MIT](https://img.shields.io/badge/License-MIT-1b232d.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-1b232d.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-MVP-ffb454.svg)](#status)
[![Version](https://img.shields.io/badge/version-0.0.1-63b981.svg)](CHANGELOG.md)

`desktop-use-hosted` is the **control plane** of a split computer-use stack. A vision-language model decides actions; this repo runs the model loop, the web operator console, and either a local nested desktop or a remote [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) data plane.

It is the hosted / screen-link evolution of the local-only [desktop-use](https://github.com/arthurkatcher/desktop-use) reference: same interrupt contract, same append-only sessions, dual model backends (Holo structured harness and generic OpenAI-compatible VLMs).

```text
  Operator browser (127.0.0.1:7788)
        │ REST + SSE transcript          │ noVNC (local ws or remote stream)
        ▼                                ▼
  ┌─────────────────────────────────────────────────────────┐
  │  desktop-use-hosted (this repo)                         │
  │  ui.py console · agent.py loop · model_backends.py      │
  │  sessions/ (events.jsonl + step PNGs)                   │
  └───────────────┬───────────────────────────┬─────────────┘
                  │                           │
     local path   │                           │  remote path
     scrot+XTest  │                           │  remote.py HTTP
                  ▼                           ▼
         Xephyr + openbox              desktop-sandbox
         x11vnc + websockify           GET /health /screenshot
                                       POST /action · stream_ws
                  │                           │
                  └─────────────┬─────────────┘
                                │ chat/completions (+ images)
                    ┌───────────┴────────────┐
                    │  Model backends        │
                    │  holo  ·  generic      │
                    └────────────────────────┘
```

## Table of contents

- [Why](#why)
- [Features](#features)
- [Architecture](#architecture)
- [Related projects](#related-projects)
- [Dual model backends](#dual-model-backends)
- [Quick start](#quick-start)
  - [A. Docker sandbox (recommended)](#a-docker-sandbox-recommended)
  - [B. Hosted CLI and console against a sandbox URL](#b-hosted-cli-and-console-against-a-sandbox-url)
  - [C. Local Xephyr only (no sandbox)](#c-local-xephyr-only-no-sandbox)
- [Operator console](#operator-console)
- [Configuration](#configuration)
- [What works (tested models)](#what-works-tested-models)
- [Security](#security)
- [Limitations and roadmap](#limitations-and-roadmap)
- [Development and tests](#development-and-tests)
- [HTTP API](#http-api)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Status](#status)

## Why

Cloud computer-use products hide the loop: closed runtimes, opaque decisions, screenshots you never see again. This stack keeps the opposite posture.

- The agent brain stays small and readable (`agent.py`, `model_backends.py`, `remote.py`, `ui.py`).
- Every step is append-only on disk under `sessions/` (meta, `events.jsonl`, PNGs).
- The data plane ([desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox)) can run in Docker while the control plane and API keys stay on your machine.
- Model choice is yours: H Company Holo Models API or any OpenAI-compatible vision endpoint.

## Features

### Agent loop (`agent.py` + `model_backends.py`)

- **Screenshot to action**: capture a desktop frame, send PNG plus task and history to the model, parse one action, apply it (local XTest or remote HTTP).
- **Action space**: `click`, `double_click`, `right_click`, `move`, `type`, `key` (combos like `ctrl+l`), `click_type`, `scroll`, `wait`, `done`. Sandbox-only types such as `spawn` are never accepted on the agent path.
- **Before/after vision**: each turn includes the previous screenshot and the current one so the model can see what its last action did.
- **Dual backends**: **holo** (structured outputs, coordinates in `[0,1000]` scaled to pixels on the control plane) and **generic** (OpenAI-compat free-form JSON, absolute pixels, optional assistant prefill).
- **Corrective retries**: malformed or wrong-shape replies are fed back with the parse error (blind retries at temperature 0 reproduce the same mistake).
- **Anti-loop guards**: fuzzy coordinate repeat detection; screen-changed feedback after each action.
- **Interrupt contract**: stop, take-control, and mid-flight messages all discard an in-flight model decision at the step boundary (`skipped` / not executed), never half-applied.
- **Idle after done**: when the agent emits `done`, the session parks as `idle` instead of tearing down. A message resumes the loop; the session ends on **End** (status `complete`), **Stop** (status `stopped`), hard **error**, or **idle timeout** (default 60s / 1 min since last action or user message). Hitting `--max-steps` also parks idle (send a message for another burst).

### Operator console (`desktop_use/ui.py` + `desktop_use/static/`)

- Console home with three panels: **Sessions** (launcher, paginated list with status badges, thumbnails, model, step count, duration), **Screens** (fleet cards), **Settings** (presets, backend-aware model picker, loop and control defaults).
- Per-session console at `/s/<id>`: live noVNC of the agent desktop (local stack or remote stream), snapshot timeline with scrubber and keyboard navigation, streaming transcript over SSE with reconnect dedupe on monotonic `seq`, stop / take control / release, mid-flight and post-done messages; light and dark themes.
- Per-screen console at `/screen/<id>`: full-viewport live stream, take control / release with TTL countdown, soft power on/off, health retry, lease banner deep-linking the holding session.
- Screen registry (`desktop_use/screen_store.py`): external sandboxes with health-gated create, soft power, exclusive lease per session, and a control FSM (`none` / `ai` / `human` + TTL) bridged into the session interrupt contract.
- Settings (`desktop_use/settings_store.py`): `settings.json` defaults for new sessions (CLI > env > settings > hardcoded), presets (Holo3 35B / Holo3 122B / Claude Sonnet 5 / Claude Opus 5), secret redaction on API responses.

### Remote sandbox (`remote.py`)

- `--sandbox-url` drives a Desktop API: `GET /health`, `GET /screenshot`, `POST /action`.
- Optional `--sandbox-token` (`Authorization: Bearer` and `X-Sandbox-Token`).
- Optional `--stream-url` for noVNC; when omitted, health `stream_ws` is used if present.
- Control plane does **not** spawn Xephyr, x11vnc, or websockify in remote mode.
- Multi-instance parallel isolation: one agent process per sandbox URL/ports.

### Persistence

```text
sessions/<id>/
├── meta.json      # id, task, model, status, started, ended, steps
├── events.jsonl   # append-only, seq-numbered (source of truth)
├── 1.png … N.png  # one screenshot per step
└── final.png      # screen at completion
```

No database. Replays rebuild from these files alone.

## Architecture

| Layer | Responsibility | Repo |
|---|---|---|
| Control plane | Model loop, operator console, session store, coord scale | **this repo** |
| Data plane | Isolated desktop, screenshot/action API, optional noVNC | [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) |
| Model host | Vision LLM inference | Your endpoint (Holo API, OpenRouter, Ollama, vLLM, …) |

Local mode keeps Xephyr on the same host as the console (same shape as upstream desktop-use). Remote mode is the production-shaped path: sandbox in Docker, control plane on the operator machine.

## Related projects

| Project | Role |
|---|---|
| **[desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox)** | Data plane companion. Docker-first nested desktop with HTTP Desktop API and noVNC. **Pair this control plane with that repo.** |
| [desktop-use](https://github.com/arthurkatcher/desktop-use) | Original local-only reference (Xephyr on the operator machine, no remote API). |

## Dual model backends

Everything model-facing uses the OpenAI-compatible `POST /v1/chat/completions` shape. Two harness profiles live in `model_backends.py`:

| | **holo** | **generic** |
|---|---|---|
| **When** | H Company Models API (`api.hcompany.ai`) or known Holo3 model ids | OpenRouter, Claude, Ollama, vLLM, most VLMs (default) |
| **Selection** | `--model-backend auto` (default) or `holo` | `auto` or `generic` |
| **Auto heuristics** | Base URL contains `hcompany.ai` / `api.hcompany`, or model id starts with `holo3` / `hcompany/holo` | Everything else |
| **Coordinates** | Model uses `[0,1000]`; control plane scales to pixels before execute | Absolute pixels |
| **Output shape** | `{note, thought, tool_call}` via top-level `structured_outputs` | Free-form `{reasoning, action}` |
| **Prefill** | Off | On (with Claude 5 skip policy) |
| **Temperature** | 0.8 | 0 |
| **Thinking** | `chat_template_kwargs` + `reasoning_effort` | OpenRouter low reasoning only when URL contains `openrouter` |

Override with `--model-backend auto|holo|generic` or env `MODEL_BACKEND` / `DESKTOP_USE_MODEL_BACKEND`. Self-hosted Holo1.x weights on vLLM stay **generic** unless you force `--model-backend holo`.

Holo extras (`structured_outputs`, thinking fields) are sent as **top-level** request body keys. Do not nest them under `"extra_body"`; many gateways ignore that nesting.

### Holo Models API notes

```bash
export HAI_API_KEY="…"                 # portal.hcompany.ai Models API key
export OPENAI_API_KEY="$HAI_API_KEY"   # optional; CLI falls back to HAI_API_KEY
export OPENAI_BASE_URL="https://api.hcompany.ai/v1"
```

| Item | Value |
|---|---|
| Models | `holo3-1-35b-a3b` (fast), `holo3-122b-a10b` (larger) |
| Auth | Bearer (`HAI_API_KEY` or `OPENAI_API_KEY`) |
| Coords | scaled with `int(x/1000*W)` and clamped on the control plane |
| Sandbox | always receives **pixel** actions only |

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/). Prefer **path A** for real work.

### A. Docker sandbox (recommended)

1. Run a [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) instance (see that repo’s README; default API `http://127.0.0.1:7090`, stream often `ws://127.0.0.1:6080`).
2. Clone and point this control plane at it.

```bash
git clone https://github.com/arthurkatcher/desktop-use-hosted.git
cd desktop-use-hosted

export SANDBOX_URL=http://127.0.0.1:7090
export STREAM_URL=ws://127.0.0.1:6080/websockify   # required if host maps a non-default stream port
# export SANDBOX_TOKEN=…                           # if the sandbox requires auth

# Probe capture + input against the sandbox (no model)
uv run python -m desktop_use.agent --sandbox-url "$SANDBOX_URL" --probe
```

### B. Hosted CLI and console against a sandbox URL

Use env vars for secrets. Prefer not to put API keys on the command line.

**Generic (OpenRouter example):**

```bash
export OPENAI_API_KEY="…"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export SANDBOX_URL=http://127.0.0.1:7090
export STREAM_URL=ws://127.0.0.1:6080/websockify

uv run python -m desktop_use.agent --sandbox-url "$SANDBOX_URL" \
  --base-url "$OPENAI_BASE_URL" \
  --model anthropic/claude-sonnet-4.5 \
  --model-backend generic \
  --max-steps 25 \
  "Open a terminal and run uname -a"

uv run python -m desktop_use.ui \
  --sandbox-url "$SANDBOX_URL" \
  --stream-url "$STREAM_URL" \
  --base-url "$OPENAI_BASE_URL" \
  --model anthropic/claude-sonnet-4.5 \
  --model-backend generic \
  --max-steps 25
# open http://127.0.0.1:7788
```

**Holo:**

```bash
export HAI_API_KEY="…"
export OPENAI_API_KEY="$HAI_API_KEY"
export OPENAI_BASE_URL="https://api.hcompany.ai/v1"
export SANDBOX_URL=http://127.0.0.1:7090
export STREAM_URL=ws://127.0.0.1:6080/websockify

uv run python -m desktop_use.agent --sandbox-url "$SANDBOX_URL" \
  --base-url "$OPENAI_BASE_URL" \
  --model holo3-1-35b-a3b \
  --model-backend auto \
  "Open a terminal and run echo hi"

uv run python -m desktop_use.ui \
  --sandbox-url "$SANDBOX_URL" \
  --stream-url "$STREAM_URL" \
  --base-url "$OPENAI_BASE_URL" \
  --model holo3-1-35b-a3b \
  --model-backend auto \
  --max-steps 25 --port 7788
```

**Multi-sandbox (parallel isolation):** one agent per instance. Do not point two agents at the same `SANDBOX_URL`.

| Instance | `SANDBOX_URL` | `STREAM_URL` | Console `--port` |
|---|---|---|---|
| 0 | `http://127.0.0.1:7090` | `ws://127.0.0.1:6080` | `7788` |
| 1 | `http://127.0.0.1:7091` | `ws://127.0.0.1:6081` | `7789` |

Health JSON may still advertise container-internal ports. When the host maps a different stream port, set `STREAM_URL` / `--stream-url` to the host-mapped URL.

### C. Local Xephyr only (no sandbox)

Same path as upstream desktop-use. System packages on Debian/Ubuntu:

```bash
sudo apt install xserver-xephyr openbox scrot xterm x11vnc novnc websockify
```

```bash
uv run python -m desktop_use.agent --probe
uv run python -m desktop_use.agent "open a terminal and run ls"

export OPENAI_API_KEY="…"
uv run python -m desktop_use.ui \
  --base-url https://openrouter.ai/api/v1 \
  --model anthropic/claude-haiku-4.5 \
  --max-steps 25
# open http://127.0.0.1:7788
```

The headless CLI needs Xephyr, openbox, scrot, xterm. The VNC trio is only for local console live view.

## Operator console

Binds **127.0.0.1** only. There is **no console login**. This is a single-operator MVP, not multi-tenant SaaS.

| Control | Where | What it does |
|---|---|---|
| LAUNCH SESSION | home | starts a run and opens its session page |
| STOP | session header | ends the session at the next step/idle boundary; pending action never runs |
| LIVE / SNAPSHOTS | control deck | real-time canvas vs step replay |
| ‹ › and arrow keys | control deck | step through snapshots |
| TAKE CONTROL | control deck | pause the agent; drive the desktop yourself |
| RELEASE CONTROL | control deck | continue the task, or stop the session |
| message bar | under transcript | mid-flight instruction, or new objective while idle |
| ◐ | header | light/dark theme |

One session runs at a time on a given console process. Non-active sessions open in snapshot replay with LIVE and TAKE CONTROL disabled.

## Configuration

| Flag | Environment | Purpose |
|---|---|---|
| `--base-url` | `OPENAI_BASE_URL` | Chat completions base (default `http://localhost:11434/v1`) |
| `--model` | `LOCAL_LOOP_MODEL` | Model id |
| `--api-key` | `OPENAI_API_KEY` or `HAI_API_KEY` | Bearer token for the model host (prefer env) |
| `--model-backend` | `MODEL_BACKEND` / `DESKTOP_USE_MODEL_BACKEND` | `auto` \| `generic` \| `holo` |
| `--max-steps` | | Cap agent steps per active burst (default 15); then idle, not end |
| `--idle-timeout` | `IDLE_TIMEOUT` | Seconds since last action/message before ending an idle session (default `60` = 1 min) |
| `--sandbox-url` | `SANDBOX_URL` / `DESKTOP_SANDBOX_URL` | Desktop API base |
| `--stream-url` | `STREAM_URL` / `DESKTOP_STREAM_URL` | Full websocket URL for noVNC |
| `--sandbox-token` | `SANDBOX_TOKEN` / `DESKTOP_SANDBOX_TOKEN` | Sandbox auth headers |
| `--port` | | Console HTTP port (default 7788, ui.py only) |
| `--vnc-port` / `--ws-port` | | Local VNC stack ports (local mode only) |
| `--display` | | Attach local path to existing X display (agent.py) |
| `--allow-real-display` | | Permit local drive of `:0`/`:1` (unsafe; default refuse) |
| `--probe` | | Screenshot + input check without a model |

Runtime Python deps (from script headers): `httpx`, `python-xlib` (xlib needed for local Desktop only).

## What works (tested models)

Live end-to-end runs against a Docker desktop-sandbox (honest summary):

| Model | Backend | Result |
|---|---|---|
| `holo3-1-35b-a3b` (Holo Models API) | holo | **PASS** (terminal + browser grounding tasks) |
| `holo3-122b-a10b` (Holo Models API) | holo | **PASS** path proven (sensitive to dirty desktop residue between runs) |
| `anthropic/claude-sonnet-5` via OpenRouter | generic | **PASS** |
| Multi-instance Holo (two sandboxes in parallel) | holo | **Isolation holds** (separate ports, no cross-desktop input) |
| Mistral medium-class via generic OpenAI-compat | generic | **FAIL on UI grounding** in our runs (menu loops / missed targets). Do not assume every vision model works. |

Other notes from generic-path experience:

| Model | Notes |
|---|---|
| `anthropic/claude-haiku-4.5` (OpenRouter) | Strong cheap default for many UI tasks |
| Gemini Flash tiers | Poor pixel grounding in earlier testing; hidden reasoning can starve JSON budget |
| `qwen2.5vl` (Ollama) | Workable fully local option |
| Self-hosted Holo1.5 weights | Use **generic** profile unless the host implements Holo structured extras |

Unit tests cover backend detection, coord scale, tool map, request bodies, remote client, stream inject, and CLI routing. They do not replace live UI grounding checks.

## Security

MVP threat model: **single operator on localhost**. Highlights:

- Console binds `127.0.0.1`; **no authentication**.
- API keys via environment variables preferred over argv.
- Sandbox token when the data plane requires it; noVNC stream exposure is a data-plane concern (see sandbox SECURITY).
- Local path refuses real displays `:0`/`:1` unless overridden; remote path never spawns Xephyr on the control plane.
- `sessions/` holds screen recordings. Treat as sensitive; it is gitignored.

Full policy: [SECURITY.md](SECURITY.md).

## Limitations and roadmap

**Current (v0.0.1 MVP)**

- Not multi-tenant SaaS: one operator, localhost console, no login.
- One active session per console process.
- Holo multi-turn fidelity is Phase 1 (structured tool path + scale); richer observation history and caching are later work.
- Model quality varies widely; grounding failures are model/site issues, not always control-plane bugs.

**Phase 2 / 3 (brief)**

- Multi-turn Holo fidelity (observation history, image budget discipline)
- Prompt / image caching where providers support it
- Multi-tenant and authenticated console (out of scope for MVP)
- Parallel local sessions; model picker in the UI header; cost telemetry

## Development and tests

```bash
# unit tests (no display, no API keys required)
uv run --with pytest python -m pytest tests/ -v

# optional live smoke against a running sandbox
SANDBOX_URL=http://127.0.0.1:7090 uv run evals/remote_smoke.py
```

See [EVALS.md](EVALS.md) for hosted e2e helpers. See [CONTRIBUTING.md](CONTRIBUTING.md) for PR norms and dual-backend test expectations.

Data-plane changes belong in [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox), not this repo.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | console home (sessions / screens / settings) |
| GET | `/s/<id>` | session console |
| GET | `/screen/<id>` | screen live view + operator control |
| GET | `/sessions`, `/api/sessions` | sessions JSON (paginated envelope on `/api/sessions`) |
| POST | `/run` `{"task": "...", "screen_id": null}` | launch a session (409 if one is running) |
| POST | `/stop` | abort at the next step boundary (`status=stopped`) |
| POST | `/end` | clean close at the next step boundary (`status=complete`) |
| POST | `/message` `{"text": "..."}` | queue a mid-flight instruction |
| POST | `/control/take` | pause the agent; user takes the desktop |
| POST | `/control/release` `{"continue": bool}` | hand back and resume, or stop |
| GET | `/events?sid=<id>` | SSE stream: full replay then live tail |
| GET | `/shot/<sid>/<n>.png` | any step screenshot |
| GET/PUT | `/api/settings` | read / update defaults (secrets redacted on read) |
| POST | `/api/settings/preset` `{"id": "..."}` | apply a model preset |
| GET/POST | `/api/screens` | list (filters + pagination) / create (health-gated) |
| GET/PATCH/DELETE | `/api/screens/<id>` | read / update / delete (delete blocked while leased) |
| POST | `/api/screens/<id>/on` · `/off` · `/health` | soft power + health probe |
| POST | `/api/screens/<id>/control/take` · `/release` | human control with TTL; take pauses a leasing session |
| GET | `/static/*` | console css/js assets |

## Troubleshooting

- **Remote stream blank**: set `--stream-url` to the **host-mapped** websocket URL when Docker publishes a non-default port. Health may still show the container port.
- **401 from sandbox**: set `SANDBOX_TOKEN` / `--sandbox-token` to match the data plane.
- **x11vnc exits on Wayland (local mode)**: `ui.py` scrubs `WAYLAND_DISPLAY`; if you run x11vnc by hand, unset it.
- **Apps open on the real desktop (local mode)**: something inherited Wayland env; launch via the session menu; give Chrome its own profile dir if needed.
- **Agent loops on the same click**: often an unfocused text field (focus is invisible in screenshots). Prefer `click_type`; the repeat guard exists for this.
- **Parse errors**: corrective retry usually fixes one-shot format slips. Confirm image input and backend profile (`holo` vs `generic`).
- **Orphan displays / VNC**: kill by exact pid. Avoid `pkill -f` patterns that match your own shell command line.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)

## Status

**v0.0.1** MVP. Shippable control plane for single-operator use with Docker sandbox and dual model backends. Expect breaking refinements before 0.1.0.
