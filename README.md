# desktop-use

**A self-hosted computer-use agent with a mission-control web console. One desktop, one loop, zero cloud runtime.**

[![License: MIT](https://img.shields.io/badge/License-MIT-1b232d.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-1b232d.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.0.1-ffb454.svg)](https://github.com/arthurkatcher/desktop-use/releases)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-63b981.svg)](CONTRIBUTING.md)

`desktop-use` re-implements the classic vision-agent runtime loop (screenshot, vision-language model, synthetic input) as two small readable Python files, and wraps it in a real-time operator console: live VNC view of the agent's desktop, streaming reasoning transcript, per-step snapshot replay, and three ways to intervene mid-flight without killing the run.

It talks to **any OpenAI-compatible vision model endpoint**: OpenRouter, Ollama, vLLM, llama.cpp, LM Studio. No vendor runtime, no closed binaries, no telemetry.

```text
 ┌────────────────────────────────────────────────────────────────────┐
 │  Browser console (localhost:7788)                                  │
 │  ┌──────────────────┐   ┌───────────────────────────────────────┐  │
 │  │ streaming         │   │ live desktop (noVNC canvas)           │  │
 │  │ reasoning         │   │ or per-step snapshot replay           │  │
 │  │ transcript (SSE)  │   │ [LIVE | SNAPSHOTS]  ‹ ● ● ● ● ›       │  │
 │  └──────────────────┘   └───────────────────────────────────────┘  │
 └───────────▲──────────────────────────▲─────────────────────────────┘
             │ SSE / REST               │ websocket (websockify)
 ┌───────────┴──────────┐   ┌───────────┴───────────┐
 │  ui.py               │   │  x11vnc               │
 │  sessions, events,   │   │                       │
 │  runner thread       │   │                       │
 └───────────▲──────────┘   └───────────▲───────────┘
             │ scrot + XTest (python-xlib)
 ┌───────────┴──────────────────────────┴───────────┐
 │  Xephyr :2  (nested X display, openbox)          │
 │  the agent's private desktop, isolated from      │
 │  your real session                               │
 └──────────────────────────────────────────────────┘
             ▲
             │ base64 PNG + JSON actions
 ┌───────────┴──────────────────────────────────────┐
 │  any OpenAI-compatible vision model              │
 │  OpenRouter / Ollama / vLLM / llama.cpp          │
 └──────────────────────────────────────────────────┘
```

## Why

Cloud computer-use agents are black boxes: closed runtimes, opaque loops, screenshots you never see again. `desktop-use` is the opposite. The whole agent brain is ~400 lines you can read in one sitting, every step of every run is persisted to plain files on your disk, and the model behind it is whatever endpoint you point it at. It exists to make the loop itself inspectable, hackable and debuggable.

## Features

### The agent loop (`agent.py`)
- **Screenshot to action**: captures the nested display with `scrot`, sends the PNG plus task and history to the model, parses one JSON action, injects it with XTest via pure `python-xlib`. No xdotool, no system automation daemons.
- **Action space**: `click`, `double_click`, `right_click`, `move`, `type`, `key` (combos like `ctrl+l`), `click_type` (click a field and type in one step), `scroll`, `wait`, `done`.
- **Before/after vision**: each turn the model receives the previous screenshot alongside the current one, so it can see what its own last action actually did (including "I just closed my own window").
- **Corrective retries**: a malformed or wrong-shape reply is fed back to the model with the parse error. At temperature 0 a blind retry reproduces the identical bad output; a corrective one fixes it.
- **JSON prefill**: the request ends with an assistant message containing `{`, which forces the reply to continue as JSON and stops tool-call-syntax slips at the source. Parsing handles backends that honor prefill and backends that ignore it.
- **Anti-loop guards**: fuzzy repeat detection (same action type within a few pixels counts as a repeat) injects an explicit "this is not working, change strategy" note; a screen-changed flag after every action tells the model whether its click did anything.
- **Safety rails**: refuses to drive `:0`/`:1` (your real session) unless explicitly overridden; the managed environment scrubs `WAYLAND_DISPLAY` so apps launched inside the session cannot escape to your real desktop.

### The console (`ui.py` + `home.html` + `ui.html`)
- **Sessions home**: every run is a session with status badge (RUNNING / COMPLETE / STOPPED / INCOMPLETE / ERROR), final-screen thumbnail, model, step count, duration and age. Launch new sessions from a prompt box. One session runs at a time; the page links you to the active one.
- **Live view**: real VNC streaming of the agent's desktop (x11vnc + websockify + noVNC), not screenshot polling. An amber border flash marks every physical action.
- **Snapshot replay**: one PNG per step, browsable through a capped timeline scrubber with arrow buttons and keyboard navigation. Clicking a step card shows its snapshot and vice versa. Finished sessions open directly in replay mode.
- **Streaming transcript**: reasoning, the action as a terminal-style command line, per-step model latency, and the outcome (screen changed / no change / not executed) stream in over SSE with reconnect dedupe.
- **Interaction triad**, all on one boundary contract (an in-flight decision is discarded and marked "not executed", never half-applied):
  - **Stop**: signal now, halts at the step boundary, pending action never runs.
  - **Take control**: confirm dialog, agent pauses, you drive the desktop through the browser canvas; on release you choose "continue task" (the agent gets a context note that a human intervened and re-reads the screen) or "stop session".
  - **Mid-flight messages**: type an instruction while the agent works; it is injected into the agent's context at the boundary with precedence over earlier plans, and the agent adapts. Messages are part of the persisted session record.
- **Light and dark themes**: follows your OS preference by default, explicit toggle persists and wins, no flash on load.
- **Flat, line-based UI**: IBM Plex, operator-console aesthetic, no decorative shadows.

### Persistence
Every session is a plain directory you can grep, diff and archive:

```text
sessions/20260724-182747/
├── meta.json      # id, task, model, status, started, ended, steps
├── events.jsonl   # append-only, seq-numbered event log (the source of truth)
├── 1.png … N.png  # one screenshot per step
└── final.png      # the screen at completion
```

Replays are reconstructed entirely from these files, so history survives server restarts. No database.

## Requirements

Python-side there is nothing to install: [`uv`](https://docs.astral.sh/uv/) resolves the two script dependencies (`httpx`, `python-xlib`) automatically from the file headers on first run. The code also spawns and tears down the whole desktop environment (Xephyr, openbox, x11vnc, websockify) by itself. You only need the system binaries present:

```bash
# Debian / Ubuntu (tested)
sudo apt install xserver-xephyr openbox scrot xterm x11vnc novnc websockify

# uv, if you do not have it yet
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Approximate package names elsewhere (untested): Fedora `xorg-x11-server-Xephyr openbox scrot xterm x11vnc novnc python3-websockify`, Arch `xorg-server-xephyr openbox scrot xterm x11vnc novnc python-websockify`.

Notes:

- Wayland hosts are fine: the agent runs in a nested X server, and the code scrubs the Wayland environment so nothing escapes to your real desktop.
- `xterm` and the browser are what the agent actually drives; the bundled openbox menu expects a terminal to exist.
- The headless CLI (`agent.py`) needs only `xserver-xephyr openbox scrot xterm`; the VNC trio is for the console's live view.
- A vision-capable model endpoint is required (see [Model backends](#model-backends)).

## Quickstart

```bash
git clone https://github.com/arthurkatcher/desktop-use.git
cd desktop-use

# console with OpenRouter (recommended model: claude-haiku-4.5)
OPENAI_API_KEY=sk-or-... uv run ui.py \
  --base-url https://openrouter.ai/api/v1 \
  --model anthropic/claude-haiku-4.5 \
  --max-steps 25
# open http://localhost:7788, type a task, press LAUNCH SESSION
```

Everything (the nested display, VNC stack and web server) starts together and tears down together on Ctrl+C.

### Local model instead

```bash
ollama pull qwen2.5vl && ollama serve   # or vLLM / llama.cpp serving any VLM
uv run ui.py --model qwen2.5vl          # defaults to http://localhost:11434/v1
```

### Headless CLI (no console)

```bash
uv run agent.py --probe                          # verify capture + input, no model needed
uv run agent.py "open a terminal and run ls"     # spawns and tears down its own display
uv run agent.py --display :2 "..."               # attach to an existing display instead
```

## Driving the console

| Control | Where | What it does |
|---|---|---|
| LAUNCH SESSION | home | starts a run and opens its session page |
| STOP | session header | halts at the next step boundary; pending action never runs |
| LIVE / SNAPSHOTS | control deck | switch between the real-time canvas and step replay |
| ‹ › and arrow keys | control deck | step through snapshots; dots are capped at ~15 wide and scroll |
| TAKE CONTROL | control deck | pause the agent and drive the desktop yourself (confirm dialog) |
| RELEASE CONTROL | control deck | choose: continue the task, or stop the session |
| message bar | under transcript | send the agent a mid-task instruction |
| ◐ | header | toggle light/dark theme |
| ⌃↵ | prompt box | launch from the keyboard |

Sessions that are not the active one open in snapshot replay with LIVE and TAKE CONTROL disabled, because the single desktop belongs to whichever session is running.

## Model backends

Any endpoint that speaks `POST /v1/chat/completions` with image input works. Findings from real runs:

| Model | Verdict |
|---|---|
| `anthropic/claude-haiku-4.5` (OpenRouter) | recommended: precise UI grounding, fast, cheap |
| Claude Sonnet tiers | stronger reasoning when tasks get long |
| Gemini Flash tiers | poor pixel grounding in our testing (missed 20px targets by ~100px); mandatory hidden reasoning eats the token budget |
| `qwen2.5vl` (Ollama) | workable fully-local option |
| Holo1.5 open weights (vLLM) | purpose-built UI grounding, good local choice |

For OpenRouter the client automatically requests low reasoning effort so hidden thinking does not starve the JSON output.

## HTTP API

The console is a plain REST + SSE surface you can script against:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | sessions home |
| GET | `/s/<id>` | session console |
| GET | `/sessions` | JSON list of all sessions |
| POST | `/run` `{"task": "..."}` | launch a session (409 if one is running) |
| POST | `/stop` | request stop at the next boundary |
| POST | `/message` `{"text": "..."}` | queue a mid-flight instruction |
| POST | `/control/take` | pause the agent, user takes the desktop |
| POST | `/control/release` `{"continue": bool}` | hand back and resume, or stop |
| GET | `/events?sid=<id>` | SSE stream: full replay then live tail |
| GET | `/shot/<sid>/<n>.png` | any step screenshot |

## Troubleshooting

- **x11vnc exits immediately on a Wayland host**: it refuses to start when `WAYLAND_DISPLAY` is set. `ui.py` already scrubs it; if you run x11vnc by hand, do `env -u WAYLAND_DISPLAY XDG_SESSION_TYPE=x11 x11vnc -display :2 ...`
- **Apps open on your real desktop instead of the session**: something inherited your Wayland environment. Launch apps through the openbox menu inside the session (the managed env scrubs the variables), and give Chrome its own profile dir so your real Chrome instance does not capture the invocation.
- **A run hangs silently**: check whether a previous display or VNC process is orphaned. Kill by exact pid; note that `pkill -f` with a pattern that appears in your own command line kills your shell (use a `[b]racket` pattern).
- **The agent loops clicking the same spot**: usually a text field (focus is invisible in screenshots). The `click_type` action and the repeat guard exist for this; if you add actions, keep them atomic.
- **Model replies fail to parse**: the corrective retry usually heals it within one attempt. If a backend consistently fails, check whether it honors assistant prefill and whether it supports image input at your resolution.

## Roadmap

- Parallel sessions (one nested display + VNC stack per session)
- Visual diff highlights between consecutive snapshots
- Token/cost telemetry per step in the gauge cluster
- Model picker in the console header
- Optional sqlite index over sessions for cross-session search and stats

## Contributing, security, license

- [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and pull request conventions
- [SECURITY.md](SECURITY.md) for the threat model and how to report vulnerabilities
- [MIT](LICENSE)
