# Paper trading: real marks + paper outcomes feed the models — walkthrough

Branch: `fix-paper-trading-pricing-and-model-feed` (2026-09-27)

Origin: a `/code-review` of the paper-trading path ("performance, fully
functional, feeding into our models") produced 5 findings; the operator asked
to fix all 5 and turn the model feed on.

## Findings → fixes

| # | Finding | Fix |
|---|---------|-----|
| 1 | `PaperAccountStore._resolve_position_prices` marked a stock at **$0** when a batch-quote row lacked a price, marked everything at cost on any FMP failure, and bypassed the Alpaca/yfinance fallback | Stocks now go through `_fetch_stock_prices` → `pilots.price_provider.get_latest_prices` → `CompositeProvider.get_quotes_batch`. Missing/zero/NaN quotes are dropped. Unpriced positions fall back to cost basis **and are flagged** (`store.last_unpriced_symbols`, `PositionSnapshot.mark_is_estimated`). The daemon's loss-velocity sampler skips unpriced ticks. Pricing runs after the DB session closes. |
| 2 | Option marks used hardcoded `sigma=0.30`, `r=0.04`, `max(1, dte)` and drove auto-exits | `_option_mark_per_share`: intrinsic if expired → live bid/ask mid → Black-Scholes on the contract's own live IV + `OPTIONS_RISK_FREE_RATE` → last trade → otherwise unpriced. Chains cached `PAPER_OPTION_MARK_CACHE_SECONDS` (60 s). `evaluate_position_exits` skips groups with an unpriced leg. |
| 3 | Enabling the bridge would feed manual/hedge/untagged/option trades into unfiltered aggregate Kelly | `_bridge_exclusion_reason`: option contracts + `PAPER_TRADES_BRIDGE_EXCLUDED_STRATEGIES` (default `Manual Trade`, `Delta Hedge`, `untagged`) → `bridge_status="excluded"`. Then `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` flipped to **True**. `excluded_count` added to bridge metrics + Retrospective Journal tile. |
| 4 | `/ws/risk/portfolio` made 2 uncached FMP calls/sec/client | Both calls now route through the cached `CompositeProvider.get_quotes_batch` (via the new stock seam and `get_latest_prices`). |
| 5 | #1052's batch fallback re-hit FMP per symbol after a whole-batch failure | `_get_quote_via_fmp_chain(include_primary=...)`; FMP is skipped when the batch returned nothing. |

## Verification

- `tests/test_paper_marking_and_model_feed.py` (new, 17 tests): stock/option marking, $0/NaN never used, session not held during fetch, chain cache, exit-skip on unpriced legs, bridge on-by-default + exclusions + configurability.
- `tests/test_market_data.py::...test_whole_batch_failure_skips_fmp_single_quote_retries` — mutation-checked (fails with the fix reverted).
- `tests/test_daemon_runtime.py::...test_loss_velocity_sample_skipped_when_a_position_is_unpriced`.
- Retrospective bridge-mechanics suites updated (file-scoped fixture empties the exclusion list; "defaults False" assertions updated).
- Settings census/liveness artifacts regenerated.
- Webapp: typecheck clean; `RetrospectiveJournalModelFeed.test.tsx`.

## Disclosed, not changed

- The `trades` ledger still has no paper/live discriminator column — bridged rows are identifiable only by `notes="Paper bridge, ..."`, so MCP/reporting consumers of that ledger now include paper outcomes.
- `pilots/options_risk.py` Greeks still use a fixed `sigma=0.25`, and `OptionsPaperExecutor`'s roll/strategy fill pricing (`_price_option_contract`) still uses `sigma=0.30`. Both are outside these 5 findings.
- 4 `tests/test_options_gex.py` / `tests/test_options_risk.py` failures reproduce on untouched `main` (pre-existing, not from this change).
