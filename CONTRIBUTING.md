# Contributing to local-loop

Thanks for considering a contribution. The project optimizes for one thing above all: **the whole loop stays readable in one sitting**. Two files, plain Python, no framework.

## Development setup

```bash
git clone https://github.com/<you>/local-loop.git
cd local-loop
# nothing to install: uv resolves script dependencies from the file headers
uv run agent.py --probe        # verifies the capture + input pipeline end to end
```

You need the system packages from the README (Xephyr, openbox, scrot, x11vnc, novnc, websockify) and any OpenAI-compatible vision endpoint for live testing.

## Ground rules

- **Test against a real run, not just by reading the code.** The failure modes of a computer-use loop (focus, timing, coordinate drift, model formatting slips) do not show up in unit tests. Before opening a PR, run at least one full session through the console and one through the CLI, and say so in the PR description.
- **Keep the boundary contract.** Stop, take-control and mid-flight messages all interrupt at the step boundary: an in-flight decision is discarded and marked `not executed`, never half-applied. New interruption mechanisms must follow the same contract.
- **Keep events append-only and replayable.** Anything the console shows must be reconstructable from `events.jsonl` alone (transient live-only events are the exception and must be cosmetic). If you add an event type, handle it in replay and in the SSE reconnect dedupe.
- **No new runtime dependencies without a strong case.** `httpx` and `python-xlib` are the entire footprint, and the frontend is dependency-free vanilla JS served from two HTML files.
- **Match the existing style.** Small functions, no classes where a function does the job, comments only where the code cannot say it (there are a handful; read them to calibrate).

## Pull requests

- Open an issue first for anything non-trivial, so the approach can be discussed before you invest time.
- Keep PRs focused: one behavior change per PR.
- Use conventional-style titles: `feat:`, `fix:`, `docs:`, `chore:`.
- Include what you tested (model, backend, task) in the description.
- Push review fixes as new commits; do not force-push a branch under review.

## Reporting bugs

Include the session directory (`sessions/<id>/`, minus any screenshots you consider private) when reporting agent behavior bugs. `events.jsonl` is usually enough to diagnose a loop or a bad decision without reproduction.

## Security issues

Do not open public issues for vulnerabilities; see [SECURITY.md](SECURITY.md).
