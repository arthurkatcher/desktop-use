# Handoff: Computer-use stack (sandbox + hosted + dual backends)

**Audience:** humans and coding agents continuing this work  
**Status:** public MVP **v0.0.1** of the control plane; pair with desktop-sandbox data plane.

## Repos

| Repo | Role |
|------|------|
| [desktop-use](https://github.com/arthurkatcher/desktop-use) | Local-only reference (Xephyr on the operator machine) |
| [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) | **Data plane**: headless desktop, capture, input, optional live stream |
| [desktop-use-hosted](https://github.com/arthurkatcher/desktop-use-hosted) | **Control plane** (this tree): model loop, sessions, console, dual backends |

Default localhost ports: sandbox API `7090`, noVNC `6080`, console `7788`.

## Mental model

```text
                    CONTROL PLANE (desktop-use-hosted)
                    keys, model, sessions, SSE UI, stop/control/message
                              |                    |
                     HTTP Desktop API         WebSocket (noVNC)
                     screenshot + action      live human view
                              |                    |
                    DATA PLANE (desktop-sandbox, often Docker)
                    Xvfb + openbox + xterm + Chromium
                    scrot + XTest, x11vnc (localhost) + websockify
```

**Desktop API contract (MVP):**

- `GET /health` → geometry, optional `stream_ws`
- `GET /screenshot` → PNG
- `POST /action` → pixel action types used by the agent (except `done`, control-plane only)
- Optional `SANDBOX_TOKEN` (`Bearer` / `X-Sandbox-Token`)
- Actions use **absolute pixel** coordinates

Control plane converts any model-native coord space (Holo `[0,1000]`) to pixels **before** calling the sandbox.

## Screen link flags

| Flag | Env | Meaning |
|------|-----|---------|
| `--sandbox-url` | `SANDBOX_URL` / `DESKTOP_SANDBOX_URL` | Remote data plane → `RemoteDesktop` |
| `--stream-url` | `STREAM_URL` / `DESKTOP_STREAM_URL` | noVNC WS; often filled from `/health` |
| `--sandbox-token` | `SANDBOX_TOKEN` / `DESKTOP_SANDBOX_TOKEN` | API auth |

When `--sandbox-url` is set: do **not** spawn local Xephyr / x11vnc / websockify. Interrupt contract unchanged: after a model decision, stop/pause/message → `skipped`, never half-apply (no POST to sandbox).

## Control plane modules

| File | Role |
|------|------|
| `agent.py` | prompts, `ask_model`, `execute`, CLI, local ManagedEnv, prefill policy |
| `model_backends.py` | dual **generic** / **holo** detect, scale, tool map, request bodies, normalize |
| `remote.py` | `RemoteDesktop` HTTP client |
| `ui.py` | SessionStore, Bus, Runner, SSE, remote vs local bootstrap, stream inject |
| `ui.html` / `home.html` | Console UI |

## Dual model backends (shipped in 0.0.1)

| Profile | Coords | Output | Notes |
|---------|--------|--------|-------|
| **generic** (default) | absolute pixels | `{reasoning, action}` | Prefill when allowed; OpenRouter reasoning only if URL matches |
| **holo** | model `[0,1000]` → pixels on control plane | `{note, thought, tool_call}` | Top-level `structured_outputs` + thinking; no prefill |

Select with `--model-backend auto|generic|holo` (auto uses URL / known Holo3 model ids). See README for tables and tested models.

Phase 1 is dual harness + scale + unit tests + live E2E on Holo and Claude. Later: richer multi-turn Holo observation history, caching, multi-tenant console.

## Lessons (do not regress)

- Claude Sonnet 5-class: skip assistant prefill (HTTP 400 if forced).
- Surface real API errors (`choices` missing) as `ValueError` for corrective retries.
- Do not send OpenRouter-only `reasoning` fields to every provider.
- Stream inject: single `__STREAM_URL__` value token; inject only in sandbox mode.
- Refuse real displays including `:0.0`; reject `spawn` / shell on the agent path.
- Host-mapped stream ports: set `STREAM_URL` when Docker maps a non-default port; health may still advertise the container port.
- Multi-sandbox: one agent process per `SANDBOX_URL`; parallel isolation works when ports differ.

## Operator UX

- Console: `http://127.0.0.1:7788/` (not raw noVNC alone).
- No console login; single-operator MVP.
- Openbox (sandbox): right-click empty desktop → Terminal / Web browser.

## Where to change what

| Change | Repo |
|--------|------|
| Model harness, console, sessions, RemoteDesktop client | **desktop-use-hosted** |
| Docker image, Xvfb desktop, Desktop API, noVNC bind | **desktop-sandbox** |
| Local-only Xephyr reference product | **desktop-use** |

Read [README.md](../README.md), [AGENTS.md](../AGENTS.md), [SECURITY.md](../SECURITY.md) next.
