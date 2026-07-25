# GOAL: Holo Models API dual-backend (desktop-use-hosted)

**Goal ID:** holo-backend-transition  
**Status:** Phase 1 **DONE** for public MVP **0.0.1**

## Product direction

- **Dual backends:** generic (Claude / OpenRouter / OpenAI-compat) + **Holo profile**
- Shared upgrades that help everyone; Holo-specific harness only on the Holo path
- Generic remains default; Holo via auto-detect `api.hcompany.ai` and/or `--model-backend holo|generic`
- Prompt/image caching and full multi-turn Holo observation history: later phases

## Phase 1 deliverables (shipped)

1. `model_backends.py`: detect, scale `[0,1000]`→pixels, tool map, request bodies, normalize
2. `ask_model` branches on backend; Holo structured path; generic path non-regression
3. Unit tests in `tests/test_model_backends.py` (plus existing suite)
4. Live E2E proven against desktop-sandbox: Holo 35B, Holo 122B, Claude Sonnet 5 (OpenRouter)
5. Sandbox API unchanged (pixel actions only)

## Later phases (not blocking 0.0.1)

- Multi-turn Holo fidelity (observation history, image budget eviction)
- Provider caching where available
- Multi-tenant / authenticated console (out of MVP scope)

## Non-goals for Phase 1

- Rewriting desktop-sandbox for model-native coords
- Claiming every vision model grounds UI well (Mistral generic E2E failed on grounding)
