# Fix CPCV/PBO/DSR NaN honesty in `validation/autonomous_backtest_runner.py` (supersedes PR #786)

## Context

PR #786 was opened from a stale base (commit `86ab86b1`) that predates the
already-merged PR #785 (`3e44600e`, now `main` tip). `gh pr view 786` reports
`mergeable: CONFLICTING`. An `/code-review` pass on #786 found:

- Everything #786 changes in `scripts/preflight_check.py`, `universe_engine.py`,
  `validation/metrics.py`, and `.env.example` is **already fixed on `main`**,
  more completely, by #785 — reapplying #786's versions would be a straight
  regression (e.g. main's `validation/metrics.py` already removed the `-999.0`
  sentinel from **both** IS and OOS Sharpe via a documented `_rank_key`/
  `_nanmean_or` pattern; #786 only fixes the OOS side).
- `conftest.py`'s session-singleton reset change (`Settings()` →
  `Settings(_env_file=None)`) reintroduces a fix #785 explicitly investigated
  and **rejected** (verified: it silently skips `tests/test_validation_macro_regime.py`'s
  live-`FRED_API_KEY` network test).
- The new `settings.py` `NO_VENV_REEXEC` field is dead (nothing reads
  `settings.NO_VENV_REEXEC`; `scripts/_bootstrap.py` reads raw `os.environ`
  only) and breaks the committed settings-census/liveness freshness tests.
- `db_config.py`'s dynamic `resolve_database_url()` diverges from the still-frozen
  `DEFAULT_DATABASE_URL` constant, empirically reproduced to break
  `tests/test_investyo_mcp_server.py` under certain import orders.

**The one file #785 never touched — `validation/autonomous_backtest_runner.py`
(the CPCV engine behind the live `POST /pilots/ai/backtest/autonomous`
endpoint, `api/pilots_api.py`) — is a real, non-redundant fix**, but #786's own
version of it is itself incomplete: it fixes the OOS Sharpe `-999.0`
fabrication but leaves an identical IS Sharpe fabrication (and a downstream
fabricated-`0.0` substitution) in the very same method, and its removal of the
OOS sentinel newly exposes a real latent bug in the shared
`probability_of_backtest_overfitting()` (a NaN OOS Sharpe for the in-sample
winning trial is silently *not* counted as overfit, since `NaN < x` is always
`False` in Python/numpy — biasing PBO downward).

This PR discards everything in #786 that's redundant/regressive and lands
only the genuinely new, corrected fix — bringing `autonomous_backtest_runner.py`
up to the same CONSTRAINT #4 (never fabricate a metric) standard `main`'s
`validation/metrics.py` already meets, and closing the
`probability_of_backtest_overfitting()` gap that fix newly exposes.

Also carried over (independently correct, not redundant): 5 of #786's 6 new
module docstrings; the 6th (`ml/transformer_vol_forecaster.py`) is corrected
— its claimed "quantile uncertainty estimation" doesn't exist anywhere in
that file.

**Explicitly dropped, not carried forward:** `conftest.py`, `settings.py`
(`NO_VENV_REEXEC`), `.env.example`, `db_config.py`, `scripts/preflight_check.py`,
`universe_engine.py`, `tests/test_preflight.py` — all fully superseded by #785
or actively regressive.

## Changes made

1. `validation/metrics.py::probability_of_backtest_overfitting()` — added a
   NaN guard for the individual winning-trial OOS cell (excludes the path
   from `measurable_paths` rather than silently miscounting it as
   "not overfit").
2. `validation/autonomous_backtest_runner.py::run_cpcv()` — removed the
   `-999.0` IS-Sharpe sentinel (matching the already-correct OOS fix) and
   replaced the fabricated-`0.0` `mean_is_sharpes` substitution with a
   NaN-aware per-strategy mean + NaN-filtered variance.
3. Reapplied 5 accurate module docstrings; corrected the 6th
   (`ml/transformer_vol_forecaster.py`) to drop the false quantile claim.
4. Added tests: `tests/test_pbo.py` (2 new cases), `tests/test_autonomous_backtest_runner.py`
   (1 new case) — see task tracker for status.
5. Updated `docs/architecture/ml-and-reports.md` and
   `docs/architecture/validation-and-signals.md`.

## Verification

```bash
pytest tests/test_pbo.py tests/test_autonomous_backtest_runner.py tests/test_dsr.py -v
pytest tests/test_metrics_cpcv_oos_aggregates.py tests/test_harness_oos_gate.py tests/test_metrics_sharpe_ratio.py tests/test_institutional_metrics.py tests/test_multiple_testing.py -q
python3 -m ruff check . --select=F821,F822,F823,E9
python3 -m pytest -m "not network and not slow" -q -n auto --dist loadgroup   # full offline CI gate
```

No settings/census regeneration needed (this PR does not touch `settings.py`).
No `STRATEGY_REGISTRY`/`docs/VALIDATION_STRATEGY_FIX_LOG.md` entry needed —
this fixes the shared PBO/DSR *measurement* honesty, not a specific
strategy's deployability lever.
