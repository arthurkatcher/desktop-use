# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.0.1 | Yes |
| earlier / unreleased trees | Best effort only |

Security fixes target the latest released version on the default branch.

## Threat model (single operator)

`desktop-use-hosted` is an **MVP control plane for one trusted operator**, not multi-tenant SaaS.

- **The agent executes real input events** on whatever desktop it is attached to (local Xephyr or a [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) instance). Clicks, keystrokes, and shell commands inside a terminal are real for that environment.
- **Isolation is not a full security boundary.** Local Xephyr isolates screen and input from your login session; it does **not** isolate filesystem or network from your user. A remote Docker sandbox is stronger isolation for untrusted UI processes, but the control plane still holds model API keys and session recordings.
- **Prompt injection is a live risk.** If the agent browses the web, page content becomes model input. A malicious page can try to steer the agent. Keep tasks scoped, keep `--max-steps` low, and watch runs you do not fully trust.
- **This is not a multi-user product.** There is no tenancy, no role model, and no console login.

## Console and network exposure

- The operator console binds **127.0.0.1** by default (`http://127.0.0.1:7788`).
- **There is no authentication** on the console REST/SSE surface. Anyone who can reach it can start sessions, stop them, inject messages, take control of the desktop view, and read session history for that process.
- Do **not** reverse-proxy the console to the public internet without an authenticating front door you trust. If you do expose it, treat that as full desktop control for any principal who can hit the proxy.
- Local VNC/websockify (when used) is intended for localhost-only access. Remote noVNC is served by the sandbox data plane; follow [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox) security guidance for stream bind addresses and tokens.

## API keys and secrets

- Prefer **environment variables** (`OPENAI_API_KEY`, `HAI_API_KEY`, `SANDBOX_TOKEN`, and aliases documented in the README) over putting secrets on the command line (argv is visible in process listings).
- Keys are sent only to the model base URL and sandbox URL you configure.
- This project does not intentionally write API keys into `sessions/` or other on-disk artifacts. Still treat session directories as sensitive (screenshots of whatever the agent saw).
- Never commit `.env` files, key material, or real tokens. Rotate any credential that may have been exposed in logs or shell history.

## Sandbox token and noVNC

- When the data plane is configured with a token, set `SANDBOX_TOKEN` / `--sandbox-token`. The control plane sends both `Authorization: Bearer` and `X-Sandbox-Token`.
- Optional sandbox auth protects the Desktop API. It does **not** replace console auth (the console has none).
- **noVNC risks:** a reachable stream URL lets a viewer see and, depending on sandbox configuration, interact with the agent desktop. Bind streams to loopback or protect them when the host is multi-user or networked. Prefer unique tokens per sandbox instance in CI and multi-instance layouts.
- Screenshots and action payloads leave the control plane toward the model host you chose (OpenRouter, Holo Models API, local vLLM, and so on). Choose providers accordingly.

## Hard rails in this control plane

- Local path refuses to drive real displays `:0` / `:1` (including forms like `:0.0`) unless `--allow-real-display` is set.
- Remote mode does not spawn Xephyr or touch the control-plane display.
- Model actions are allowlisted; `spawn`, `shell`, and similar non-agent types are rejected before local execute or sandbox `POST /action`.
- Managed local env scrubs `WAYLAND_DISPLAY` so apps launched inside the session are less likely to escape to the host desktop.
- Stop, take-control, and mid-flight message interrupts discard in-flight model decisions at a step boundary rather than half-applying them.
- Session shot and static path handlers reject path traversal (`..`, absolute escapes).

## Session recordings

`sessions/` stores every screenshot and the full event log of each run. Treat the directory as **sensitive**, keep it out of git (it is gitignored), and scrub before sharing bug reports. Prefer attaching `events.jsonl` (and carefully chosen frames) over bulk screen dumps.

## Reporting a vulnerability

Please report security issues via **GitHub Security Advisories** on this repository (Security tab → “Report a vulnerability”).

- Do not open public issues for vulnerabilities.
- Include a clear description, impact, and reproduction steps when possible.
- You can expect an acknowledgement within about a week.

We appreciate responsible disclosure. Please do not include real production keys or third-party customer data in reports.
