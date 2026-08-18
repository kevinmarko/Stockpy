# Walkthrough: Options Desk Deployability Gate — Runtime Wiring Follow-Up (v2)

## What this session did

PR #790 (`89308aa9`) had already wired the `OPTIONS_DESK_DEPLOYABILITY_GATES` `gate_status` field
onto the options desk pilots' execute endpoints and closed audit findings F5/F11/F15/F16. Its own
follow-on log entry (`docs/VALIDATION_STRATEGY_FIX_LOG.md`, 2026-08-17) was explicit that five
items were left out of scope. This session closes all five, in two commits:

- `e13b1a2c` — "fix: close remaining options-desk deployability runtime gaps"
- `1cebcc79` — "docs: options desk deployability gate runtime wiring follow-up"

(A merge of `origin/main`, `7989e130`, sits between these two and pulled in PR #791's unrelated
Gaussian HMM regime-model work plus a routine regeneration of `docs/settings_field_census.*` /
`docs/settings_liveness.json` to resolve merge conflicts — no new logic was authored in that
merge itself.)

### 1. `get_0dte_signals`'s dead lookup, removed

`pilots/zero_dte_engine.py::get_0dte_signals` used to guard an intraday-bar lookup with
`hasattr(store, "get_intraday_bars")` — but `HistoricalStore` (`data/historical_store.py`) has
no such method; it's daily-OHLCV-only (`get_bars`/`get_bars_bulk`). The guard therefore always
evaluated `False`, `bars` stayed `None` regardless of what actually happened, and the whole
`try/except` block was dead code dressed up as a real data-source attempt. It's replaced with an
explicit `intraday_bars=None` and an inline comment stating the real reason: no intraday/
1-minute bar source exists anywhere in this repo, and the four mandatory 0DTE stress windows
fall outside yfinance's ~30-day 1-minute retention anyway, so this is a structural gap rather
than a bug with a real fix available today. `scan_0dte_breakouts` already degrades honestly on
`None` (marks the opening range invalid, returns `signal_type="NO_SIGNAL"` with an explanatory
`reason`) — this change stops pretending a real lookup was attempted, nothing more.

### 2. `vol_mispricing`'s gate entry, documented as inert

`OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` in `api/pilots_api.py` has no runtime
consumer — `pilots/vol_mispricing.py` exposes no `execute_*` function and no
`PaperAccountStore` import; its only API surface is the read-only
`GET /pilots/options/forecast/mispricing`. This was true before this session but undocumented.
An inline comment now states it plainly, and `docs/signals/vol_mispricing.md` gained a "Live
Paper-Execution Status" section saying the same thing for a doc reader who never opens the code.

### 3. `vrp_premium_selling.md`'s duplicate/stale section, corrected

The file had two `## Backtest Validation` headings: an old one claiming Sharpe 0.612 / DSR 1.000
/ `deployable=True`, and a newer, correct one recording the platform's actual measured
2026-08-15 numbers (Sharpe 0.217, PBO 0.000, DSR 0.000, MaxDD 17.9%, `deployable=False`). The
stale section is removed; the file now has exactly one `## Backtest Validation` heading, matching
`docs/VALIDATION_STRATEGY_FIX_LOG.md`.

### 4. Two dropped tests, restored, plus new direction-sign coverage

`tests/test_options_desk_deployability_runtime_gap.py`'s introducing commit (`f3f63003`)
included two tests that a later commit (`89308aa9`) silently dropped when it overwrote the file
with a narrower 4-test version:

- **T1** — `execute_0dte_trade` refuses rather than fabricating a `1.50` placeholder fill price
  when no real quote/spot is resolvable.
- **T2** — the SPY and QQQ dispersion weight maps are genuinely distinct dicts, not one
  copy-pasted into the other.

Both are restored. New coverage (**T3**, in `tests/test_dispersion_trading.py`) closes a real
gap that existed even in the original test file: two new tests monkeypatch
`_source_real_dispersion_inputs` to supply a correlation spread strongly past the ±0.15 threshold
in each direction, then assert `execute_dispersion_trade(basket=None)`'s resulting
`is_long_dispersion` flag and per-leg `side` values (`"buy"`/`"sell"`) track the measured
spread's actual sign rather than a hardcoded default — proving the "real-data-sourcing path"
claim is genuinely exercised in both directions, not just smoke-tested.

