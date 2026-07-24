# Security Policy

## Threat model, honestly stated

`desktop-use` drives a desktop with a language model. Understand what that means before running it:

- **The agent executes real input events.** Everything it does inside the nested display is real: real clicks, real keystrokes, real shell commands if it opens a terminal. The nested Xephyr display isolates the *screen and input*, not the *filesystem or network*: a terminal inside the session is a terminal on your machine, running as your user.
- **Prompt injection is a live risk.** If the agent browses the web, page content becomes model input. A malicious page can try to steer the agent. Keep tasks scoped, keep `--max-steps` low, and watch runs you do not trust.
- **The console binds to localhost only** (`127.0.0.1:7788`), and the VNC port is `-localhost` as well. Do not reverse-proxy it to the network: there is no authentication layer. If you need remote access, put it behind an authenticating proxy and understand that anyone who reaches the console fully controls the desktop.
- **API keys** are read from the environment and sent only to the endpoint you configure. They are never written to disk by this project.
- **Session recordings contain your screen.** `sessions/` holds every screenshot of every run. Treat that directory as sensitive, and scrub it before sharing bug reports.

## Hard rails built in

- Refuses to drive display `:0`/`:1` (a real session) unless explicitly overridden with `--allow-real-display`.
- The managed environment scrubs `WAYLAND_DISPLAY` so applications launched inside the session cannot open windows on the host desktop.
- Stop, take-control and message interrupts discard in-flight actions at a step boundary rather than half-applying them.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Please do not open public issues for security problems. You can expect an acknowledgement within a week.
