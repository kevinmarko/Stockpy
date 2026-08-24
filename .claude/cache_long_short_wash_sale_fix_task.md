# Task Tracker: Cache Long/Short wash-sale fix

- [x] Confirm findings against real code (read `engine/cache_long_short_engine.py`,
      `data/cache_long_short_store.py`, `pairs_ondemand.analyze_pair`'s real return shape).
- [x] Reproduce the wash-sale bug directly (targeted-test hook caught both
      pre-existing tests that encoded the wrong semantics).
- [x] Fix `check_wash_sale` — acquisition-date-window based, ticker-exact-match
      scoped, `as_of` param added, docstring explains the backward/forward-window limitation.
- [x] Add `wash_sale_note` forward-looking advisory to `generate_sell_down_orders`'s
      approved response.
- [x] Correct `check_correlation_drift`/module docstring's inaccurate
      "delegates to analyze_pair" claim.
- [x] Fix `record_tax_lot`/`close_tax_lot` tz-normalization (`_naive_utc` helper,
      mirrors `data/broker_fills_store.py`).
- [x] Rewrite/extend `TestCheckWashSale` (5 cases) — reproduces both original
      failure directions plus reacquisition-after-harvest, pnl-direction-irrelevance,
      and an `as_of`-parametrized historical check.
- [x] Add 2 tz-normalization regression tests to `tests/test_cache_long_short_store.py`.
- [x] `ruff check --select=F821,F822,F823,E9` clean.
- [x] `pytest tests/test_cache_long_short_engine.py tests/test_cache_long_short_store.py tests/test_cache_long_short_api.py` — 67 passed.
- [x] `docs/architecture/signal-engines.md` — new Cache Long/Short entry added.
- [ ] Open PR (feature branch already created: `fix-cache-long-short-wash-sale`).
