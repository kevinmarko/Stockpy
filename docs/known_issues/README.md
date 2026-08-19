# Known Issues

Dated write-ups of real production issues found and root-caused in this codebase —
kept even after a fix lands, because the "why" (root cause, what was tried and
rejected, what's still unverified) is exactly the context a future regression needs
and a commit message won't carry. See [`docs/README.md`](../README.md) for the full
documentation library index.

| File | Status |
|------|--------|
| [`cnn_lstm_tf_deadlock.md`](cnn_lstm_tf_deadlock.md) | **Fixed** (Round 8) — TensorFlow eager-execution deadlock in the CNN-LSTM forecaster; see CLAUDE.md's CNN-LSTM subprocess-isolation bullet for the production fix this informed |
| [`lightgbm_faiss_libomp_collision_segfault.md`](lightgbm_faiss_libomp_collision_segfault.md) | **Fixed** (two rounds — a segfault, then a separate deadlock) — OpenMP `libomp` collision between lightgbm and faiss sharing a process; see CLAUDE.md's rag_index/faiss bullet for the production fix |
| [`robinhood_device_approval_login_hang_risk.md`](robinhood_device_approval_login_hang_risk.md) | **Mitigated by design, not yet verified against a real Robinhood account** — device-approval login has no built-in timeout; see CLAUDE.md's Robinhood login bullet for the killable-subprocess fix |
| [`react_router_dom_ghsa_jjmj_open_redirect.md`](react_router_dom_ghsa_jjmj_open_redirect.md) | **Resolved** — fixed in [PR #475](https://github.com/kevinmarko/Stockpy/pull/475), `react-router-dom` bumped to `7.18.2` |
| [`vite_plugin_pwa_workbox_dev_chain_unfixable.md`](vite_plugin_pwa_workbox_dev_chain_unfixable.md) | **Resolved** — targeted `overrides` entry in `webapp/package.json`; `npm audit` now 0 findings (was 8 high) |
| [`pip_audit_stale_ambient_env_false_positive.md`](pip_audit_stale_ambient_env_false_positive.md) | **Resolved — false positive.** A reported 36-vulnerability pip-audit result was scanning the wrong Python environment; a rebuilt `.venv` showed zero |
| [`2026_08_security_quality_review.md`](2026_08_security_quality_review.md) | **Findings fixed, documented.** A 2026-08-05 proactive security/quality review (no open GitHub issues existed at the time) |
| [`forecast_tracker_local_data_root_split.md`](forecast_tracker_local_data_root_split.md) | **Code fixed (PR #720); data reconciliation still pending.** `ForecastTracker` kept writing `forecast_errors` to the old repo-relative DB after the `LOCAL_DATA_ROOT` migration, splitting it from every other table for hours undetected — see CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet |
| [`webapp_memory_leak_investigation.md`](webapp_memory_leak_investigation.md) | **Resolved & Verified** — Memlab V8 heap profiling identified focus retention and WebSocket reconnect edge cases; hardened `useLiveTick.ts`, `LogStream.tsx`, `Modal.tsx`, and `CommandPaletteModal.tsx` with automated test coverage |
| [`codeql_advanced_outage_and_bandit_addition.md`](codeql_advanced_outage_and_bandit_addition.md) | **Resolved.** CodeQL Advanced was blocked first by a real default-setup/advanced-setup config conflict (cleared on its own), then by a live GitHub.com code-scanning-ingestion outage (external, not a code defect). Added `bandit` as an independent CI SAST job that never depends on GitHub's code-scanning API; 44-finding baseline individually triaged and suppressed with `# nosec BXXX` |
| [`scenario_matrix_field_mismatch.md`](scenario_matrix_field_mismatch.md) | **Fixed** ([PR #808](https://github.com/kevinmarko/Stockpy/pull/808)) — Paper Broker screen crashed on every visit against a live backend because `ScenarioHeatmap.tsx` and `pilots/scenario_matrix.py` disagreed on almost every field name; one instance of the "Options desk mock/live API parity" gap CLAUDE.md already documents, worse than the named ones since it's mounted unconditionally |

Three of these (`cnn_lstm_tf_deadlock.md`, `lightgbm_faiss_libomp_collision_segfault.md`,
`robinhood_device_approval_login_hang_risk.md`) are also individually cross-linked from
CLAUDE.md's own architecture bullets, since the production fixes they informed are
load-bearing. The rest are indexed here only.
