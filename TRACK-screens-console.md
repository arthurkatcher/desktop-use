# TRACK: Screens console MVP

Feature branch: `feat/screens-console-mvp`  
Plan: Screens / Sessions / Control / Settings (Parts A–H)  
Stop before Part H (live e2e) until operator says go.

---

## Part A — Branch + track + gitignore
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes
- Tests run: n/a (scaffold)
- Result: green
- Files touched: `.gitignore`, `TRACK-screens-console.md`
- Blockers / deferred: none

## Part B — Settings (TDD)
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes
- Tests run: `uv run … pytest tests/test_settings_store.py tests/test_settings_api.py -v`
- Result: green (fixed deadlock: `apply_preset` nested `Lock` → `_persist_unlocked`)
- Files touched: `settings_store.py`, `tests/test_settings_store.py`, `tests/test_settings_api.py`, `ui.py` (`/api/settings`, preset)
- Blockers / deferred: none

## Part C — App shell
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes
- Tests run: manual structure in home.html (sidebar nav)
- Result: green (sidebar Sessions | Screens | Settings)
- Files touched: `home.html`
- Blockers / deferred: none

## Part D — Screen registry + health + soft power
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes
- Tests run: `tests/test_screen_store.py`, `tests/test_screens_api.py`
- Result: green (real tiny health HTTP + real files)
- Files touched: `screen_store.py`, `remote.py` (`probe_health`), `ui.py` APIs, home Screens UI
- Blockers / deferred: live sandbox probe is Part H

## Part E — Session list pagination + filters
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes
- Tests run: `tests/test_sessions_pagination.py`
- Result: green
- Files touched: `ui.py` `SessionStore.list_page`, `/api/sessions`, home filters/load more
- Blockers / deferred: none

## Part F — Lease + launch bind
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes
- Tests run: store lease tests in `test_screen_store.py`; full suite green
- Result: green (unit/API); live multi-screen launch is Part H
- Files touched: `screen_store.py` lease, `Runner.launch(screen_id)`, `/run` body, home picker
- Blockers / deferred: e2e with real RemoteDesktop on leased screen = Part H

## Part G — Control FSM + TTL + UX routing
- Date: 2026-07-26
- Branch: feat/screens-console-mvp
- Done: yes (API + store + Screens chrome + session `/control/*` bridge)
- Tests run: control/TTL tests in `test_screen_store.py` + screens API take/release
- Result: green
- Files touched: control APIs, timer thread, pause bridge, Screens free take, leased deep-link CTA
- Blockers / deferred: live matrix M/C rows + ui.html polish = Part H / minor follow-up

## Full suite
- Command: `uv run --with pytest --with httpx --with python-xlib python -m pytest tests/ -q`
- Result: **132 passed** in ~17s (2026-07-26)

## STOP before Part H
Awaiting operator go for live sandbox e2e wave.
