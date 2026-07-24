# AGENTS.md

Guidance for coding agents (and humans in a hurry) working on this repository.

## What this project is

`desktop-use` is a self-hosted computer-use agent: a vision-language model
drives a nested Linux desktop (Xephyr) through screenshots and synthetic XTest
input, wrapped in a web operator console with live VNC view, streaming
transcript, snapshot replay and mid-flight human intervention.

The project's prime directive: **the whole loop stays readable in one
sitting**. Two Python files, two HTML files, no framework, no build step.
Resist any change that breaks that.

## File map

| File | Role |
|---|---|
| `agent.py` | The agent brain and CLI. `Desktop` (scrot capture + XTest input via python-xlib), `ManagedEnv` (Xephyr + openbox lifecycle with teardown), `ask_model` (prompt build, JSON prefill, parsing, shape validation), `execute` (action dispatch), `run` (the CLI ReAct loop). |
| `ui.py` | The console server. `SessionStore` (one directory per session: meta.json, seq-numbered events.jsonl, step PNGs), `Bus` (SSE fan-out), `Runner` (the session loop thread: interrupts, repeat guard, message drain), HTTP handler (REST + SSE + static). Spawns x11vnc and websockify. |
| `ui.html` | Per-session console page. Vanilla JS, no dependencies. Transcript rendering, noVNC canvas, snapshot timeline, stop/control/message UI, theming. |
| `home.html` | Sessions list + launcher page. |
| `sessions/` | Runtime data, gitignored. Never commit it: it contains screen recordings of the local machine. |

## Commands

```bash
uv run agent.py --probe          # capture + input pipeline check, no model needed
uv run agent.py "task..."        # headless CLI run (spawns and tears down its own display)
uv run ui.py --base-url ... --model ...   # full console at http://localhost:7788
```

There is no test suite yet. Verification is live: run `--probe`, then at least
one real session through the console and one through the CLI before claiming a
change works. The failure modes here (focus, timing, coordinate drift, model
formatting slips) do not show up in static reading of the code.

## Invariants you must not break

1. **The step-boundary interrupt contract.** Stop, take-control and mid-flight
   messages all interrupt the same way: an in-flight model decision is
   discarded and emitted as a `skipped` event, never half-applied. Any new
   interruption mechanism follows the same contract.
2. **Events are append-only and replayable.** Anything the console displays
   must be reconstructable from `events.jsonl` alone. Persisted events carry
   monotonic `seq` numbers; the client dedupes on them across SSE reconnects.
   Transient bus-only events (no `seq`) are allowed only for cosmetic
   immediate feedback and must have a persisted counterpart if they carry
   state (see `message_sent` vs `user_message`).
3. **The desktop is isolated.** Never weaken the refusal to drive `:0`/`:1`,
   and keep `WAYLAND_DISPLAY` scrubbed from everything spawned inside the
   session, or apps escape onto the user's real desktop.
4. **Dependency budget.** Runtime Python deps are `httpx` and `python-xlib`,
   declared in the script headers. The frontend is dependency-free vanilla JS
   (noVNC is served from the system package). New dependencies need a strong
   written case.
5. **Model-agnosticism.** Everything model-facing goes through the
   OpenAI-compatible chat completions shape. Parsing must tolerate backends
   that honor assistant prefill and backends that ignore it. Do not hardcode
   provider-specific behavior outside the existing OpenRouter reasoning-effort
   branch.

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
  tool-use-trained models from emitting tool-call syntax on turn 1.

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
