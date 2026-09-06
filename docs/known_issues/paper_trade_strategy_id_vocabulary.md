# Paper-Trade `strategy_id` Vocabulary Is Not Standardized

**Status**: Fixed for the options desk (2026-09-06 pass) — the automated writers for `dispersion-trading`, `copula-stat-arb`, `zero-dte-momentum-breakout`, `earnings-crush`, `put-credit-spread`, `call-credit-spread`, `call-debit-spread`, `put-debit-spread`, `covered-call`, and `iron-condor` now stamp their matching `pilots.catalog` Pilot `id` (see the table below). `main_pipeline` and the `queue_builder.py` advisory bucket remain deliberately out of scope. `Manual Trade`, `untagged`, and `Delta Hedge` remain legitimate non-Pilot buckets.
**Date**: 2026-09-06
**Incident Level**: Low/informational (measurement-quality gap, not a correctness bug)

## Root Cause

`sizing/kelly.py::estimate_win_rate_and_payoff_per_strategy` filters
`transactions_store`'s `trades` table by **exact string equality** on the
`strategy` column to compute per-strategy win rate / payoff for Kelly sizing
warm-up. This makes the `strategy_id`/`strategy_name` string every paper-trade
writer stamps onto an order a de facto vocabulary that must line up exactly
for two trades to be pooled as "the same strategy." They currently do not
line up — writers across the codebase use at least four different naming
conventions for what is conceptually the same idea, so Kelly's per-strategy
warm-up silently fragments across near-duplicate keys a human would consider
identical.

## The four (or more) conventions found

1. **Pilot-registry kebab-case `id` slugs** (`pilots/catalog.py`, e.g.
   `"trend-following"`, `"cross-sectional-momentum"`, `"vrp-premium-selling"`)
   — the identifier shown in the Pilots PWA UI. **No paper-trade writer
   audited in this pass actually stamps this exact string onto an order.**
   `pilots/catalog.py` also carries a SEPARATE, snake_case
   `validation_strategy_id` field (e.g. `"timeseries_momentum"`,
   `"cross_sectional_momentum"`) matching `STRATEGY_REGISTRY`'s keys in
   `scripts/refresh_validations.py` — a third, distinct vocabulary from
   either of the above, used only for validation-harness lookups, not order
   attribution.

2. **Human-readable free-text labels**, Title Case with spaces — the most
   common convention among the automated pilot writers:
   `"Dispersion Arbitrage"` (`pilots/dispersion_trading.py`), `"Copula Stat
   Arb"` (`pilots/copula_stat_arb.py`), `"Delta Hedge"`
   (`pilots/options_hedging.py`), `"Vol Mispricing"`
   (`pilots/vol_mispricing.py`), `"Earnings Crush"`
   (`execution/options_paper_executor.py`), `"0DTE Momentum Breakout"`
   (`pilots/zero_dte_engine.py`, or a caller-supplied custom
   `strategy_name` — see the fix in this same remediation pass that makes
   the 0DTE exit path read the position's real tag instead of re-hardcoding
   this literal).

3. **The literal `"Manual Trade"`** — stamped by every genuinely
   human-initiated order with no automated-strategy context at all:
   `pilots/paper_broker.py::execute_roll` (the Paper Broker screen's Roll
   dialog) and both branches of
   `pilots/paper_broker_options_order.py::execute_paper_order` (the Options
   Chain / Quick Trade order ticket, single-leg and multi-leg). This pass
   confirmed all three sites genuinely have no better strategy context to
   thread through (no caller in the chain — webapp component, Pydantic
   request model, or Python function signature — carries a strategy
   identity) and left the literal as intentional, with a comment at each
   site.

