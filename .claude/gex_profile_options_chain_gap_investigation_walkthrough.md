# GEX Profile "no options chain data" investigation — walkthrough

## What was observed

On the Pilots PWA's Paper Broker screen, the "GEX Profile" panel
(`GET /pilots/options/gex/profile` → `pilots/options_gex.py::get_options_gex_profile`)
was live-tested against the real running dev stack for symbol SPY and honestly
reported:

- Total Net Dealer GEX: `+$0.0M`
- Zero-Gamma Flip Level: `—` ("No real options chain data available to solve for a
  flip boundary")
- Major Gamma Walls (Call Wall / Put Wall): both `—`

This looked CONSTRAINT #4-compliant on its face (refusing to fabricate a number
rather than making one up), but the feature was delivering nothing for SPY.

## Root cause: a genuine code bug, not a data/entitlement gap

`get_options_gex_profile`'s live-chain resolution step:

```python
from data.market_data import get_options_provider
options_provider = get_options_provider()
expirations = options_provider.fetch_options_chain(clean_sym)
if expirations and isinstance(expirations, list):
    chain_map = {}
    for exp in expirations[:5]:
        c = options_provider.fetch_options_chain(clean_sym, exp)
        if c:
            chain_map[str(exp)] = c
    if chain_map:
        chain_data = chain_map
```

`data.market_data.CompositeOptionsProvider.fetch_options_chain(symbol, expiration)`
is backed by yfinance's `Ticker.option_chain(expiration)`, which returns an
`Options` **namedtuple** carrying two SEPARATE DataFrames — `.calls` and `.puts`
— not a flat, dict-like chain.

`chain_map` (a `dict[str, Options]`) was then passed straight through as
`chain_data` to `calculate_gex_profile(chain_data=chain_data, ...)`, which calls
`_normalize_chain_data(chain_data)`. That function only understands two input
shapes:

```python
if isinstance(chain_data, pd.DataFrame):
    ...
elif isinstance(chain_data, (list, tuple)):
    ...
else:
    return []
```

A `dict` of `{expiration: Options namedtuple}` matches neither branch, so it fell
straight to `return []` — an empty, "unparseable" chain — **even when the live
options chain fetch itself succeeded with real strikes and real open interest**.
`calculate_gex_profile` then produced its honest empty-chain degradation
(`net_gex=0.0`, `zero_gamma_flip=None`, walls `None`,
`diagnostics={"warning": "Empty or unparseable option chain data"}`), which the
webapp correctly rendered as "—" / "+$0.0M" / "No real options chain data
available" — a faithful rendering of a value that was itself wrong.

## Reproduction

Confirmed against the real running dev stack (`.venv`, live network, no mocks),
`MARKET_DATA_PROVIDER=fmp` with no `FMP_API_KEY` configured in this worktree so
the process fell back to yfinance for quotes — the exact yfinance code path this
bug lives in:

**Before the fix** — `get_options_gex_profile("SPY")`:

```json
{
  "spot_price": 766.75,
  "net_gex": 0.0,
  "zero_gamma_flip": null,
  "call_wall_strike": null,
  "put_wall_strike": null,
  "chain_source": "live",
  "diagnostics": {"warning": "Empty or unparseable option chain data"}
}
```

Note `chain_source: "live"` (not `"synthetic"`) — the live chain fetch DID
succeed; the resolver just couldn't parse what it got back. A direct call
confirmed yfinance's live SPY chain had real data:

```
expirations: ['2026-08-20', '2026-08-21', '2026-08-24', ...]
chain type: <class 'yfinance.ticker.Options'>
fields: ('calls', 'puts', 'underlying')
calls columns: ['contractSymbol', 'lastTradeDate', 'strike', ..., 'openInterest', 'impliedVolatility', ...]
```

**After the fix** — `get_options_gex_profile("SPY")` (same live network, same
process):

```json
{
  "spot_price": 766.61,
  "net_gex": -618956820454.29,
  "zero_gamma_flip": 770.38,
  "call_wall_strike": 770.0,
  "put_wall_strike": 765.0,
  "gamma_regime": "PIN_RISK_HIGH",
  "chain_source": "live",
  "diagnostics": {"contract_count": 1245, "strikes_count": 259, "has_zero_flip": true}
}
```

1245 real contracts across 259 strikes, a real zero-gamma flip, real gamma walls
— all from data that was available the whole time.

## The fix

`pilots/options_gex.py` gained `_flatten_provider_chain_entry(chain_obj,
expiration)`, which:

- Recognizes the yfinance-shaped `.calls`/`.puts` object and flattens both
  DataFrames into a single list of dict records, tagging each with
  `option_type` (`"CALL"`/`"PUT"`) and `expiration`.
- Also tolerates a bare `pd.DataFrame` or a `list`/`tuple` of dict records
  (back-filling `expiration` where missing), so a future options-chain provider
  swap (e.g. an FMP-backed one) degrades gracefully instead of silently
  re-breaking this exact path.
- Never raises (CONSTRAINT #6) — any unrecognized shape degrades to `[]`.

`get_options_gex_profile`'s chain-resolution loop now builds one flat
`list[dict]` via this helper instead of a `dict[str, Options]`, which
`_normalize_chain_data` already knows how to parse — no changes were needed to
`_normalize_chain_data` itself, keeping every other caller (tests using
DataFrame/list inputs, the synthetic-chain path) untouched.

## Why this wasn't caught earlier

`tests/test_options_gex.py` had thorough coverage of the pure math
(`calculate_gex_profile`, `_normalize_chain_data`, zero-gamma root-finding, wall
identification, regime classification) using synthetic/DataFrame/list chain
inputs — all shapes `_normalize_chain_data` already handled correctly. There was
no test exercising `get_options_gex_profile`'s own live-provider resolution path
end-to-end, so the shape mismatch between what
`CompositeOptionsProvider.fetch_options_chain` actually returns and what
`_normalize_chain_data` accepts was never exercised.

## Tests added (`tests/test_options_gex.py`)

- `test_flatten_provider_chain_entry_yfinance_shaped_namedtuple` — the core
  regression: a yfinance-shaped `.calls`/`.puts` object flattens into 6 tagged
  records instead of vanishing.
- `test_flatten_provider_chain_entry_dataframe_and_list_and_none` — the
  defensive fallback shapes (bare DataFrame, list of dicts, `None`/empty) never
  raise and degrade correctly.
- `test_get_options_gex_profile_resolves_real_gex_from_yfinance_shaped_chain` —
  end-to-end: with a mocked live quote and a mocked yfinance-shaped chain
  provider, `get_options_gex_profile` now returns a real, non-degenerate GEX
  profile (`chain_source="live"`, no `diagnostics.warning`, non-null gamma
  walls, non-zero net GEX) instead of the pre-fix empty-chain degradation.

All 20 tests in `tests/test_options_gex.py` pass, plus the repo's genuine-bug
ruff gate (`F821,F822,F823,E9`) is clean on both changed files.

## Files changed

- `pilots/options_gex.py` — added `_flatten_provider_chain_entry`; rewired
  `get_options_gex_profile`'s live-chain-resolution loop to use it.
- `tests/test_options_gex.py` — three new tests (above).
