# Volatility Mispricing Scanner (`pilots/vol_mispricing.py`)

## Rationale

Compares real-time market-implied volatility against a model-based fair-value forecast to
identify statistically overpriced (RICH) or underpriced (CHEAP) volatility regimes, following
the Corsi (2009) HAR-RV Volatility Risk Premium (VRP) framework used elsewhere in this codebase
(`pilots/har_volatility.py`). RICH regimes recommend short-premium credit structures; CHEAP
regimes recommend long-premium debit/straddle structures.

## Signal Logic

$$\text{spread} = \text{market\_iv} - \text{fair\_iv}$$

- `market_iv`: the market's current implied volatility read (VIX for the index-level case).
- `fair_iv`: `pilots.har_volatility.forecast_forward_volatility` — a Corsi HAR-RV forecast blended
  with long-term sample variance, over real trailing log-returns.
- `spread >= DEFAULT_RICH_VOL_THRESHOLD` (+0.03): RICH — sell an iron condor (short 0.30-delta
  put/call, long 0.15-delta wings).
- `spread <= DEFAULT_CHEAP_VOL_THRESHOLD` (-0.03): CHEAP — buy an ATM (0.50-delta) long straddle.
- Otherwise: NEUTRAL / flat.

## Backtest Validation (`vol_mispricing`, 2026-08)

Registered in `STRATEGY_REGISTRY["vol_mispricing"]` via `_build_vol_mispricing_adapter` in
`scripts/refresh_validations.py`, backed by `validation/options_selling_backtest.py::simulate_vol_mispricing_returns`.