### 5. Doc-drift correction: `dispersion_trading`'s basket defect

The 2026-08-17 log entry's "Defects found" list is corrected here, not silently left as-is:
`pilots/dispersion_trading.py`'s SPY/QQQ constituent overlap is only half-fixed. The **weight
maps** are now genuinely distinct per T2 above, but `INDEX_CONSTITUENTS_MAP`'s underlying
constituent lists still overlap — `docs/signals/dispersion_trading.md`'s "Defects found" section
now states this explicitly instead of letting the weight-map fix read as a full resolution.

### 6. `CLAUDE.md` / `AGENTS.md` accuracy pass

The existing F1-F16 remediation bullet claimed all four options-desk pilot modules
(`earnings_crush`, `dispersion_trading`, `zero_dte_engine`, `vol_mispricing`) "consistently
surface and enforce" `OPTIONS_DESK_DEPLOYABILITY_GATES`. That was inaccurate for
`vol_mispricing`, which has no execute path at all. The bullet is corrected to name the three
modules that actually get the live `gate_status` wiring, and to state `vol_mispricing`'s entry
is informational-only, pointing to the new `docs/VALIDATION_STRATEGY_FIX_LOG.md` entry and
`docs/signals/vol_mispricing.md` for detail rather than re-deriving it inline. Both files stay
mirrored per the repo's `sync_agent_docs.sh` convention.

### 7. `docs/VALIDATION_STRATEGY_FIX_LOG.md` entry

A new 2026-08-18 "Runtime Wiring Follow-Up & Doc-Drift Correction" entry itemizes all five closed
gaps above, each with concrete verification evidence (file/line references, `grep` confirmations
of single-match headings, git-log comparisons proving which commit dropped which test) rather
than re-asserting the prior entry's claims unchecked.

## Verification results

Ran the full targeted suite for every file touched by this session's code changes:

```
pytest tests/test_options_desk_deployability_runtime_gap.py tests/test_zero_dte_engine.py \
       tests/test_dispersion_trading.py tests/test_pilots_api.py -q
```

Result: **436 passed, 0 failed** (137-145 warnings, all pre-existing deprecation/pytest-mark
warnings unrelated to this change).

Per-file breakdown (run individually to confirm no cross-file masking):

| File | Result |
|---|---|
| `tests/test_options_desk_deployability_runtime_gap.py` | 6 passed |
| `tests/test_zero_dte_engine.py` | 24 passed |
| `tests/test_dispersion_trading.py` | 14 passed |
| `tests/test_pilots_api.py` | 392 passed |

No `webapp/src/` changes were made in this session, so `npm run --prefix webapp typecheck` and
the browser-check step do not apply — this session's work is Python + docs only.

## Files touched (this session's two authored commits, excluding the origin/main merge)

**Code:**
- `pilots/zero_dte_engine.py`
- `api/pilots_api.py`

**Tests:**
- `tests/test_zero_dte_engine.py`
- `tests/test_options_desk_deployability_runtime_gap.py`
- `tests/test_dispersion_trading.py`
- `tests/test_pilots_api.py`

**Docs:**
- `docs/signals/vrp_premium_selling.md`
- `docs/signals/vol_mispricing.md`
- `docs/signals/zero_dte_engine.md`
- `docs/signals/dispersion_trading.md`
- `docs/signals/earnings_crush.md`
- `docs/VALIDATION_STRATEGY_FIX_LOG.md`
- `CLAUDE.md`
- `AGENTS.md`

## What a reviewer should focus on

1. Does the `intraday_bars=None` inline comment in `pilots/zero_dte_engine.py` accurately
   describe the data gap (no intraday source anywhere in this repo), or should this be tracked
   as a real follow-up item to build one?
2. Is the `vol_mispricing` gate-entry-with-no-consumer pattern (keeping the dict entry as a
   documentation-only record for a hypothetical future execute endpoint) the right call, versus
   just removing the entry until an execute endpoint exists?
3. The half-fixed `dispersion_trading` constituent-overlap defect is documented, not resolved —
   confirm that's an acceptable state to merge, or flag it for a dedicated follow-up PR.
