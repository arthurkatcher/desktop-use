# GOAL (desktop-use-hosted)

Control-plane companion to desktop-use that accepts a **screen link** to a
remote Desktop API:

```bash
uv run ui.py \
  --sandbox-url http://127.0.0.1:7090 \
  --stream-url ws://127.0.0.1:6080 \
  --base-url … --model …
```

## Changes vs local-only desktop-use

- `remote.py`: RemoteDesktop HTTP client
- `--sandbox-url` / `--stream-url` / `--sandbox-token` on agent.py and ui.py
- `model_backends.py`: dual holo / generic model harness
- ui.html uses `__STREAM_URL__` for noVNC in remote mode
- Local Xephyr path unchanged when sandbox-url is unset

## Sandbox API (data plane contract)

| Method | Path | Response |
|---|---|---|
| GET | `/health` | `{"ok":true,"width":1280,"height":800,"display"?,"stream_ws"?}` |
| GET | `/screenshot` | PNG bytes |
| POST | `/action` | JSON action body; `{"ok":true}` |

Auth when token set: `Authorization: Bearer` and/or `X-Sandbox-Token`.

Paired data plane: [desktop-sandbox](https://github.com/arthurkatcher/desktop-sandbox).