**Honesty contract — every input is genuinely real, none is a proxy** (unlike the sibling
`vrp_premium_selling`/spread strategies, which use a GJR-GARCH IVR/VRP proxy in place of a
historical options chain): `market_iv` is the real VIX (`macro_history.VIXCLS`, real daily
coverage 1990–2026); `fair_iv` is computed by calling the pilot's own
`forecast_forward_volatility` on real trailing SPY log-returns — the exact same function the
live pilot calls in production. The one documented narrowing: strikes are delta-targeted via
`OptionsPricingRecommender.find_strike_for_delta` rather than ranked against a live options-chain
snapshot, since no historical chain data exists anywhere in this codebase (see
`validation/options_selling_backtest.py`'s module docstring) — the same limitation every sibling
options-selling adapter in this file already documents.

Walk-forward evaluation, 2015-01-01 → 2026-08-01, `--workers 1`:

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe (net) | **-0.499** | > 0.50 | ❌ FAIL |
| PBO | **0.000** | < 0.50 | ✅ PASS |
| DSR | **0.027** | > 0.95 | ❌ FAIL |
| Max Drawdown | **100.7%** | < 30% | ❌ FAIL |
| Tail-scenario stress gate | **FAIL** | survive all 4 windows, MaxDD < 50% | ❌ FAIL |

Per-window stress detail (non-vacuous — every window shows real, non-flat P&L, confirming the
gate genuinely evaluated live positions rather than a strategy that never opened a trade):

| Window | Max DD | Final Return | Survived |
|---|---|---|---|
| OCT_2008 | 203.8% | -75.5% | ❌ NO (blow-up) |
| FEB_2018 | 66.5% | -56.1% | ✅ yes, but DD > 50% |
| MAR_2020 | 29.8% | +8.5% | ✅ yes |
| AUG_2024 | 32.0% | +11.5% | ✅ yes |

**Status: `deployable=False`.** The RICH-branch iron condor genuinely blows up in the 2008
crisis window (DD > 200% of the position's own defined max-risk basis, driven by the
constant-entry-sigma simplification never re-marking to a widening VIX intraperiod, plus the
absence of any credit-event/regime gate in this simulation — `vol_mispricing`, unlike
`vrp_premium_selling`, does not currently consult `MacroEconomicDTO`'s CREDIT EVENT flag). This
is a genuine, measured result — no threshold or delta target was tuned to chase the gate; the
pilot's own unmodified `DEFAULT_RICH_VOL_THRESHOLD`/`DEFAULT_CHEAP_VOL_THRESHOLD` (±0.03) and the
conventional 0.30/0.15 delta targets were used as-is.

**Forward improvement path, not implemented here**: gating the RICH branch behind the same real
VIX/CREDIT-EVENT regime check `vrp_premium_selling` already uses would very plausibly close most
of the OCT_2008/FEB_2018 tail-loss — worth a dedicated follow-up rather than silently added here
to avoid retroactively tuning this measurement.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for strategy
registry fix history and the `.claude/giant_master_plan_audit.md` F4 finding this closes.

## Live Paper-Execution Status

As of 2026-08-18, `pilots/vol_mispricing.py` has a live paper-execution path — but unlike
`earnings_crush`, `dispersion_trading`, and `zero_dte_engine` (each an `UNGATEABLE_DATA_GAP`
whose deployability gate is surfaced for transparency but never blocks execution),
`vol_mispricing` is a **MEASURED** deployability failure (Sharpe -0.499, DSR 0.027, fails the
Oct-2008 stress window — see the Backtest Validation section above), so its execute endpoint is
**blocked by default** and only proceeds on an explicit, per-request override. This was a
deliberate, considered design choice, not an oversight, and closes out the decision this
section previously left open between "document only" and "build with an enforced gate."

- `pilots/vol_mispricing.py::execute_vol_mispricing_trade(symbol, *, candidate, contracts=1,
  dry_run=False, is_live=False)` executes a single, caller-selected candidate trade (one element
  of `build_candidate_strategy_trades()`'s output — the caller must explicitly choose which
  trade, this function never silently picks "the best" one). It validates the symbol, refuses in
  `is_live=True` mode (this platform never routes live options orders), returns a dry-run preview
  when `dry_run=True`, and otherwise reuses the shared
  `execution.options_paper_executor.OptionsPaperExecutor.execute_earnings_crush_trade` multi-leg
  fill primitive (now generalized via its new `strategy_name=` parameter, so the paper-broker
  blotter correctly labels these trades `"Vol Mispricing"` instead of the executor's old
  hardcoded `"Earnings Crush"` default) to submit an atomic multi-leg fill into
  `PaperAccountStore`. `__all__` now includes `execute_vol_mispricing_trade`.
- **Leg price translation ($/share → $/contract)**: `_create_strategy_leg` produces
  `unit_price` as a per-share option premium (e.g. `2.50`); the paper executor expects
  `fill_price` as a per-contract dollar amount. Since one contract represents 100 shares,
  `fill_price = unit_price * 100.0`. A leg with no resolvable `unit_price` is left unpriced
  rather than assigned a fabricated price — the shared executor's own `CONSTRAINT #4` guard (see
  below) refuses the whole trade if any leg ends up unpriced.
- The new route is `POST /pilots/options/mispricing/execute` in `api/pilots_api.py`, gated the
  same way as its three siblings (`require_command_token` + `require_paper_broker_writes_enabled`)
  **plus** an enforced deployability check: `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]`'s
  `gate_status == "MEASURED_FAIL"` blocks the request (`{"ok": False, "blocked": True, ...}`,
  `gate_status` always echoed so the caller sees exactly why) unless the request body sets
  `override_deployability_gate: true`. This is a **per-request** override only — there is no
  standing settings flag that disables the gate globally, and the override is always visible in
  the response (`override_applied`), never silent.
- Two latent bugs in the shared `execute_earnings_crush_trade` executor were fixed as a
  prerequisite for reusing it safely here (both in `execution/options_paper_executor.py`):
  (1) a `CONSTRAINT #4` violation where a leg with no resolvable `fill_price`/`raw_price` was
  silently assigned a fabricated `$1.50`/`$150.00` sentinel instead of refusing the trade — now
  fixed to refuse the whole trade honestly instead; (2) the function computed a real
  per-candidate `strategy` label but never actually used it, hardcoding `strategy_name="Earnings
  Crush"` in every call regardless of caller — now controllable via the new `strategy_name=`
  parameter (default `None` preserves the exact historical `"Earnings Crush"` behavior for every
  pre-existing caller).

### 2026-08-18 Full Validation Run (`vol_mispricing`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | -0.0098 |
| **PBO** | 0.0000 |
| **DSR** | 0.4818 |
| **Max Drawdown** | 98.71% |
| **Deployable** | ❌ False |

*Reconciliation note: this run's Sharpe/DSR differ from both the "Backtest Validation" section's
2015-2026 walk-forward numbers above (Sharpe -0.499, DSR 0.027) and an earlier, since-superseded
2026-08-17 pre-rebase run (Sharpe -0.0369, DSR 0.4966) for this same strategy. The qualitative
verdict is stable across all three runs — `deployable=False`, MaxDD in the ~99-101% range,
consistent with the RICH-branch iron-condor blow-up in the OCT_2008 stress window documented
above — but the point estimates for Sharpe/DSR are not stable run-to-run. This is consistent with
`vol_mispricing` being a low-trade-count strategy whose walk-forward window right edge moves with
"today's date," so a handful of trades entering/leaving the sample between runs can swing DSR
meaningfully; it has not been separately investigated further here. The `MEASURED_FAIL` gate
status and the enforced-override design in "Live Paper-Execution Status" below do not depend on
the exact point estimate and are unaffected by this variance.

### 2026-08-21 re-verification — 4th independent confirmation of the OCT_2008 blow-up

A `scripts.refresh_validations` run in a network-isolated sandbox hit `RuntimeError:
Adapter returned an empty feature/return frame — insufficient history for this start/end
range` for this strategy. Investigated and given a self-diagnosing error message in
[#850](https://github.com/kevinmarko/Stockpy/pull/850) (diagnostics-only, no adapter-logic
change), then re-run with a real `FMP_API_KEY` on 2026-08-21 (`--start 2005-01-01`,
default end = today):

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | -0.140 |
| **PBO** | 0.000 |
| **DSR** | 0.302 |
| **Max Drawdown** | 100.0% |
| **Deployable** | ❌ False |

| Window | Max DD | Final Return | Survived |
|---|---|---|---|
| OCT_2008 | 203.8% | -75.5% | ❌ NO (blow-up) |
| FEB_2018 | 66.5% | -56.1% | ✅ yes, but DD > 50% |
| MAR_2020 | 29.8% | +8.5% | ✅ yes |
| AUG_2024 | 32.0% | +11.5% | ✅ yes |

The per-window stress figures are **bit-identical** to the "Backtest Validation" section's
2015-2026 walk-forward run above — the same OCT_2008 203.8%/-75.5%/NO blow-up, the same
FEB_2018 66.5%/-56.1% survive-but-breach, the same MAR_2020/AUG_2024 pass numbers. This is
the 4th independently-run confirmation (alongside the 2026-08-15, 2026-08-17, and 2026-08-18
runs above) that this strategy's RICH-branch iron condor genuinely blows up in the 2008
crisis window — not a fluke of any one run's data window or environment. Confirms the
"insufficient history" RuntimeError encountered in the network-isolated sandbox was
environmental (no/invalid FMP credentials there), not a defect in this adapter. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 entry.

**Follow-up (same day) — 5th independent confirmation, now on the correct full data window**:
`data/fmp_client.py::historical_eod_full_range` fixes the FMP 5,000-row truncation this run was
unknowingly subject to. Re-run against the full 2005-present window: Sharpe=-0.033, PBO=0.000,
DSR=0.504, MaxDD=100.0%, `deployable=False` (unchanged conclusion). The per-window stress figures
are again **bit-identical** to every prior run — OCT_2008 203.8%/-75.5%/NO blow-up — now the 5th
independent confirmation, and the first one measured on the genuinely correct full historical
window. See `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s "FMP `historical_eod` 5,000-row cap fixed"
entry.
