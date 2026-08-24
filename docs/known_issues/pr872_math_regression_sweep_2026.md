# PR 872 Math-Regression Sweep (Agent 6a)

**Status**: Clean sweep — no quant-math regression found in scope
**Date**: 2026-08-24
**Incident Level**: N/A (audit pass, no incident)

## Scope

Agent 6a of a 6-agent remediation of PR 872. This document covers the specific,
reproducible sweep specified in that task:

```
git diff origin/main...HEAD -- '*.py' | grep -E "^[+-]" | grep -vE "^(\+\+\+|---)" \
  | grep -iE "1e-12|1e-6|1e-10|nan|isnan|fillna|sharpe|sortino|calmar|drawdown|dsr|pbo|kelly|252|psd|eigen|cvar|std\(|deployable"
```

Run from worktree `pr872-agent6a` (branch `pr872-agent6a-hygiene`, based on commit
`9b75ec13` "Agent 1: unblock CI, strip PR 872 scope creep"), against
`origin/main` at commit `e879b450`. `HEAD` at the time of this sweep was
`9b75ec13` itself (no commits of this agent's own work were included in the
diff being swept — the two test-file fixes described below were applied to the
working tree afterward and are not part of what this sweep audited).

18 matching added/removed lines were found, landing in 6 files. Every file is
addressed below. **Two files were pre-cleared by a prior audit and were not
re-derived here, per the task's own instruction**: `sizing/hrp_cvar_optimizer.py`
and `conftest.py`. Both were spot-checked anyway (cheap to do) and confirmed —
in both cases the diff against `origin/main` is type-annotation-only churn
(`def objective(w):` → `def objective(w: np.ndarray) -> float:` and similar);
no numeric literal, default, comparison operator, or fixture scope/isolation
changed.

## File-by-file findings

### `tests/test_validation_forecast_direction.py` — regression found and fixed

The PR had replaced the test's call to the production `_download_closes`
helper (`scripts/refresh_validations.py`, FMP-backed, throttled, dead-letter
per-ticker) with a raw, unthrottled `yfinance.download()` call plus manual
`Close` column reshaping. This directly undid the fix documented in CLAUDE.md
under "unthrottled single-shot `yfinance` bulk fetch replaced with per-ticker
FMP fetches" — the exact failure mode that incident describes (Yahoo silently
failing the majority of a multi-ticker batch, contaminating downstream
results) is reachable again through this one test's own data-loading path,
and the test would then no longer be exercising the same code path production
validation runs actually use.

**Fix**: restored the pre-PR version verbatim — `from scripts.refresh_validations
import _download_closes, _make_strategy_fn` and `closes = _download_closes(tickers,
"2023-01-01", "2023-12-31")`, replacing the `yfinance`/manual-reshape block.
Confirmed via `diff` that the restored file is now byte-identical to
`origin/main:tests/test_validation_forecast_direction.py`. `_download_closes`
itself is untouched by this PR (confirmed by reading
`scripts/refresh_validations.py:3857-3877` — still FMP-backed, still routes
through `_fetch_fmp_ohlcv_batch`). File re-parses cleanly
(`ast.parse`); this test is `pytest.mark.network`-marked and was not executed
(no live network intent for this specific test in this pass — see Task 2's
test, which was network-marked too but did run, below).

### `tests/test_validation_lgbm_ranker_registry.py` — regression found and fixed

`test_lgbm_ranker_validation_harness_runs`'s docstring had been shortened from
a version that explained *why* the test doesn't assert deployability/profitability
(a 10-name, reduced-split configuration isn't expected to clear the Sharpe/DSR
bar, and that's not what the test checks — the real registry numbers live in
`docs/signals/lgbm_ranker.md`) down to a generic "does not assert
profitability" one-liner. This is a CONSTRAINT-#4-adjacent documentation
regression: losing the explicit pointer to where the real, trustworthy numbers
live makes it easier for a future reader to mistake this smoke test's
arbitrary-data report for a real deployability signal.

