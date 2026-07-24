# Changelog

## 0.0.1 (2026-07-24)

First public release.

- Agent loop (`agent.py`): screenshot to XTest action via any OpenAI-compatible
  vision endpoint; before/after screenshots, corrective retries, JSON prefill,
  `click_type` compound action, fuzzy repeat guard, managed Xephyr environment
  with automatic teardown, real-display refusal rail.
- Console (`ui.py`): session model persisted to plain files (meta, seq-numbered
  event log, per-step screenshots), sessions home with launcher and status
  badges, live noVNC desktop view, snapshot timeline with scrubber, streaming
  SSE transcript with reconnect dedupe, step-boundary interrupt contract shared
  by stop, take-control (pause / resume-or-stop with context note) and
  mid-flight user messages, light/dark theming.
