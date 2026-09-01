# 0DTE Intraday Momentum & TTM Squeeze Breakout (`pilots/zero_dte_engine.py`)

## Rationale

Detects TTM Squeeze compression (Bollinger Bands inside Keltner Channels) on SPY/QQQ intraday
bars and trades same-day-expiring options on the breakout, with a mandatory 15:45 ET hard-stop
liquidation to eliminate overnight/pin risk.

## Backtest Validation — NOT GATEABLE (structural, not merely data-limited)

**Explicitly registered in `STRATEGY_REGISTRY` as `UNGATEABLE_DATA_GAP`**. Three independent, compounding blockers, the third of
which is decisive on its own:

1. **No intraday history exists in this repository.** The entry signal is a 15-minute opening
   range on 1-minute bars; `data/historical_store.py::HistoricalStore` exposes only
   `get_bars`/`get_bars_bulk` (daily), no intraday method.
2. **No point-in-time 0DTE options chain.** Contract selection needs per-strike deltas and
   bid/ask at the moment of entry — the same "no historical chain data anywhere in this
   codebase" gap documented in `earnings_crush.md`/`dispersion_trading.md`.
3. **Decisive: the mandatory tail-scenario stress gate can never be run for this pilot with any
   reachable data.** The four dated shock windows this platform's options-selling gate requires
   (OCT_2008, FEB_2018, MAR_2020, AUG_2024) are all more than a decade in the past; yfinance's
   1-minute bar retention is roughly the trailing 30 days. There is no source — free or
   otherwise — this sandbox can reach that provides 1-minute SPY/QQQ bars for any of the four
   required windows. A strategy this codebase's own convention gates on stress-window survival
   cannot honestly be marked `stress_gate_passed` when the gate literally cannot execute.

Worth noting separately: 0DTE momentum *buys* premium (long options on breakout), so it is
long-gamma, not an options-*selling* book in the strict sense the tail-stress addendum was
designed for — but its acute same-day pin/gap risk is exactly the profile that addendum exists
to catch, so exempting it on that technicality rather than the data gap above would be the wrong
kind of exemption.

## Defects found while analysing this pilot

Recorded so they are not lost; `pilots/*.py` is out of this task's ownership. These substantiate
and extend `.claude/giant_master_plan_audit.md`'s finding F5 (the 15:45 ET liquidation gate being
orphaned from any live path):

1. **FIXED — `get_0dte_signals` was a dead path.** It used to call
   `store.get_intraday_bars(...)` behind an `hasattr` guard, but `HistoricalStore` exposes no
   such method — the guard always failed closed and `bars` was always `None`, a dead pretense of
   a lookup rather than a real one. Fixed by deleting the try/except/hasattr block entirely and
   calling `scan_0dte_breakouts` with `intraday_bars=None` explicitly, with an inline comment
   explaining why (no intraday/1-minute bar source exists anywhere in this repo — see the
   "Not gateable" reasoning above). `scan_0dte_breakouts`'s existing honest `intraday_bars is
   None` branch already degrades correctly (`opening_range.valid=False`, `signal_type=
   "NO_SIGNAL"`, an explanatory `reason`) rather than fabricating a synthetic range from daily
   bars, so this was a dead-code cleanup, not a new behavior — `get_0dte_signals` still returns
   `is_actionable=False` in every case, now for the honestly-documented reason instead of a
   silently-always-false guard. Regression-tested by
   `tests/test_zero_dte_engine.py::test_get_0dte_signals_no_intraday_source_degrades_honestly`
   (asserts the honest NO_SIGNAL/non-actionable degrade) and
   `::test_get_0dte_signals_source_has_no_dead_historical_store_lookup` (an `inspect.getsource`
   + `hasattr` guard against this exact dead pattern reappearing). Separately, still true and out
   of scope here: `get_0dte_signals` has no `chain_data` parameter at all, so even with a real
   intraday-bar source wired in it could not select a contract — that's the same "no
   point-in-time 0DTE options chain" gap already documented above, not a new finding.
2. **FIXED — `execute_0dte_trade` no longer fabricates a fill price.** It previously fell back to
   a hardcoded `unit_price = 1.50` when no live quote was supplied. The current implementation
   has no such literal: when `quote_price`/`limit_price` are absent it attempts a real
   Black-Scholes theoretical price off a live spot (`pilots.price_provider.get_latest_price` +
   `pilots.options_risk.calculate_black_scholes_greeks`), and if no live spot is available either,
   it refuses outright — returning `{"ok": False, "error": "No quote_price or limit_price
   provided. Real price source required to execute 0DTE trade."}` — instead of writing a
   fabricated-price trade into `PaperAccountStore` (CONSTRAINT #4).
3. **The module's own docstring overstates the implementation.** The documented "TTM Volatility
   Squeeze **Gate**" gates nothing — the squeeze condition only contributes +0.10 to a confidence
   score. The documented "opening range reversal" stop does not exist in code. (Still open.)

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) and
`.claude/giant_master_plan_audit.md`'s findings F4 and F5.

`POST /pilots/options/zero-dte/execute`'s response body now includes a `gate_status` field
(sourced from `OPTIONS_DESK_DEPLOYABILITY_GATES["zero_dte_engine"]` in `api/pilots_api.py`) —
`"UNGATEABLE_DATA_GAP"` — echoing this doc's `deployable=False` verdict inline on every execution
attempt, so an operator hitting the live endpoint sees the same honest gate status documented
here without cross-referencing this file.