**Fix**: restored the original docstring verbatim. The `monkeypatch` fixture
and the `monkeypatch.setattr(scripts.refresh_validations, "_XSEC_UNIVERSE_CAPPED",
TICKERS)` line this PR had also added to all three test functions in this file
were confirmed via `git show origin/main:scripts/refresh_validations.py` vs.
this branch's copy to be **byte-identical** (`_XSEC_UNIVERSE_CAPPED` and the
rest of that module are untouched by this PR) — i.e. the monkeypatch is not
compensating for any behavior change this PR made, it is a harmless test-universe
pin, and per the task instructions it was left in place. Checked the other two
functions in the file (`test_lgbm_ranker_adapter_returns_callable_strategy_fn`,
`test_lgbm_ranker_strategy_fn_produces_real_trades`) against
`origin/main:tests/test_validation_lgbm_ranker_registry.py`: neither lost any
docstring content — the first had no docstring in either version, and the
second's existing docstring ("A single fold call must genuinely train a
ranker...") is unchanged. After the fix, `diff` against `origin/main` shows
**only** the three `monkeypatch` additions as the remaining delta — confirmed
this is the full, exact set of legitimate changes.

Ran `python3 -m pytest tests/test_validation_lgbm_ranker_registry.py -q` for
real (this file carries a module-level `pytestmark = pytest.mark.network`, so
strictly it *is* network-marked, contrary to what this task's own instructions
assumed — `pytest.ini`'s `addopts` does not auto-deselect `-m network` by
default, deselection is opt-in via `-m "not network"` at invocation time, so
running the file directly with no `-m` flag still attempts live yfinance
calls). Real network access was available in this sandbox (consistent with
this operator's own memory note that yfinance works here despite no
`FMP_API_KEY`). Result: **3 passed in 140.53s** (0:02:20) — real output, not
assumed.

### `sizing/hrp_cvar_optimizer.py` — pre-cleared, re-confirmed clean

Every hit is a `def f(w):` → `def f(w: np.ndarray) -> float:` (or
`-> np.ndarray` / `-> Callable[[np.ndarray], float]`) style annotation add.
No default value, comparison, or numeric literal changed anywhere in the
diff. Matches the prior audit's conclusion.

### `conftest.py` — pre-cleared, re-confirmed clean

Every hit is the same class of annotation-only change
(`def _field_default(model_cls, name):` →
`def _field_default(model_cls: type, name: str) -> Any:`, and
`monkeypatch` → `monkeypatch: pytest.MonkeyPatch` on the autouse fixtures).
No fixture body, scope, or isolation logic changed. Matches the prior audit's
conclusion.

### `ml/training_data.py` — reviewed, not a regression (legitimate feature swap)