4. **`"untagged"`** — `data.paper_account_store.PaperPosition.strategy_id`'s
   own column default, and `PaperAccountStore.apply_multi_leg_fill`'s
   `strategy_id` parameter default. This is a THIRD manual-trade label,
   distinct from `"Manual Trade"` above: the multi-leg branch of
   `pilots/paper_broker_options_order.py::execute_paper_order` (its Bull
   Call Spread / Iron Condor / Straddle / etc. branch, `store.
   apply_multi_leg_fill(...)`) never passes a `strategy_id` argument at all,
   so a manually-placed multi-leg strategy is tagged `"untagged"` while a
   manually-placed single-leg order or manual roll is tagged `"Manual
   Trade"` — two different buckets for the same "a human clicked the order
   ticket" event, purely because one code path forgot to pass the kwarg the
   sibling path does pass. Not fixed in this pass (out of scope: the task
   that produced this doc named only the two specific `"Manual Trade"`
   hardcodes above, not this third, previously-undocumented instance) but
   flagged here since it's the same vocabulary-fragmentation problem in
   miniature.

5. **The single literal `"advisory"`** —
   `execution/queue_builder.py::CONFIG["strategy_id"]` stamps every
   advisory-path equity order queued to `output/execution_queue.json` (the
   Robinhood MCP execution bridge, a DIFFERENT write path from
   `PaperAccountStore` entirely) with this one bucket. This is coarser than
   any of the above: it does not distinguish WHICH signal/strategy inside
   the advisory engine drove the recommendation — every advisory-path
   equity trade pools into one undifferentiated Kelly cohort. One partial
   exception: `execution/compose.py`-composed intents carry their own
   per-source override (`"advisory"` / `"follow-<pilot_id>"` for a
   single-source rec, `"composed"` for one netted across multiple sources —
   see `queue_builder.py`'s `intent_strategy_id` comment), but a plain,
   uncomposed `Recommendation`/`FollowIntent` (every existing caller as of
   this writing) still falls through to the flat `"advisory"` default. This
   is a real, structural limitation, not something this pass attempts to
   fix.

## Concrete consequence

A strategy that is conceptually one thing — e.g. the pilot the operator
knows as "Trend Following" (`id="trend-following"`,
`validation_strategy_id="timeseries_momentum"`) — has no single string
anywhere in a paper-trade writer that matches either of its own catalog
identifiers. If/when a writer for that pilot is added, whatever ad hoc label
it picks becomes YET ANOTHER key `estimate_win_rate_and_payoff_per_strategy`
pools separately from every other near-synonym. Kelly's per-strategy warm-up
(win rate, payoff ratio) is therefore measuring fragments of a strategy's
real trade history rather than the whole of it, understating sample size and
delaying (or permanently preventing, for a low-volume strategy) the warm-up
threshold from ever being reached — a measurement-quality gap, not a
fabricated-metric or fail-open safety bug (CONSTRAINT #4/#6 are not violated;
every individual trade's `strategy_id` is genuine, just inconsistently
spelled across writers).

## What was fixed in this pass, and what wasn't

**2026-08-24 pass** (see the accompanying PR 872 remediation commit): three
genuinely wrong hardcodes were corrected —
`execution/options_paper_executor.py`'s `Close Earnings Crush` path used
`dict.get(key, default)`, which does not fall back on an explicitly-`None`
value (fixed to `dict.get(key) or default`); `pilots/zero_dte_engine.py`'s
0DTE exit path re-hardcoded `"0DTE Momentum Breakout"` instead of reading the
position's own `strategy_id` (fixed to thread the real tag through, so a
custom-named 0DTE entry now closes under the SAME tag it opened with). This
pass did NOT touch what label any writer stamps going forward — only how
faithfully an *exit* preserved whatever label the *entry* used.

**2026-09-06 pass (this feature — Strategy Report Card + vocabulary
standardization, Phase 1)**: the options-desk automated writers named in
finding #2 above were moved off their free-text Title Case labels onto
`pilots.catalog`'s kebab-case Pilot `id` slugs — the exact direction this
doc's own "Suggested direction for a future pass" section below recommends.
Ten new catalog Pilots (`category="Options"`, `weights={}`,
`followable=False` — see `docs/architecture/webapp-and-gui.md`) were added to
`pilots/catalog.py`, and each write site now stamps the matching id instead
of its old label:

| Writer | Old label | New `strategy_id` |
|---|---|---|
| `pilots/dispersion_trading.py::execute_dispersion_trade` | `"Dispersion Arbitrage"` | `"dispersion-trading"` |
| `pilots/copula_stat_arb.py::execute_copula_spread_trade` | `"Copula Stat Arb"` | `"copula-stat-arb"` |
| `pilots/zero_dte_engine.py` (`execute_0dte_trade`'s default, `evaluate_0dte_exits`'s fallback, `ZeroDteExitSignal.strategy_id`'s default) | `"0DTE Momentum Breakout"` | `"zero-dte-momentum-breakout"` |
| `execution/options_paper_executor.py`'s Earnings Crush entry/close paths | `"Earnings Crush"` | `"earnings-crush"` (the close-side query now matches EITHER label, so a position opened before this pass still closes under the correct bucket) |
| `execution/options_paper_executor.py`'s directive-driven multi-leg entries, via the new `pilots.catalog.OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID` lookup keyed on `technical_options_engine.py`'s real `directive["Strategy"]` string | *(previously untagged / passed through verbatim)* | `"put-credit-spread"` / `"call-credit-spread"` / `"call-debit-spread"` / `"put-debit-spread"` / `"covered-call"` / `"iron-condor"` |

`pilots/paper_broker_options_order.py::execute_paper_order`'s single-leg
branch also now passes an explicit `strategy_id="Manual Trade"` — a read-side
clarity fix, not a Pilot-id rename (a human-placed order has no Pilot to
attribute to). It previously fell back to the store's own `"untagged"`
column default, so a manual single-leg order now consistently reads
`"Manual Trade"` instead of sometimes `"untagged"` depending on which branch
handled it; the multi-leg branch's own separate `"untagged"` gap (finding #4
above) is **not** fixed by this pass.

A new read-side helper, `pilots/strategy_report_card.py`
(`GET /strategy/report-card`), cross-references every catalog Pilot's
validated backtest (`ValidationHistoryStore`) against its real paper-trading
history (`PaperAccountStore`) and carries its own
`LEGACY_STRATEGY_ID_ALIASES` map translating the pre-standardization labels
above onto the new ids, so trades recorded before this pass still attribute
correctly on the read side. It gates every realized statistic behind
`MIN_TRADES_FOR_VERDICT = 10` — below 10 closed trades it reports an honest
`None`/"insufficient sample" rather than a noisy or fabricated verdict
(CONSTRAINT #4).

**Not fixed, deliberately out of scope for both passes**: forcing every
writer in the codebase onto one shared vocabulary is a larger refactor than
either pass above attempted — the 2026-09-06 pass covers only the options
desk named in the table (a paper-trade writer for a non-options Pilot, e.g.
one of the equity signal modules in `pilots/catalog.py`, still stamps
whatever ad hoc label it always did, if it writes at all). Specifically,
**`main_pipeline` attribution and `execution/queue_builder.py`'s
`"advisory"`/`"composed"` bucket (finding #5 above) remain deliberately out
of scope** — a weighted-sum ensemble decision has no single principled
strategy owner to attribute the trade to, so folding either onto one Pilot
id would misattribute by construction rather than fix the fragmentation.
`"Manual Trade"`, `"untagged"`, and `"Delta Hedge"` remain legitimate,
intentional non-Pilot buckets (findings #3/#4 above), not gaps to close.
This doc records the landscape so that future work has an accurate map
instead of having to re-derive it.

## Suggested direction for a future pass

Standardize on the pilot-registry kebab-case `id` (`pilots/catalog.py`) as
the one attribution key every writer stamps — it is already the identifier
surfaced to the operator in the UI, is unique per pilot by construction, and
sidesteps the ambiguity between `strategy_name` (a display label) and
`strategy_id` (an attribution key) that several writers currently conflate
(e.g. `pos_strategy_id = strategy_name` at the 0DTE open site). Human-
initiated manual trades (`"Manual Trade"`, `"untagged"`) and the advisory
bridge's `"advisory"`/`"composed"` bucket are legitimately NOT
pilot-attributable and should stay as their own explicit, documented
buckets rather than being folded into the pilot-slug vocabulary.
