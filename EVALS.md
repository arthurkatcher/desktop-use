# EVALS: desktop-use-hosted

How to run unit tests and live evals for the control plane against
`desktop-sandbox`.

## Prerequisites

| Need | Why |
|------|-----|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | script deps (`httpx`, `python-xlib`) |
| Running **desktop-sandbox** on loopback | remote path / e2e |
| Optional `OPENAI_API_KEY` | model-in-the-loop agent task |

Default sandbox URL: `http://127.0.0.1:7090`  
Override with `SANDBOX_URL` or `DESKTOP_SANDBOX_URL`.

## Unit tests (no display, no sandbox)

```bash
cd desktop-use-hosted
uv run --with pytest python -m pytest tests/ -q
```

Coverage:

- `tests/test_remote.py` / `tests/test_remote_desktop.py`: `RemoteDesktop`
  HTTP mapping against a mock server
- `tests/test_cli_flags.py`: `--sandbox-url` routes to remote mode; local
  path does not call `run_remote`

## Live eval: hosted_e2e

```bash
# terminal A: data plane
cd desktop-sandbox
./scripts/run-local.sh --no-vnc
# or: uv run python -m sandbox.server --port 7090 --no-vnc

# terminal B: control plane
cd desktop-use-hosted
export SANDBOX_URL=http://127.0.0.1:7090
uv run python evals/hosted_e2e.py
```

What it does:

1. `GET $SANDBOX_URL/health` (SKIP with clear message if down)
2. `RemoteDesktop` screenshot + move/click (and best-effort xterm + `date`)
3. If `OPENAI_API_KEY` is set, runs a short `desktop_use.agent --sandbox-url ...` task

Artifacts: `/tmp/hosted-e2e-shot.png`, plus agent final shot under `/tmp` when
the optional model step runs.

### Model-backed optional step

```bash
export OPENAI_API_KEY="…"
export OPENAI_BASE_URL=https://openrouter.ai/api/v1   # or local vLLM/Ollama
export LOCAL_LOOP_MODEL=anthropic/claude-haiku-4.5
uv run python evals/hosted_e2e.py
```

## Probe only (CLI)

```bash
uv run python -m desktop_use.agent --sandbox-url http://127.0.0.1:7090 --probe
```

## Pair integration (from sandbox repo)

```bash
cd desktop-sandbox
./scripts/e2e-pair.sh
# starts sandbox if needed, then probes hosted RemoteDesktop / HTTP contract
```

## Console remote mode

```bash
uv run python -m desktop_use.ui \
  --sandbox-url http://127.0.0.1:7090 \
  --stream-url ws://127.0.0.1:6080 \
  --base-url https://openrouter.ai/api/v1 \
  --model anthropic/claude-haiku-4.5 \
  --api-key "$OPENAI_API_KEY"
# open http://127.0.0.1:7788
```

When `--sandbox-url` is set, the console must **not** spawn local Xephyr/VNC.

## Pass criteria (manual / nightly)

| ID | Task | Pass |
|----|------|------|
| T1 | Open terminal, run `date` | Terminal + date-like output visible |
| T2 | Open browser to example.com | Example Domain content visible |
| T3 | `echo hello` in terminal | `hello` visible |

Do not claim green without a real run and screenshot evidence.
