# How the README demos are recorded

The demo GIFs in `assets/` are real, unscripted agent sessions captured off the
operator console. No frames are staged; the model output you see is what the
model did on that run. This is the pipeline, so the next person (or the next
release) can reproduce them.

## Stack under recording

1. **Data plane** — `desktop-sandbox` in Docker, fresh-started so the desktop
   has no leftover windows (a warm desktop lets the agent shortcut the task,
   which makes a boring demo):

   ```bash
   docker restart desktop-sandbox   # or make run on first boot
   curl -s -H "X-Sandbox-Token: dev-sandbox-token" http://127.0.0.1:7090/health
   ```

2. **Control plane** — this repo's console against the sandbox, any
   OpenAI-compatible VLM (the shipped demos ran Holo):

   ```bash
   uv run python -m desktop_use.ui \
     --sandbox-url http://127.0.0.1:7090 --sandbox-token dev-sandbox-token \
     --stream-url ws://127.0.0.1:6080 \
     --base-url "$OPENAI_BASE_URL" --api-key "$OPENAI_API_KEY" \
     --model holo3-1-35b-a3b --max-steps 25 --port 7788
   ```

## Capture

Driven with `playwright-cli` so every take has identical choreography:

```bash
playwright-cli open http://127.0.0.1:7788
playwright-cli resize 1600 900            # 16:9 — MUST match the video size
playwright-cli video-start demo.webm --size "1600x900"
# click ＋ NEW SESSION, fill the task, click LAUNCH SESSION
# ... wait for the session's done/idle event ...
playwright-cli video-stop
```

Gotchas learned the hard way:

- **Viewport and `--size` must be the same aspect ratio.** Playwright pads any
  mismatch with a gray letterbox strip baked into the video.
- **Restart the sandbox between takes.** Session state persists on the desktop;
  a second browser task will find the previous page already open and finish in
  two steps.
- The console's dark theme is the one that reads well on GitHub's dark mode.

## Post

Speed up and palette-quantize with ffmpeg (tune `setpts` so a take lands
around 20–25 s; keep GIFs under ~6 MB):

```bash
ffmpeg -i demo.webm -vf "setpts=PTS/2,fps=12,scale=960:-2:flags=lanczos,\
split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=\
dither=bayer:bayer_scale=4" demo.gif
```

Keep the source `.webm` files out of the repo; only the compressed GIFs are
committed.
