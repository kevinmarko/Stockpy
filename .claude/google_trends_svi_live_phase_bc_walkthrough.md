# Google Trends live viewer — Phases B + C walkthrough (API + Search Attention screen)

**Operator decisions (2026-10-11):** freeze exception granted for the screen ("do b and c"); ship Phases A + B + C together in PR #1114 ("ship it all at once"); built by 6 parallel agents with lead review ("have sonnet have 6 agents build them out … and you double check it"); desktop sidebar + Research hub only (no Marketplace/mobile tile).

Design: `.claude/google_trends_svi_live_implementation_plan.md` §3. Interface contract: `.claude/google_trends_svi_live_phase_bc_contract.md`. Build order: `.claude/google_trends_svi_live_phase_bc_task.md`. Phase A: `.claude/google_trends_svi_live_phase_a_walkthrough.md`.

## How it was built

The lead agent wrote one binding contract with the function signatures and payload fields. Six agents then built against it in three waves, so no two agents edited the same file at once. The lead reviewed and verified each wave before committing it and starting the next.

| Wave | Agent | Scope | Lead review outcome |
|---|---|---|---|
| 1 | B1+B2 | `TrendsStore` read helpers + `data/trends_view.py` + tests | Chain walk uses the stitcher's own functions; status order matches the contract. Accepted. |
| 1 | C1+C2 | TS types, `liveApi`/`mockApi`, deterministic mock fixtures | Types match the contract 1:1. Accepted; window walk fixed later (see below). |
| 1 | docs | `TAB_HELP.trends` + glossary, HOW_TO_GUIDE section, signal/architecture docs, MCP route list | Accepted. |
| 2 | B3+B4 | Two endpoints in `api/data_api.py` + HTTP tests | Accepted. The agent found the root cause of the earlier spike's "no such table" failures (below). |
| 2 | C3 | Three charts + seams table | Fixed: the ASVI y-axis now always includes 0. |
| 3 | C4 | `TrendsAttention` screen + tests | Accepted (CSS token check, no fabricated defaults). |
| 3 | C5 | Route, sidebar entry, Research hub card | Full vitest suite + build green. Accepted. |
| — | `api-parity-reviewer` | TS vs Python field-by-field | No mismatches, all explicit generics, both endpoints have a caller. |

## Things worth learning from

- **In-memory SQLite and threads.** FastAPI runs sync endpoints in a worker thread. An in-memory SQLite database is private to the connection that created it, so the endpoint saw an *empty* database. That is why the earlier design spike failed 9 of 16 tests. HTTP tests now use a file-backed SQLite in `tmp_path`. Unit tests of the pure builders can still use in-memory stores, because they run on one thread.
- **Seams are a chain.** Window *k* is scaled against the stitch so far, so `applied_factor` is cumulative (window 0's scale). `pair_factor` is the neighbour ratio. Both are shown, so the table teaches the difference.
- **Recompute check on real data.** The data API ran locally against a database the *real* daemon refresh wrote (only Google's fetch was faked). `recompute_check.matches` was `true`. So the viewer's re-stitch reproduces exactly what the daemon stored.

## Lead fixes made during review

1. `TrendsAsviChart`: the y-domain always includes 0, because Recharts hides an out-of-domain `ReferenceLine`.
2. `.notice-{warn,info,success}` (`webapp/src/index.css`) hard-coded light text tints meant for the dark theme. On the light theme they were unreadable (#fce3b4 on near-white). Light-theme overrides now use dark tones of the same hues. This affects every notice in the app; it was found because the recompute-mismatch warning was illegible.
3. The mock window walk was off by one versus `plan_windows` (it echoed "91 d"). It now walks start..start+90 inclusive with the next window starting at end−30, like the backend, and echoes the real cap/retention defaults.
4. The `stitched_only` reason now names the common real cause: data stored before download batches existed. That is the state of the 6 live terms until their first post-Phase-A download.

## Verification evidence (this session)

- Python: `tests/test_trends_store.py`, `test_trends_view.py`, `test_data_api_trends_svi.py`, `test_data_api.py`, settings census/liveness, timeouts AST guard → 207 passed (wave 2), plus 47 after the reason tweak.
- Webapp: `npm run typecheck` clean; full `npm test` 153 files / 1779 tests passed; `npm run build` OK (wave 3). Trends files re-run after fixes: 11 files / 144 tests passed.
- **Browser, mock mode** (Chromium via playwright-core, desktop 1366 px + phone 390 px): every mock term renders its intended state. 0 console errors, no `NaN`, no page-level horizontal scroll at phone width, and the mismatch warning is legible after fix 2.
- **Browser, real API** (`VITE_USE_MOCK=false`, real `api.data_api` on a scratch SQLite seeded by the real daemon refresh): every `/data/trends/*` request returned 200; 0 Trends-related console errors (the 15 others were `ERR_CONNECTION_REFUSED` from services not started); `ok`, `stitched_only`, `partial_only` and `no_data` render correctly; 422 `invalid_term` confirmed with curl.
- Full offline pytest suite: GitHub CI on the final head (see PR).

## Not verified here

- The operator's real database and live backend. After merge and a daemon restart, open `/research/trends`. Expect the 6 existing terms to show `stitched_only` until each gets its first complete download (at most 10 terms per pass, one pass per timer wake).
