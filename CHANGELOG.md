# Changelog

## 0.0.2 (2026-07-25)

### Session idle, End, and status UX

- Agent `done` (and max-steps budget) parks the session as **idle** instead of
  ending it. Message bar stays open; a user message emits `resumed` and
  continues the loop with the new text as the current objective.
- Session ends on **End** (`status=complete`), **Stop** (`status=stopped`),
  hard **error**, or **idle timeout** (`--idle-timeout` / `IDLE_TIMEOUT`,
  default 60s since last desktop action or user message).
- Console **END** button (next to STOP) + `POST /end`: same step-boundary
  interrupt as Stop, but finishes as successful **complete** (not stopped).
- New persisted events: `idle`, `resumed`, `end_requested`.
- Idle lamp amber; idle-timeout card **SESSION ENDED** (not red error);
  take-control locked after session ends.
- Home badges: `idle`, `complete`, `ended`, `stopped`.

### Agent desktop guidance

- Prefer keyboard shortcuts `ctrl+alt+t` / `ctrl+alt+b` and desktop
  Browser / Terminal icons (double-click); do not use right-click menus
  to launch apps under pcmanfm.

## 0.0.1 (2026-07-25)

First public MVP of the **desktop-use-hosted** control plane.

### Dual model backends

- `model_backends.py`: detect `generic` vs `holo` (`--model-backend auto|generic|holo`,
  env `MODEL_BACKEND` / `DESKTOP_USE_MODEL_BACKEND`).
- Holo: top-level `structured_outputs` + thinking fields, no prefill, temp 0.8,
  parse `{note, thought, tool_call}`, scale `[0,1000]`→pixels before execute.
- Generic: absolute pixels, optional JSON prefill (Claude 5 skip policy),
  OpenRouter low reasoning effort when the base URL contains `openrouter`.
- CLI/UI accept `HAI_API_KEY` when `OPENAI_API_KEY` is unset.
- Unit tests in `tests/test_model_backends.py`.

### Remote sandbox control plane

- `--sandbox-url`, `--stream-url`, `--sandbox-token` on `agent.py` and `ui.py`
  (env aliases `SANDBOX_URL`, `STREAM_URL`, `SANDBOX_TOKEN` and `DESKTOP_*`).
- `remote.py` / `RemoteDesktop`: health, screenshot, action over HTTP.
- Console injects remote noVNC URL only in sandbox mode; local Xephyr path unchanged.
- Multi-instance: one agent per host-mapped sandbox/stream ports.

### Safety and robustness

- Refuse real displays including `:0.0` / `:1.1` (not only exact `:0`/`:1`).
- Reject model `spawn` and other non-agent action types before sandbox POST.
- Session shot/meta and noVNC static paths: reject `..`, absolute paths, and
  escape outside their roots.
- Stream inject uses a single `__STREAM_URL__` value token and a
  `startsWith('__')` guard so injection cannot make the placeholder check
  self-defeating.
- Non-JSON model API error bodies surface as `ValueError` for corrective retries.
- Preflight check for required system binaries with an install hint.

### Console and agent loop (inherited / carried forward)

- Agent loop: screenshot to action via OpenAI-compatible vision endpoints;
  before/after screenshots, corrective retries, `click_type`, fuzzy repeat
  guard, managed Xephyr environment with automatic teardown.
- Console: session store (meta, seq-numbered `events.jsonl`, per-step PNGs),
  sessions home, live noVNC, snapshot timeline, SSE transcript with reconnect
  dedupe, step-boundary interrupt contract (stop, take-control, mid-flight
  messages), light/dark theming.

### Tests and evals

- `tests/` unit suite (remote client, stream inject, prefill/spawn/display,
  dual backends, CLI flags).
- Optional live smoke: `evals/remote_smoke.py`, `evals/hosted_e2e.py`.
