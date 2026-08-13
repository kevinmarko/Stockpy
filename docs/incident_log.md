# InvestYo Advisory Platform — Incident Log

Chronological record of operational anomalies, pauses, and remediations. Each entry
is appended; never edited or deleted. Pair with `output/decision_log.jsonl` for the
per-signal operator log.

---

## Template (copy for new entries)

### YYYY-MM-DD — short title

- **Detected:** how the anomaly surfaced (preflight failure, calibration MAE
  spike, dead-letter queue entry, manual observation)
- **Symptom:** observable state at detection
- **Root cause:** what was actually wrong
- **Remediation:** what was done; reference commits/PRs by SHA or URL
- **Pause taken?** yes / no — if yes, link to the matching decision_log.jsonl entry
- **Follow-up:** open items, watchlist entries, Gravity steps added

---

## Entries

### 2026-08-13 — ForecastTracker bypassed LOCAL_DATA_ROOT, split forecast_errors across two databases

- **Detected:** direct manual inspection (comparing `lsof` output for the running
  orchestrator daemon process against file mtimes/row counts in both databases) while
  verifying the `settings.LOCAL_DATA_ROOT` migration (PR #718) post-deploy — not by any
  automated test, alert, or preflight check.
- **Symptom:** the live orchestrator daemon (`desktop.orchestrator_daemon --interval 300`),
  restarted onto PR #718's merged code, was writing real data into two different SQLite
  databases simultaneously. Every other table (`price_bars`, `account_snapshots`,
  `symbol_rating_events`, etc.) correctly moved to the new `LOCAL_DATA_ROOT`-anchored DB;
  the `forecast_errors` table alone kept accumulating in the old, repo-relative DB
  location for hours, undetected. At discovery: 1,974,166 real `forecast_errors` rows
  existed in the old location; zero existed yet in the new one.
- **Root cause:** `forecasting/forecast_tracker.py`'s `ForecastTracker.__init__` had a
  hardcoded, CWD-relative default `db_path: str = "quant_platform.db"` that completely
  bypassed `db_config.resolve_database_url()` / `settings.LOCAL_DATA_ROOT`, missed during
  PR #718's migration. This operator's real `.env` has
  `FORECAST_SKILL_WEIGHTING_ENABLED=true`, so `main_orchestrator.py`'s
  `EngineContext.build()` constructs a bare `ForecastTracker()` every cycle, hitting the
  unfixed default on every run.
- **Remediation:** fixed in PR #720 (branch `fix-forecast-tracker-db-path`) —
  `ForecastTracker.__init__`'s default now resolves via `db_config.resolve_database_url()`
  exactly like every sibling store. Covered by 3 new regression tests in
  `tests/test_forecast_tracker.py`. Full write-up:
  `docs/known_issues/forecast_tracker_local_data_root_split.md`.
- **Pause taken?** No — `forecast_errors` is a diagnostic/opt-in feature table (backs the
  opt-in `FORECAST_SKILL_WEIGHTING_ENABLED` forecast-blending feature), not order
  execution or position sizing; no kill-switch was triggered and none was warranted.
- **Follow-up:**
  - Reconcile the diverged `forecast_errors` data between the old and new database — still
    pending, needs explicit operator sign-off on approach before any move/merge is
    attempted (a naive overwrite risks destroying real data in one location or the other).
  - Audit for any other module constructing its own DB connection with a
    similarly-shaped hardcoded bare-literal default that bypasses
    `db_config.resolve_database_url()`. This is now a confirmed recurring bug class — two
    instances found so far (`data/historical_store.py` in PR #718,
    `forecasting/forecast_tracker.py` in PR #720) — worth a deliberate, dedicated sweep
    rather than assuming it's fully closed.
