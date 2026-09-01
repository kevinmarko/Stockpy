# Google Trends Pipeline Audit Remediation — Task Tracker

## Audit phase (7 parallel agents, read-only)
- [x] Stitcher/ASVI hardening audit — found the 3000x fabrication regression
- [x] Live fetcher audit — found the CONSTRAINT #6 uncaught-exception bug
- [x] Persistence store audit — confirmed clean core, found misplaced test fixture + unbounded growth
- [x] Pipeline wiring audit — found phantom setting + false-negative-masking test
- [x] Daemon/API/webapp audit — found the genuine 3-way endpoint contract conflict
- [x] Docs finalization audit — found duplicate content + doc overclaims
- [x] LSTM-Attention model audit — found the model is completely non-functional (2 ImportErrors)

## Fix phase (6 parallel agents + 2 direct fixes)
- [x] Agent 1 — Stitcher: symmetric epsilon guard, reproduction test, no-lookahead perturbation test (13/13 pass)
- [x] Agent 2 — Live fetcher: 6 fixes (uncaught exception, dependency dup, dead import, rate-limiter lock+monotonic clock, test isolation, date-parse guard) (10/10 pass)
- [x] Agent 3 — Persistence store: fixture relocated to root `conftest.py`, upsert-dedup added (34/34 pass, existing-data migration caveat disclosed)
- [x] Agent 4 — Daemon/API/webapp: reverted competing endpoint contract to match PR #957/main exactly, deleted obsolete test file, fixed daemon per-symbol isolation (62/62 pass, typecheck clean)
- [x] Agent 5 — Pipeline wiring: real settings field added, broken test fixed (verified the except branch genuinely fires), state_snapshot field actually wired, dead import removed (33/33 pass)
- [x] Agent 6 — LSTM-Attention: both ImportErrors fixed (verified LIVE, real TF subprocess), CONSTRAINT #4 fabrication removed, output_shape bug fixed, AST-guard exemption added, dead Gravity function removed, CLAUDE.md/AGENTS.md corrected (13+83 pass)
- [x] Direct fix — `docs/signals/google_trends_asvi.md`: Section D overclaim corrected, missing settings added, related-files header expanded
- [x] Direct fix — `docs/architecture/data-layer.md`: duplicate bullet block removed

## Final verification (orchestrating session, independent of all fix agents' self-reports)
- [x] Re-ran full domain test suite: 217 passed
- [x] Re-ran broader blast-radius sanity suite (settings/main_orchestrator/daemon consumers): 190 passed
- [x] Re-ran webapp typecheck: clean
- [x] Confirmed both LSTM ImportError fixes via direct grep of the corrected import lines
- [x] Confirmed AST-guard exemption correctly excludes `lstm_diagnostic` from parametrization
- [x] Confirmed CLAUDE.md/AGENTS.md byte-identical via `cmp`
- [x] Confirmed the 5 reverted endpoint files are byte-identical to `main`
- [x] Confirmed diagnostic-only boundary intact (zero `SIGNAL_WEIGHTS`/`signals/` references)