This file's hits come from a real, substantive change: the 30-day paper-trade
outcome features (`paper_hit_rate_30d`, `paper_avg_realized_pnl_30d`) used to
be derived by running `apply_triple_barrier` (a synthetic labeling proxy)
against price history for each recorded paper order, with a carefully-reasoned
PIT guard distinguishing a genuine barrier timeout from an under-observed one
(the removed comment block/logic starting "We can only learn from outcomes
resolved STRICTLY BEFORE as_of_naive..."). This PR replaces that proxy with
the platform's own real recorded closed-trade outcomes
(`PaperAccountStore`'s new `paper_closed_trades` table — `realized_pnl`,
`realized_pnl_pct`, `exit_ts`), gated identically for PIT-safety
(`exit_ts < as_of_naive`).

This is a genuine improvement, not a weakened guard: the new gate is simpler
*because* it's now filtering on a trade's own real, already-realized exit
timestamp rather than reconstructing a synthetic outcome from price history —
there is no "under-observed vertical timeout" failure mode to guard against
anymore, because the mechanism that produced that specific failure mode
(triple-barrier applied to a truncated price series) is no longer in this
code path.

Verified this doesn't quietly weaken the platform's actual triple-barrier
lookahead protection: `ml/triple_barrier.py` and `apply_triple_barrier` are
still used elsewhere (`ml/meta_labeling.py`, `scripts/train_meta_labelers.py`),
and both `tests/test_triple_barrier_lookahead.py` and `ml/triple_barrier.py`
itself show **zero diff** against `origin/main` — the core lookahead-bias
protection for the triple-barrier method itself is completely untouched; only
this one specific paper-feature use site was swapped to a different, more
direct data source.

`tests/test_training_data_paper_features.py` lost
`test_pit_ticker_row_vertical_timeout_requires_full_window_elapsed` (the
regression test for the triple-barrier vertical-timeout PIT bug) as a direct
consequence — correctly, since the code path it was testing no longer exists.
New `data/paper_account_store.py` realized-PnL math
(`_record_closed_trade`, ~321 lines added, no regex hits) was independently
spot-checked and found to guard every division
(`... if pos.avg_entry_price > 0 else 0.0`, `... if qty > 0 else 0.0`, etc.) —
no unguarded division found in the added code.

### `settings.py` — reviewed, not a regression (new opt-in flag, safe default)

Single hit is the addition of `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`
(default `False`), with a description that explicitly reasons through why it
defaults off (the shared `trades` ledger has no paper/live discriminator
column and feeds real-performance-reporting consumers, so bridging simulated
fills into it by default would be a silent data-integrity change). This
follows the codebase's established "new behavior-changing flag defaults to
today's exact behavior" convention correctly.

## Additional observation outside the regex sweep (not fixed — out of this agent's file scope)

While reviewing the surrounding diff for context (not part of the regex
sweep's hit list, and not a hit itself), a latent bug was noticed in
`pilots/vol_mispricing.py::get_volatility_mispricing_data`. This PR correctly
removes a CONSTRAINT #4 violation — a hardcoded fabricated spot-price fallback
(`500.0 if sym == "SPY" else (130.0 if sym == "NVDA" else 150.0)`) — replacing
it with `spot_price = None` (documented, deliberate, and covered by
`docs/known_issues/paper_options_zero_fill_price.md`, status "Mitigated").
However, a few lines later, the function's synthetic-chain fallback branch
(entered when the real options chain fetch also fails/returns empty —
`if not raw_extracted:`) still unconditionally computes
`k = round(spot_price * m, 2)` for a set of moneyness levels. If **both** the
live quote fetch and the live chain fetch fail in the same call (a real, if
narrow, double-data-outage scenario), `spot_price` is `None` at that point and
this raises `TypeError: unsupported operand type(s) for *: 'NoneType' and
'float'` instead of returning the honest "unavailable" response the sibling
`evaluate_strike_mispricing`/`evaluate_vol_mispricing` path already knows how
to produce (it has its own `spot_price is None` guard, but only reached
*after* this synthetic-chain block, so it never gets the chance to run in this
scenario). The one caller, `GET /pilots/options/forecast/mispricing`
(`api/pilots_api.py`), has no surrounding try/except, so this would surface as
an unhandled 500 rather than a graceful degrade.

This is not a "weakened guard" regression in the sense the sweep was looking
for — it's a residual gap in an otherwise-correct fabrication fix, in a file
outside this agent's permitted edit scope
(`tests/test_validation_forecast_direction.py`,
`tests/test_validation_lgbm_ranker_registry.py`, and this doc only). Flagged
here for whichever agent/session owns `pilots/vol_mispricing.py` or the
options-desk parity sweep to pick up; **not fixed as part of this pass**.

## Bottom line

No quant-math regression (weakened threshold, fallback, or guard that used to
be honest and isn't anymore) was found beyond the two documentation/test-data
regressions already described and fixed above (Tasks 1 and 2). Every other
regex hit traces to either pre-cleared annotation-only churn or a genuine,
correctly-guarded feature addition. One unrelated, pre-existing gap was
noticed and is disclosed above without being fixed, since it falls outside
this agent's assigned file scope.
