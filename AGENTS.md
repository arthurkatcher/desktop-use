# AGENTS.md

Guidance for coding agents (and humans in a hurry) working on this repository.

## What this project is

`desktop-use-hosted` is the **control plane** for a computer-use stack: a
vision-language model drives a desktop through screenshots and synthetic
input, wrapped in a web operator console with live VNC view, streaming
transcript, snapshot replay and mid-flight human intervention.

Two desktop backends:

1. **Local** (default): nested Xephyr + openbox + scrot + XTest (same shape as
   upstream [desktop-use](https://github.com/arthurkatcher/desktop-use)).
2. **Remote sandbox** (`--sandbox-url`): HTTP Desktop API + optional
   `--stream-url` for noVNC. No Xephyr spawn on the control plane. Pairs with
   [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox).

The prime directive still holds: **the whole loop stays readable in one
sitting**. No framework, no build step. Resist any change that breaks that.

## File map

Package `desktop_use/` (run via `python -m`, deps from `pyproject.toml`):

| File | Role |
|---|---|
| `desktop_use/agent.py` | The agent brain and CLI. `Desktop` (scrot + XTest), `ManagedEnv` (Xephyr lifecycle), `ask_model`, `execute` (local methods or `RemoteDesktop.execute`), `run`. |
| `desktop_use/model_backends.py` | Dual profile helpers: resolve generic vs Holo, scale `[0,1000]`→pixels, tool map, request bodies, normalize to `{reasoning, action}`. |
| `desktop_use/remote.py` | `RemoteDesktop`: health / screenshot / action over httpx against a sandbox API; `probe_health` (soft, never raises) for the screen registry. |
| `desktop_use/screen_store.py` | Screen registry: connection, health, soft power, exclusive lease, control FSM (`none`/`ai`/`human` + TTL). |
| `desktop_use/settings_store.py` | `settings.json` store: defaults + presets, validation, atomic writes, `public_settings` redaction. |
| `desktop_use/ui.py` | Console server. Routes `/` (home), `/s/<id>` (session), `/screen/<id>` (screen live view + control), `/api/*`, `/static/*`, `/novnc/*`. Local mode spawns Xephyr + x11vnc + websockify. |
| `desktop_use/static/` | Frontend, no build step. `home.html` (sessions / screens / settings panels), `session.html` (per-session console), `screen.html` (per-screen live view + take control). `css/console.css` owns all theme tokens + shared primitives; per-page css/js beside it. |
| `tests/` | Unit tests (mock httpx for `RemoteDesktop`; dual-backend coverage). |
| `evals/remote_smoke.py` | Live smoke against `SANDBOX_URL` if set. |
| `sessions/`, `screens/`, `settings.json` | Runtime data, gitignored. |

Stream injection: pages carry one `window.DU = { streamUrl: '__STREAM_URL__',
wsPort: '__WS_PORT__' }` bridge inline; the module js (`static/js/*.js`) ships
token-free and reads the bridge. Session page falls back to the local ws port
when the placeholder survives; screen page never falls back (only the screen's
own stream).

## Commands

```bash
uv run python -m desktop_use.agent --probe        # local pipeline check
uv run python -m desktop_use.agent "task..."      # headless CLI (private Xephyr)
uv run python -m desktop_use.ui --base-url ... --model ...   # console :7788

# remote sandbox (desktop-sandbox data plane on :7090 / :6080)
uv run python -m desktop_use.agent --sandbox-url http://127.0.0.1:7090 --probe
uv run python -m desktop_use.ui --sandbox-url http://127.0.0.1:7090 \
  --stream-url ws://127.0.0.1:6080 --base-url ... --model ...

uv run --with pytest python -m pytest tests/ -v
SANDBOX_URL=http://127.0.0.1:7090 uv run evals/remote_smoke.py
```

Verification: unit tests for backends and remote client; live `--probe` (local
or remote); at least one real console session before claiming a change works.

## Invariants you must not break

1. **The step-boundary interrupt contract.** Stop, take-control and mid-flight
   messages all interrupt the same way: an in-flight model decision is
   discarded and emitted as a `skipped` event, never half-applied. Any new
   interruption mechanism follows the same contract.
2. **Session ends only on Stop, error, or idle timeout.** Agent `done` and
   max-steps park the session as `idle` (`idle` / `resumed` events); the
   message bar stays open and a user message continues the loop. Do not
   emit `run_end` or release the desktop lock on task completion alone.
3. **Events are append-only and replayable.** Anything the console displays
   must be reconstructable from `events.jsonl` alone. Persisted events carry
   monotonic `seq` numbers; the client dedupes on them across SSE reconnects.
   Transient bus-only events (no `seq`) are allowed only for cosmetic
   immediate feedback and must have a persisted counterpart if they carry
   state (see `message_sent` vs `user_message`).
3. **The desktop is isolated.** Never weaken the refusal to drive `:0`/`:1`
   on the local path, and keep `WAYLAND_DISPLAY` scrubbed from everything
   spawned inside a local session. Remote mode must not spawn Xephyr or
   touch the control-plane display.
4. **Dependency budget.** Runtime Python deps are `httpx` and `python-xlib`
   (xlib only needed for local Desktop). Frontend stays dependency-free
   vanilla JS. New dependencies need a strong written case.
5. **Model-agnosticism / dual backend.** Everything model-facing goes through
   the OpenAI-compatible chat completions shape. **Generic** (default): absolute
   pixels, optional prefill, OpenRouter `reasoning` only if `openrouter` is in
   the base URL. **Holo**: no prefill, top-level `structured_outputs` + thinking
   fields, parse `{note,thought,tool_call}`, scale coords on the control plane
   before execute. Do not inject Holo fields on the generic path, and never
   send nested `"extra_body"` for Holo extras.

## Agent-loop lore (learned from real failures, do not regress)

- The model gets before + current screenshots and a textual action history.
  Removing either reintroduces looping.
- Retries after a bad reply must be corrective (feed the error back). At
  temperature 0 a blind retry reproduces the identical bad output.
- Validate reply shape (`action.type` present), not just JSON syntax.
- The repeat guard uses fuzzy coordinate matching (a few px), because models
  jitter coordinates between identical attempts.
- Text fields look identical focused and unfocused in screenshots; that is why
  `click_type` exists and why the prompt forbids double-clicking inputs.
- The JSON prefill (`{"role": "assistant", "content": "{"}`) is what stops
  tool-use-trained models from emitting tool-call syntax on turn 1 (generic
  path only; Holo disables prefill).

## Style

- Python: small functions, classes only where state demands it, comments only
  for what the code cannot say. Match the existing formatting (79-ish cols).
- Frontend: CSS custom properties for every color (both themes must stay in
  sync), flat line-based UI, no decorative shadows, IBM Plex.
- Prose (docs, commit messages, PR text): no em dashes.
- Commits: `feat:` / `fix:` / `docs:` / `chore:` prefixes.

## Docs to keep in sync

If you change behavior, update README.md (features, API table, troubleshooting)
and CHANGELOG.md. If you add an event type, document it where the interrupt
contract is described and handle it in replay.
