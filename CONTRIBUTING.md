# Contributing to desktop-use-hosted

Thanks for considering a contribution. This project optimizes for one thing above all: **the whole loop stays readable in one sitting**. Small plain-Python modules, no framework, no frontend build step.

`desktop-use-hosted` is the **control plane**. The nested desktop **data plane** lives in a separate repository:

- **[desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox)** for Docker desktop, `/health` / `/screenshot` / `/action`, and noVNC

Open sandbox API or image changes there. Keep this repo focused on the agent loop, dual model backends, operator console, and the HTTP client that talks to the sandbox.

## Development setup

```bash
git clone https://github.com/arthurkatcher/desktop-use-hosted.git
cd desktop-use-hosted

# uv resolves script dependencies (httpx, python-xlib) from file headers
uv run python -m desktop_use.agent --probe          # local capture + input (needs Xephyr stack)
# or, with a running desktop-sandbox:
# uv run python -m desktop_use.agent --sandbox-url http://127.0.0.1:7090 --probe
```

**Local Xephyr path** needs the system packages listed in the README (Xephyr, openbox, scrot, xterm; plus x11vnc, novnc, websockify for local console live view).

**Remote path** needs a running [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) and no local VNC stack on the control plane.

For live model runs, point at any OpenAI-compatible vision endpoint (or Holo Models API). Prefer env vars for keys:

```bash
export OPENAI_API_KEY="…"
export OPENAI_BASE_URL="…"
# Holo also accepts HAI_API_KEY when OPENAI_API_KEY is unset
```

## Tests

### Unit tests (mandatory for PRs that touch logic)

```bash
uv run --with pytest python -m pytest tests/ -v
```

These cover dual backends (detect, scale, tool map, request bodies, normalize), remote client behavior (mocked httpx), stream URL inject, prefill/spawn/display refuse, and CLI flag routing. **PRs that change `model_backends.py`, `remote.py`, `agent.py`, or `ui.py` must keep the suite green.**

### Live E2E (optional; needs keys and a sandbox)

Live UI grounding cannot be fully unit-tested. Optional checks:

```bash
SANDBOX_URL=http://127.0.0.1:7090 uv run evals/remote_smoke.py
# see EVALS.md for hosted_e2e and model-backed steps
```

If you run live E2E, say so in the PR (model id, backend `holo` or `generic`, task, pass/fail). Do not claim a model works without a real run. Not every vision model grounds UI well; regressions are often model-side.

### Dual-backend expectations

| Change area | Expectation |
|---|---|
| Generic path (prefill, pixels, OpenRouter reasoning) | Unit tests still pass; do not break non-Holo defaults |
| Holo path (structured outputs, `[0,1000]` scale, tool map) | Unit tests for scale/map/normalize/body builders stay green |
| Shared loop (interrupt, events, execute allowlist) | Both backends must still return `{reasoning, action}` with **pixel** coords from `ask_model` |
| Sandbox client only | Mocked remote tests; optional live smoke |

Never inject Holo-only request fields onto the generic path. Never nest Holo extras under `"extra_body"`.

## Ground rules

- **Keep the step-boundary interrupt contract.** Stop, take-control, and mid-flight messages discard an in-flight decision and mark it not executed / `skipped`, never half-applied.
- **Events are append-only and replayable.** Console state must rebuild from `events.jsonl` alone. New event types need replay and SSE reconnect dedupe handling.
- **Do not weaken isolation rails.** Refuse driving `:0`/`:1` on the local path; scrub `WAYLAND_DISPLAY` for local sessions; remote mode must not spawn Xephyr on the control plane.
- **Dependency budget.** Runtime Python deps are `httpx` and `python-xlib`. Frontend stays dependency-free vanilla JS. New dependencies need a strong written case.
- **Model-agnostic OpenAI chat shape.** Provider-specific behavior stays in existing branches (Holo profile, OpenRouter reasoning-effort).
- **Match style.** Small functions, classes only where state demands it, comments only for what the code cannot say, ~79 columns. Prose: no em dashes. Commits: `feat:` / `fix:` / `docs:` / `chore:`.

## Pull requests

- Open an issue first for anything non-trivial so the approach can be discussed early.
- Keep PRs focused: one behavior change per PR when practical.
- Use conventional-style titles: `feat:`, `fix:`, `docs:`, `chore:`.
- Include what you tested (unit suite, and live model/backend/task if any).
- Push review fixes as new commits; do not force-push a branch under review.
- Update README.md and CHANGELOG.md when behavior or operator-facing flags change.
- Do not commit `sessions/`, `.env`, keys, or screen recordings.

## Docs to keep in sync

| If you change… | Also update… |
|---|---|
| Flags, env vars, backends | README configuration + dual-backend sections |
| Security posture or binds | SECURITY.md |
| Event types or interrupt behavior | README, AGENTS.md lore, replay paths |
| Sandbox HTTP contract | Prefer desktop-sandbox docs; mirror only the client surface here |

## Reporting bugs

Include a session directory (`sessions/<id>/`) with screenshots redacted if needed. `events.jsonl` is often enough to diagnose loops or bad decisions. Never paste live API keys.

## Security issues

Do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md) and use GitHub Security Advisories.
