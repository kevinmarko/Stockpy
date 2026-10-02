# Paper pipeline review fixes — walkthrough (2026-10-02)

The operator ran a code review over #1089, #1092, #1094, #1093 and #1095, and it returned 9 findings.

- **Already resolved by #1096 (Alpaca removal):**
  - #3: RISK REDUCE on the Alpaca path. That path no longer exists.
  - #6: the duplicated `_uses_local_paper_ledger` guard. It was deleted.
- **Fixed here:** the other 7.

| # | Finding | Fix |
|---|---|---|
| 1 | Open pipeline positions could fall out of the universe and never exit | `pipeline/advisory_inputs.py`: open `main_pipeline` paper symbols are unioned in last, after retention. They are never rating-excluded, never suppress the DEFAULT_TICKERS fallback, and `UniverseBuild.held` stays Robinhood-only. They are read with the database-only `PaperAccountStore.open_position_symbols()`. |
| 2 | Probe overrode deliberate zeros and ignored the gross cap | `main_orchestrator._probe_weight_for_row` plus cycle gating. The probe applies only while closed `main_pipeline` trades are below 30 (an unreadable count switches it off). It is withheld for a Dual-Momentum-zeroed risky asset, or when the regime multiplier or meta-label composite is ≤0, NaN or missing. It is scaled by the regime multiplier and capped together with existing pipeline exposure at `MAX_PORTFOLIO_GROSS`. Skip reasons are logged once per cycle. |
| 4 | No holiday or early-close calendar; stale fills | `engine/advisory_agent.is_us_market_open_now`. Primary source: FMP `exchange-market-hours.isMarketOpen`, cached 60 s. Fallback: the clock check minus `holidays.NYSE`, which has no early-close data (disclosed). Separately, `FMPPaperBroker` rejects fills on a missing quote timestamp or one older than `PAPER_FILL_MAX_QUOTE_AGE_SECONDS` (default 900). |
| 5 | 402 latched `/quote` forever | A 402 is latched only when its body says it is a plan restriction ("Restricted Endpoint" or the access-denied markers). Any other 402 is transient: it advances the cooldown and is never latched. |
| 7 | Probe weight had no typed confirmation | `PAPER_PIPELINE_PROBE_WEIGHT` is now in `DANGEROUS_KEYS`. |
| 8 | Unbounded per-symbol fallback | `FMPProvider._quotes_one_by_one` has a 20 s wall-clock budget. Symbols it doesn't reach go to yfinance, logged. |
| 9 | Freeze status did network price marking | `scripts/feature_freeze_status.py` uses `open_position_symbols()` and reads the database only. |

**Live facts this relied on (checked 2026-10-01):**
- `exchange-market-hours` and `holidays-by-exchange` work on this FMP plan.
- A comma-separated `/quote` returns `[]`, so there is no bulk alternative.
- `holidays` is installed.
- The regime multiplier and meta-label composite default to 1.0 when the model is absent, so the gate blocks only on a real risk opinion.

**Verification:** the targeted test files (417 passed), then the full offline suite and ruff (see PR).
