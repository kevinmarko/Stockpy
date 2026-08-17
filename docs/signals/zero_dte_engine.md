# 0DTE Intraday Momentum & TTM Squeeze Breakout (`pilots/zero_dte_engine.py`)

## Rationale

Detects TTM Squeeze compression (Bollinger Bands inside Keltner Channels) on SPY/QQQ intraday
bars and trades same-day-expiring options on the breakout, with a mandatory 15:45 ET hard-stop
liquidation to eliminate overnight/pin risk.

## Backtest Validation — NOT GATEABLE (structural, not merely data-limited)

**Not registered in `STRATEGY_REGISTRY`.** Three independent, compounding blockers, the third of
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

## Defects found while analysing this pilot (out of scope here — not fixed)

Recorded so they are not lost; `pilots/*.py` is out of this task's ownership. These substantiate
and extend `.claude/giant_master_plan_audit.md`'s finding F5 (the 15:45 ET liquidation gate being
orphaned from any live path):

1. **`get_0dte_signals` is a dead path.** It calls `store.get_intraday_bars(...)` behind a
   `hasattr` guard, but `HistoricalStore` exposes no such method — the guard always fails closed,
   and `chain_data` is never forwarded to it either way. The function can therefore only ever
   return `is_actionable=False`; the live 0DTE signal-generation path is inert regardless of
   market conditions.
2. **`execute_0dte_trade` fabricates a fill price.** When no live quote is supplied it falls back
   to a hardcoded `unit_price = 1.50` — a fabricated price on a path that writes real trades into
   `PaperAccountStore` (a CONSTRAINT #4 concern).
3. **The module's own docstring overstates the implementation.** The documented "TTM Volatility
   Squeeze **Gate**" gates nothing — the squeeze condition only contributes +0.10 to a confidence
   score. The documented "opening range reversal" stop does not exist in code.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) and
`.claude/giant_master_plan_audit.md`'s findings F4 and F5.
