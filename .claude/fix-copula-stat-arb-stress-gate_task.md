# Task Tracker: `copula_stat_arb` options-selling stress-gate exclusion

- [x] Read `pilots/copula_stat_arb.py` in full; read `docs/signals/copula_stat_arb.md`.
- [x] Determine whether `copula_stat_arb` actually sells options (read trade-construction code,
      not the name/module grouping). Finding: **no** — it trades equity shares only.
- [x] Since it's not an options-selling strategy: add documentation-only fix.
  - [x] Comment in `scripts/refresh_validations.py` near `_resolve_options_selling_stress_fn`.
  - [x] Note in `docs/signals/copula_stat_arb.md`'s "Backtest Validation" section.
  - [x] Addendum to `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s existing 2026-08-19 entry.
- [x] Verify: `scripts/refresh_validations.py` still parses/imports; `_resolve_options_selling_stress_fn`
      behavior unchanged (`copula_stat_arb` -> `None`, `vrp_premium_selling` -> real fn).
- [x] Run `tests/test_refresh_validations.py` (151 passed), `tests/test_copula_stat_arb.py`,
      `tests/test_command_manifest_freshness.py`, `tests/test_stress_gate.py` (all passed).
- [x] Commit on branch `fix-copula-stat-arb-stress-gate`, push, open PR (not merged).
