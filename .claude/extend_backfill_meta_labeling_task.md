# Task tracker — extend Forecast Backfill meta-labeling to all 6 eligible signals

Branch: `extend-backfill-meta-labeling`

- [x] **WP1** — Correct the stale "gate refuses all 6 eligible signals" disclosure
      (webapp screen, `ml/meta_bootstrap.py`, docs). Derive `is_active` from
      `BACKFILL_ELIGIBLE_SIGNAL_IDS` instead of a hardcoded 3-name list.
- [x] **WP2** — Honest per-signal eligibility reporting: `self.eligibility` +
      `_mark_eligibility()`, surfaced via the summary JSON, API, TS types, mock,
      and a new "Eligible Signals Not Trained" webapp section. Multi-select now
      seeds from eligibility too.
- [x] **WP3** — Real point-in-time `accrual_ratio`/`gross_profitability`/`sector`
      data (extracted from `scripts/refresh_validations.py` into
      `data/sneqr_quality_facts.py`), unblocking `sector_quality_rank`. Verified
      live against real SEC EDGAR data (6-name Technology-sector universe, all 4
      horizons trained).
- [x] **WP4** — Quarantined realized-vol proxy (`IVR_Proxy`/`VRP_Proxy`,
      `ml/vrp_premium_selling_proxy_signal.py`) for `vrp_premium_selling`, which
      cannot be genuinely unblocked (real historical per-ticker IV is
      structurally unavailable from FMP/Yahoo). Structurally excluded from
      `BACKFILL_ELIGIBLE_SIGNAL_IDS` — can never reach live inference.
- [x] **WP5** — Suppress `options_flow_sentiment`'s live-production
      price-momentum fallback on the backfill path only (it was about to
      silently train a meta-labeler on price momentum mislabeled as options
      flow sentiment). `signals/options_flow_sentiment.py` itself untouched,
      verified zero diff.
- [x] Doc corrections found by an independent audit pass: two forward-looking
      "options_flow_sentiment trains too" claims (written before WP5 landed)
      corrected in `CLAUDE.md`/`AGENTS.md` and `docs/architecture/ml-and-reports.md`;
      a broken markdown table in `docs/known_issues/README.md` fixed; dangling
      "WP4 section" cross-references now resolve; WP4/WP5 doc sections reordered
      to numeric order in `docs/plans/FORECAST_BACKFILL_PLAN.md`.
- [x] Full verification: offline + live-network `tests/test_forecast_backfill.py`
      (60/60), `tests/test_train_meta_labelers.py`/`test_meta_labeling.py`/
      `test_registry_load.py`/`test_strategy_engine.py` (97/97),
      `tests/test_measure_settings_census.py` (regenerated for the 2 new
      settings), webapp typecheck clean, webapp `ForecastBackfillScreen` +
      `forecastBackfillCopy` tests (28/28), ruff genuine-bug gate clean.
- [ ] Open PR against `main`, self-review the full diff, merge once verification
      is confirmed green in this session (per `CLAUDE.md`'s Start-of-session
      checklist §5 — no separate go-ahead needed for a change of this shape,
      since it's gated entirely behind opt-in settings defaulting to `False`
      and touches no live execution/broker behavior).
- [ ] Sync local `main` checkout after merge (`CLAUDE.md` §7).

## Explicitly out of scope (disclosed, not silently dropped)

- No DB persistence/caching for the WP3 EDGAR fetch — every backfill run
  re-fetches company-facts JSON per ticker. Documented as a deliberate,
  disclosed scope trim in `docs/plans/FORECAST_BACKFILL_PLAN.md`'s WP3 section.
- The LIVE per-cycle trading pipeline is untouched — `sector_quality_rank`
  remains dormant there (`processing_engine.calculate_fundamental_metrics()`
  still doesn't compute either raw input for production scoring).
- `volatility/bootstrap_iv_history.py`'s pre-existing, separate hazard
  (writes synthetic IV into the real `iv_history` table with no provenance
  marker) — flagged in `docs/known_issues/vrp_premium_selling_no_historical_iv.md`,
  spawned as its own follow-up task per the operator's explicit choice, not
  fixed in this change.
