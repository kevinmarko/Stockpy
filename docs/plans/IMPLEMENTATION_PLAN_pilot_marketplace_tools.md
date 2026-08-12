# Implementation Plan: 4 New Pilot Marketplace MCP Tools

Branch: `add-pilot-marketplace-tools` (worktree: `.claude/worktrees/pilot-marketplace-tools`)
Base: `main` (includes MCP Apps SDK widgets — `mcp_widget_resources.py`, `mcp_widgets/` —
OAuth 2.1 connector work — `mcp_oauth_provider.py`, `mcp_oauth_store.py` — and
`readOnlyHint=True` tool annotations, PR #658).

**Verification status (2026-08-10):** the branch was rebased onto latest `origin/main`
(`aae53228`, includes PR #657's OAuth rate limiting) and every file/line citation below
was spot-checked against the real post-rebase tree. All cited functions/classes exist
exactly as described (`FollowsStore.upsert/remove/get_mirrored`,
`pilots.performance.pilot_headline/pilot_performance`,
`pilots.scoring.pilot_holdings/load_snapshot`, `CompositeProvider.get_latest_quote`/
`get_provider`, `MarketDataError`, `PortfolioPosition`, `pilots/attribution.py`'s
pure-function pattern, `api/data_api.py`'s quote-fetch error-handling at line 445). Two
line numbers drifted by 1-2 lines from PR #658 merging in after the original reads and
are corrected below (`_PILOT_PICKER_UI`/`_PILOT_DETAIL_UI`/`_FOLLOW_RESULT_UI` now at
`investyo_mcp_server.py:64-66`; the mirror.py cap formula at `pilots/mirror.py:410-413`).
No other discrepancies found — the architectural reasoning, primitive choices, and PR
split below are unchanged and ready to implement.

## Context

`investyo_mcp_server.py`'s "[9] PILOTS MARKETPLACE" section (lines ~2803-3219) exposes
`pilots/` (catalog, scoring, performance, follows_store, mirror) as MCP tools, mirroring
`api/pilots_api.py`'s surface to the webapp PWA. Four gaps, each independently motivated:

1. **`unfollow_pilot`** — `follow_pilot(pilot_id, amount)` rejects `amount <= 0`
   (`investyo_mcp_server.py:3156-3157`). There is no way to stop following a Pilot
   through this connector at all today.
2. **`compare_pilots`** — `list_pilots`/`get_pilot_detail` already render as interactive
   MCP Apps widgets (card grid, detail panel); there is no side-by-side comparison view,
   and the user explicitly asked for one reusing the existing widget design system.
3. **`get_quote`** — a plain live-quote lookup is confirmed missing from the tool
   inventory even though the platform's own `data/market_data.py::CompositeProvider`
   already exposes exactly this primitive to every other read path.
4. **`get_portfolio_by_pilot`** — operators want to see real account P&L segmented by
   which followed Pilot a position came from; nothing in the current tool set answers
   "how is Pilot X actually doing in my live account," only "how did Pilot X's backtest
   do" (`get_pilot_performance`) or "what does Pilot X currently want to hold"
   (`get_pilot_detail`).

All four follow the existing section's constraints: read tools never import a heavy
engine; the one write tool (`unfollow_pilot`) never contacts a broker, same as
`follow_pilot`; every tool returns markdown + a trailing ` ```json ` block; CONSTRAINT #4
("never fabricate data") and CONSTRAINT #6 ("dead-letter resilience — never raise")
apply throughout.

---

## Tool 1 — `unfollow_pilot(pilot_id: str) -> str`

### FollowsStore already supports removal — two primitives, not one

`pilots/follows_store.py` already has BOTH:

* `FollowsStore.remove(pilot_id) -> bool` (`pilots/follows_store.py:199-210`) — **hard
  delete**, drops the row (including its `mirrored` attribution) entirely.
* `FollowsStore.upsert(pilot_id, 0.0)` (`pilots/follows_store.py:159-197`) — the
  established **"cancel"** semantics already used by `api/pilots_api.py::upsert_follow`
  (`PUT /follows`, docstring: *"``amount == 0`` cancels it"*, `api/pilots_api.py:2481-2489`)
  and by `follow_pilot` itself. Sets `status="cancelled"`, excludes the row from
  `aum_proxy()`/`followers_proxy()`/`list_active()` (so it drops out of `get_follows()`
  immediately), but **keeps the row and its `mirrored` field** — `upsert` explicitly
  preserves `mirrored` when present (see the "Backward-compatible" design-constraint
  bullet in the module docstring, `pilots/follows_store.py:50-52`).

**Recommendation: `unfollow_pilot` calls `FollowsStore().upsert(pilot_id, 0.0)`, NOT
`.remove()`.** Reasoning:

* `remove()` would delete the `mirrored` attribution (`[{symbol, weight,
  target_notional}]`) — the ONLY record of what this follow put on. Without it,
  `unfollow_pilot` cannot honestly tell the user *which* positions/how much value is
  left behind (it would have to fall back to "we don't know"), and `get_portfolio_by_pilot`
  (Tool 4) would permanently lose the ability to attribute residual holdings to a Pilot
  the user just unfollowed.
* `upsert(0.0)` is byte-identical, user-visible behavior to what "unfollow" needs: gone
  from `get_follows()`, gone from AUM/followers proxies, and — because `plan_follow` is
  never called again for a cancelled follow — no further rebalancing/force-exit ever
  happens for it. That satisfies "stops future rebalancing."
* It is consistent with the ONE existing "stop following" primitive already wired into
  the PWA backend (`PUT /follows` amount=0), rather than introducing a second,
  behaviorally-different meaning for "unfollow" than "cancel."
* `remove()` stays available, unused by this tool, as a distinct "purge from the store
  entirely" primitive — do not repurpose it here; note this explicitly in the tool
  docstring so a future reader doesn't "fix" this to call `remove()`.

### No automatic unwind — verified against `plan_follow`/`get_execution_queue`

`pilots/mirror.py::plan_follow` only runs from `follow_pilot` (amount > 0). There is no
"rebalance to zero" or force-liquidate path triggered by unfollowing — the *only*
force-exit mechanism in the codebase is the one **inside** `build_follow_intents`
(`pilots/mirror.py:376-502`), which fires on the *next* `follow_pilot` call for a name
the Pilot has since dropped from its own holdings, not on unfollow. Since `unfollow_pilot`
never calls `plan_follow` again, nothing is queued, and `output/execution_queue.json`
(read by `get_execution_queue`, `investyo_mcp_server.py:2119`) is untouched.

**Honest default (confirmed, not invented):** unfollowing removes the follow from
tracking and stops future rebalancing, but places **no** sell order and writes **no**
queue entry. The tool must say this plainly and, since `mirrored` is preserved, can say
it *concretely* — with real symbols/values, not just a generic warning.

### Kill-switch interaction

**Recommendation: do NOT gate `unfollow_pilot` on `GlobalKillSwitch.is_active()`.**
`follow_pilot` blocks on the kill switch because it plans NEW risk (an order-queue
preview that could grow exposure). Unfollowing only stops future increases in exposure
and removes a follow from tracking — it takes on no new risk and, if anything, should be
available precisely when the kill switch is active (an operator pausing everything
should still be able to stop tracking a Pilot). This reasoning holds; verified there is
no other precedent in this codebase for gating a purely-subtractive state change on the
kill switch (`FollowsStore.upsert(0.0)` itself has no kill-switch check either, in
`pilots_api.py`'s `PUT /follows` — only `POST /pilots/{id}/follow` checks it).

### Widget decision

**Recommendation: no widget — plain markdown is sufficient.** Reasoning:

* `follow_pilot`'s widget (`follow-result.html`) exists because its payload is
  genuinely tabular and needs visual weight (a banner + a multi-row planned-intents
  table with per-symbol action/notional/rationale) — the same shape `pilot-picker.html`
  and `pilot-detail.html` already justify a widget for.
* `unfollow_pilot`'s payload is fundamentally a single confirmation sentence plus, at
  most, a short list of residual symbols/values (typically 0-10 rows) — well inside what
  a chat markdown table already renders cleanly in any MCP host, including ones with no
  Apps SDK support at all (stdio clients, Claude Code).
* Building a 4th widget template + a 4th `_WIDGET_RESOURCES` entry + a 4th `App(...)`
  boilerplate wrapper for a payload this simple adds maintenance surface (one more
  template to keep the CSS token contract) without a proportional UX gain. If a later
  iteration wants a consistent "action confirmed" look across follow/unfollow, the
  existing `.banner-caution` class in `_common.css` already gives markdown-rendering
  hosts and a future widget the same visual language for free — no work needed now.

### Signature, data sources, output shape

```python
@mcp.tool()
def unfollow_pilot(pilot_id: str) -> str:
```

* No `ToolAnnotations(readOnlyHint=True)` — it writes state (mirrors `follow_pilot`,
  per `tests/test_investyo_mcp_tool_annotations.py`'s explicit "must never be given
  this annotation" pattern for write tools).
* Data sources:
  * `pilots.catalog.get_pilot(pilot_id)` (`pilots/catalog.py:515`) — 404-equivalent via
    the existing `_unknown_pilot_message` helper (`investyo_mcp_server.py:2819-2822`) on
    unknown id.
  * `pilots.follows_store.FollowsStore().get(pilot_id)` (`pilots/follows_store.py:149`)
    — read the row BEFORE cancelling, to report `amount`/`mirrored` from the pre-cancel
    state (matches `follow_pilot` reading `plan_follow`'s result before formatting).
  * `pilots.follows_store.FollowsStore().upsert(pilot_id, 0.0)` — the write.
  * `pilots.follows_store.FollowsStore().get_mirrored(pilot_id)` (`pilots/follows_store.py:242`)
    — the last mirrored holding set, for the "you still hold ~$X across N symbols"
    disclosure. Empty list → "no attributed positions on record" (honest, not "you hold
    nothing" — a legacy/never-planned follow has no attribution either).
* Markdown: header, "not currently followed" short-circuit if no row exists (idempotent,
  returns a message rather than erroring), a confirmation line, then — only if
  `mirrored` is non-empty — a `| Symbol | Target Notional (last attributed) |` table
  under a `## Still Held (not automatically sold)` heading with the exact honesty
  sentence from the task: *"You still hold existing positions from this Pilot; they
  will not be automatically sold."*
* JSON payload: `{"pilot_id", "was_following": bool, "cancelled_amount": float|None,
  "residual_mirrored": [...], "note": str}`.
* Failure handling: wrap in `try/except Exception as e: return f"Failed to unfollow
  pilot '{pilot_id}': {str(e)}"`, matching every sibling tool.

---

## Tool 2 — `compare_pilots(pilot_ids: list[str], range: str = "1M") -> str`

### Data needed per Pilot — confirmed shapes, no duplication needed

* Headline metrics: `pilots.performance.pilot_headline(pilot)` — confirmed
  (`pilots/performance.py:136-154`) returns exactly `{sharpe, dsr, pbo, max_drawdown,
  deployable}`, all `None` when ungated — this is the SAME call `list_pilots` already
  makes per pilot (`investyo_mcp_server.py:2850`).
* Equity curve: `pilots.performance.pilot_performance(pilot, range=range_norm)`
  (`pilots/performance.py:157-266`) — confirmed exact shape:
  `{"metrics": {...}|None, "curve": [{"date": "YYYY-MM-DD", "value": float}]|None,
  "benchmark": [...]|None, "macro_benchmark": [...]|None, "reason": str|None, "range": str}`.
  `curve` is base-100 indexed (`equity = (1+returns).cumprod()*100`,
  `validation/harness.py:80`), downsampled to ≤120 points
  (`MAX_EQUITY_CURVE_POINTS`, `validation/harness.py:60`). Base-100 indexing means
  curves ARE directly comparable on one shared axis without renormalization — a real
  design gift for the chart (see below).
* Holdings count: `pilots.scoring.pilot_holdings(pilot, snapshot)` length, same as
  `list_pilots` (`investyo_mcp_server.py:2851`).

**Recommendation: call these SAME functions directly, per pilot, in a loop — no
refactor, no new shared helper needed.** `pilots/performance.py` and `pilots/scoring.py`
are already pure, dependency-light, snapshot-driven functions designed to be called
per-Pilot by any orchestrating tool (that's exactly what `list_pilots`/`get_pilot_detail`/
`get_pilot_performance` already do independently). `compare_pilots` is a thin
orchestration layer: load the snapshot **once** (`pilots.scoring.load_snapshot()`),
then loop `pilot_ids` calling `pilot_headline`, `pilot_holdings`, `pilot_performance`
per id. This is the cleaner approach given the existing structure — extracting a new
"shared comparison helper" module would just wrap three already-reusable one-line calls
with no logic to actually share.

### Cap: recommend exactly 3

* Enforce `2 <= len(pilot_ids) <= 3`, dedupe while preserving order, reject with a
  clear message otherwise (`"compare_pilots needs 2-3 distinct pilot ids (got N)."`).
* Justification tied to layout: at 2-3 columns, `grid-template-columns:
  repeat(auto-fit, minmax(220px, 1fr))` (the SAME pattern `.picker-grid` already uses,
  `mcp_widgets/templates/pilot-picker.html:6-11`) lays out cleanly side-by-side in a
  typical chat-panel width (~400-700px) without wrapping into an awkward 2-then-1 or
  scrolling ≥4-wide row. A shared equity-curve SVG overlay (below) also gets
  unreadable past ~3 overlaid series (color/legend collision) — this is the same reason
  `webapp/src/components/charts.tsx`'s comparison charts only ever overlay 2-3 series
  (curve + benchmark + macro_benchmark) at once.
* 1 pilot has no comparison to make (`get_pilot_detail` already covers it); reject with
  the same message.

### Equity-curve chart: build a dependency-free inline SVG polyline — recommended

**Recommendation: (a) hand-draw a vanilla-JS inline SVG line chart, not (b) skip the
visual.** Reasoning:

* Cost is genuinely low: the input is already a downsampled ≤120-point
  `[{date, value}]` array per pilot, base-100 indexed, so NO scaling logic beyond a
  shared min/max → SVG-coordinate map is needed (no log scale, no per-series
  renormalization — base-100 already makes magnitude directly comparable).
* Value is high: the equity-curve overlay is the single most compelling reason to build
  a NEW widget at all — the stat-card comparison (Sharpe/DSR/PBO side-by-side) is
  already fully expressible as a markdown table with no widget needed. Skipping the
  chart would mean building an entire new widget template, `_WIDGET_RESOURCES` entry,
  and `App(...)` wrapper for a payload that provides zero visual value over plain
  markdown — not worth the maintenance surface.
* It matches the codebase's existing constraint: every widget template is fully
  self-contained (vendored `@modelcontextprotocol/ext-apps` bundle + `_common.css`/
  `_common.js`, no external network fetch — `mcp_widget_resources.py`'s own docstring
  frames this as a hard requirement). Recharts (`webapp/src/components/charts.tsx`'s
  actual charting library) is NOT usable here — it would need to be vendored into the
  `mcp_widgets/build/` npm bundle, a materially bigger, riskier addition to the build
  pipeline for what a ~40-line hand-rolled polyline function achieves just as well at
  this data volume (≤120 points × ≤3 series).

**Concrete SVG design** (for `_common.js`, a new `renderEquityOverlaySvg(container,
series)` function; `series = [{label, color, points: [{date, value}]}]`):

```js
function renderEquityOverlaySvg(container, series) {
  const W = 600, H = 200, PAD = 8;
  // Union of all dates across series -> shared X domain (each pilot may have a
  // different OOS window length; an honest chart does NOT force-align them).
  const allDates = [...new Set(series.flatMap(s => s.points.map(p => p.date)))].sort();
  const allValues = series.flatMap(s => s.points.map(p => p.value));
  if (!allDates.length || !allValues.length) { /* render empty-state, return */ }
  const xIdx = new Map(allDates.map((d, i) => [d, i]));
  const xScale = i => PAD + (i / Math.max(1, allDates.length - 1)) * (W - 2 * PAD);
  const yMin = Math.min(...allValues), yMax = Math.max(...allValues);
  const yScale = v => H - PAD - ((v - yMin) / Math.max(1e-9, yMax - yMin)) * (H - 2 * PAD);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "compare-equity-svg");

  // Baseline at value=100 (the shared starting point every base-100 curve begins at).
  const baseline = document.createElementNS(svg.namespaceURI, "line");
  baseline.setAttribute("x1", PAD); baseline.setAttribute("x2", W - PAD);
  baseline.setAttribute("y1", yScale(100)); baseline.setAttribute("y2", yScale(100));
  baseline.setAttribute("class", "compare-equity-baseline");
  svg.appendChild(baseline);

  for (const s of series) {
    const pts = s.points
      .filter(p => xIdx.has(p.date))
      .map(p => `${xScale(xIdx.get(p.date))},${yScale(p.value)}`)
      .join(" ");
    const poly = document.createElementNS(svg.namespaceURI, "polyline");
    poly.setAttribute("points", pts);
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", s.color);
    poly.setAttribute("stroke-width", "2");
    svg.appendChild(poly);
  }
  container.appendChild(svg);
  // + a small color-keyed legend row above/below, reusing category-chip styling.
}
```

* Ordinal 3-color palette (new `_common.css` custom properties, e.g.
  `--compare-1: var(--growth)`, `--compare-2: var(--cat-momentum)`,
  `--compare-3: var(--caution)`) — reuse existing tokens rather than inventing new hex
  values, consistent with `CATEGORY_COLOR_MAP`'s existing pattern.
* Honest empty-state: if a compared Pilot's `curve` is `None` (no validated backtest),
  it is simply omitted from the SVG (not zero-filled) and called out in the stat table
  as "— (no validated backtest)" exactly as `list_pilots`/`get_pilot_detail` already do
  — never fabricate a flat line.
* No axis labels/gridlines/tooltip needed for v1 (keeps the ~40-line budget honest);
  the stat-card row above the chart already gives exact numeric values.

### New widget file + registration

* New `mcp_widgets/templates/pilot-compare.html`, structurally identical to
  `pilot-picker.html`/`pilot-detail.html` (bundle + `_common.css`/`_common.js`
  placeholders, `App({name: "PilotCompare", ...}, {}, {autoResize: true})`,
  `ontoolresult` → `extractJsonPayload` → a new `renderComparePanel(root, payload)` in
  `_common.js` that lays out up to 3 `.card`s in a `repeat(auto-fit, minmax(200px,1fr))`
  grid (stat rows via the existing `stat-row`/`stat-label`/`stat-value` classes,
  `deployableBadge`/`categoryChip` reused verbatim) plus one shared
  `renderEquityOverlaySvg` call below the cards.
* `mcp_widget_resources.py:_WIDGET_RESOURCES` gains one line, following the exact
  existing tuple pattern:
  ```python
  ("pilot-compare.html", "ui://widgets/pilot-compare.html", "Pilot Comparison"),
  ```
* `investyo_mcp_server.py` gains `_PILOT_COMPARE_UI = {"ui": {"resourceUri":
  "ui://widgets/pilot-compare.html"}} if _WIDGETS_AVAILABLE else None`, alongside the
  3 existing `_..._UI` constants (`_PILOT_PICKER_UI`/`_PILOT_DETAIL_UI`/
  `_FOLLOW_RESULT_UI`, `investyo_mcp_server.py:64-66`).

### Signature, output shape

```python
@mcp.tool(meta=_PILOT_COMPARE_UI, annotations=ToolAnnotations(readOnlyHint=True))
def compare_pilots(pilot_ids: list[str], range: str = "1M") -> str:
```

* JSON payload per pilot: `{"id", "name", "category", "headline": {...},
  "holdings_count", "performance": {"curve": [...], "benchmark": [...], "reason": ...,
  "range": ...}}`, list of 2-3 such objects — the widget's `ontoolresult` extracts this
  array directly (same `extractJsonPayload` contract every existing widget uses).
* Markdown: one `##` section per pilot with the same headline bullets `get_pilot_detail`
  already renders, plus a note that the interactive host renders an overlay chart.

---

## Tool 3 — `get_quote(symbol: str) -> str`

### Data-layer convention — confirmed via `data/market_data.py` + `api/data_api.py`

* Exact method: `CompositeProvider.get_latest_quote(symbol: str) -> Quote`
  (`data/market_data.py:2015-2079`, ABC contract at `:143`). Raises `MarketDataError`
  (`data/market_data.py:65`) on unrecoverable failure — never returns a fabricated
  quote.
* `Quote` fields (frozen dataclass, `data/market_data.py:77-107`): `symbol, price, bid,
  ask, timestamp: datetime, is_stale: bool, source: str`. Docstring confirms
  `is_stale=True` is unconditional for yfinance (`data/market_data.py:94, 384-426`),
  matching CLAUDE.md's documented "yfinance quotes are always `is_stale=True` by
  design" note.
* Singleton accessor: `data.market_data.get_provider() -> CompositeProvider`
  (`data/market_data.py:2507`) — this repo's established call site is
  `api/data_api.py:456` (`provider = get_provider()`), NOT constructing
  `CompositeProvider()` directly. `get_quote` should do the same.
* **Established error-handling pattern** (`api/data_api.py:444-478`, the `/data/quotes`
  endpoint — the closest sibling to this new tool):
  ```python
  try:
      q = provider.get_latest_quote(sym)
  except MarketDataError as exc:
      logger.info(...); # degrade honestly
  except Exception as exc:  # defensive dead-letter
      logger.warning(...)
  ```
  `get_ticker_context` (`investyo_mcp_server.py:284-312`) is NOT the right precedent to
  copy — it predates the `CompositeProvider` convention and calls `yfinance` directly,
  which is exactly the anti-pattern `docs/architecture/data-layer.md` says any new
  quote/bar/fundamentals fetch (outside `DataEngine.fetch_technical_raw()`) must NOT do.
  `get_quote` must go through `CompositeProvider`, matching `api/data_api.py`, not
  `get_ticker_context`.

### Output design — honest about staleness

* Markdown: `# Quote: {SYMBOL}` header, then a small table/bullets:
  `**Price**: $X  |  **Bid**: $X  |  **Ask**: $X`, then an explicit
  `**Live/Delayed**: {"🟢 Live" if not is_stale else "🟡 Delayed"} (source: {source},
  as of {timestamp.isoformat()})` line — never hides `is_stale` behind a plain price.
* Unknown/invalid symbol: catch `MarketDataError` specifically and return
  `f"No quote available for '{symbol}': {exc}"` — degrade gracefully (matches
  `/data/quotes`'s per-symbol dead-lettering; also matches `get_pilot_detail`'s pattern
  of a clear one-line message rather than a raw traceback). A bare `except Exception`
  fallback below it dead-letters anything unexpected, consistent with every other tool
  in this file's outer `try/except Exception as e: return f"Failed to ...: {str(e)}"`.

### Widget: confirmed — none needed

A single quote is a 3-4 field scalar result; nothing here benefits from interactivity
(no drill-down, no follow-up action, no chart). Every existing widget in this codebase
exists because its payload is a LIST (picker grid) or a multi-section DETAIL panel with
a follow-up action (detail + follow form) or a status confirmation with a table
(follow-result). `get_quote` has none of those shapes. Confirmed: no widget.

### Signature

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_quote(symbol: str) -> str:
```

JSON payload: `{"symbol", "price", "bid", "ask", "timestamp": iso8601, "is_stale",
"source"}` (bid/ask may be `NaN` per `Quote`'s own docstring — coerce `NaN`/`inf` to
`None` before `json.dumps` the same way `api/data_api.py::_clean_nan` does, since raw
`NaN` is not valid JSON).

---

## Tool 4 — `get_portfolio_by_pilot()`

### Is real per-position attribution possible? Yes, as an honest PROXY — not full P&L, not "out of scope"

Three independent facts, confirmed by reading the actual code:

1. **No post-execution linkage exists.** `execution/order_manager.py::make_client_order_id`
   embeds `strategy_id` (which `pilots/mirror.py::plan_follow` sets to
   `f"follow-{pilot.id}"` via `execution/queue_builder.py`'s per-intent
   `strategy_id` override, `execution/queue_builder.py:344-359`) into the
   **client-order-id sent to the broker**, but nothing reads it back once an order
   executes. `transactions_store.py::Trade` DOES carry a `strategy` column
   (`transactions_store.py:32`), but `record_trade()` is called ONLY from
   `execute_paper_trade` (`investyo_mcp_server.py:955`) — the platform's own **paper**
   trade journal — never from the real Robinhood order-placement path
   (`robinhood-execution` skill uses the Robinhood MCP's `place_equity_order` directly,
   which never touches `TransactionsStore`). **Conclusion: there is no DB table
   anywhere linking an actual filled brokerage position to the Pilot that originated
   it.** Building that would mean either parsing Robinhood order history for
   `client_order_id` strings (fragile, and Robinhood's API may not even round-trip an
   arbitrary client order id back to the operator) or adding new persistent
   attribution-tracking infrastructure at execution time — genuinely a bigger scope than
   the other 3 tools combined, and out of scope for this PR.
2. **A real, already-computed TARGET attribution DOES exist and is reusable.**
   `FollowsStore.get_mirrored(pilot_id)` (`pilots/follows_store.py:242-252`) returns the
   last `[{"symbol", "weight", "target_notional"}]` set `plan_follow` computed and
   persisted (`pilots/mirror.py:656-689`) — this is the SAME data the system already
   uses to decide how much of a symbol to force-sell when a Pilot drops it
   (`pilots/mirror.py:34-57`'s "per-follow attribution" mechanism, capped at
   `min(last target notional, currently held market value)`,
   `pilots/mirror.py:409-413`). It is explicitly documented as *"the last TARGET
   notional, not a real per-lot cost basis... a proportional estimate"*
   (`pilots/mirror.py:48-49`) — i.e. the codebase already treats this as an honest proxy,
   not ground truth, for a materially similar purpose.
3. **Real position-level P&L exists at the whole-position level.**
   `data.robinhood_portfolio.PortfolioPosition` (`data/robinhood_portfolio.py:97-127`)
   carries real `market_value`, `average_cost`, `unrealized_pl`, `unrealized_pl_pct` per
   symbol — but Robinhood's own `average_cost` is already a single blended per-share
   figure (not FIFO/LIFO lots), so pro-rating a position's `unrealized_pl` by a
   value-fraction is mathematically CONSISTENT with what `average_cost` already implies
   (not an extra approximation layered on top): if `f = attributed_value / market_value`,
   then `f * unrealized_pl == f*market_value - f*quantity*average_cost` exactly, because
   `average_cost` already applies uniformly across every share.

**Recommendation: build this now as an explicitly-labeled PROXY attribution using
`FollowsStore.get_mirrored()` + live position data — genuinely buildable with existing
data, not deferred, not requiring new tracking infrastructure.** The one thing that
MUST be scoped down vs. a naive design: real per-lot cost-basis attribution is NOT
possible and must never be claimed; and multi-Pilot overlap on the same symbol must be
normalized honestly (below), not double-counted.

### `pilots/scoring.py::pilot_holdings` — reusable, but the WRONG primitive here

`pilot_holdings(pilot, snapshot)` (`pilots/scoring.py:211-306`) computes what a Pilot
CURRENTLY wants to hold (a target basket derived fresh from the latest snapshot) — it is
NOT attribution of what the operator's account actually holds because of that Pilot.
Using it directly (e.g. "does this held symbol appear in Pilot X's current top-N
holdings, weighted by target weight") would be the WEAKER, purely-coincidental-overlap
proxy the task warns against — two different Pilots frequently rank the same large-cap
name, and a symbol could be a Pilot's current pick with zero dollars ever actually
allocated to it via a follow. `FollowsStore.get_mirrored()` is strictly better: it is
the system's own record of what THIS follow specifically claimed, capped at real
followed dollars — use it, not `pilot_holdings`, as the attribution basis. (`get_pilot_detail`
already uses `pilot_holdings` for its own, different, legitimate purpose — current
target basket display — so it stays untouched.)

### Algorithm (proxy attribution)

1. Load `account_snapshot = HistoricalStore().latest_account_snapshot()` (same call
   `follow_pilot` already makes, `investyo_mcp_server.py:3169`) — DB-first, never forces
   a live login (respects `ROBINHOOD_AUTO_REFRESH_ENABLED`). No snapshot → return an
   honest "no account data" result, no fabricated positions.
2. For EVERY follow row with a non-empty `mirrored` set — **both active AND cancelled**
   (a cancelled/unfollowed Pilot's residual holdings are exactly what `unfollow_pilot`
   promises to keep visible; `FollowsStore.list_all()` + a per-row `get_mirrored`-style
   read, not `list_active()`) — build a raw per-symbol claim:
   `raw_claim[pilot_id][symbol] = min(mirrored_target_notional, position.market_value)`
   — the EXACT capping formula `pilots/mirror.py`'s own force-exit logic already uses
   (`pilots/mirror.py:409-413`), so this tool's definition of "how much of this holding
   is this Pilot's" is not a new invention, it's the system's existing definition,
   reused.
3. **Overlap normalization (the one new piece of logic, and the trickiest part of this
   tool):** for a symbol claimed by more than one Pilot, `sum(raw_claim[*][symbol])` can
   exceed `position.market_value` (independent claims, not a partition). Scale every
   Pilot's raw claim for that symbol by
   `min(1.0, position.market_value / sum(raw_claim[*][symbol]))` so the attributed
   total across all Pilots for one symbol never exceeds what is actually held — an
   honest normalization (labeled `overlap_scaled: true` on affected rows in the JSON
   payload), never a fabricated split.
4. `attributed_unrealized_pl[pilot_id][symbol] = (scaled_claim / market_value) *
   position.unrealized_pl` (exact given uniform `average_cost`, see above).
5. `unattributed_value[symbol] = position.market_value -
   sum(scaled_claim[*][symbol])` — the "manual trade / no follow claims this" bucket,
   surfaced as its own row, never silently dropped.
6. Per-Pilot totals: `attributed_market_value`, `attributed_unrealized_pl`,
   `attributed_unrealized_pl_pct` (weighted, i.e. `pl / value`, `None` if value is 0).

### Honesty labeling (CONSTRAINT #4)

* A top-level banner/JSON field: `"attribution_basis": "proxy"`,
  `"note": "Attribution is based on each follow's last target allocation "
  "(pilots.follows_store.FollowsStore.get_mirrored), capped by currently-held market "
  "value and scaled down where multiple Pilots claim the same symbol. This is NOT "
  "per-lot cost-basis P&L tracking — Stockpy does not record which Pilot originated "
  "a specific executed order."`
* `as_of`: `account_snapshot.fetched_at` + each pilot's `mirrored_updated_at` — so a
  stale attribution basis (e.g. a follow that hasn't rebalanced in weeks) is visible,
  not hidden.
* Never raises; a Pilot with an empty `mirrored` set (never planned, or a legacy row)
  is simply absent from the breakdown with `reason: "no attribution recorded for this
  follow"`, not zero-filled.

### Widget: recommend NO widget for v1

This is already the riskiest/most novel of the 4 tools (new normalization logic, a new
proxy concept). Keep it plain-markdown (tables: per-Pilot summary + per-symbol detail +
unattributed bucket) to avoid compounding review risk with a new widget template in the
same PR. A future PR can add a `pilot-portfolio.html` widget once the proxy math has
some real production mileage — flag this as a natural, but deliberately deferred,
follow-up.

### Signature, output shape

```python
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_portfolio_by_pilot() -> str:
```

* Markdown: `# Portfolio by Pilot (proxy attribution)`, the honesty banner, one `##`
  section per Pilot with `market_value`/`unrealized_pl`/`unrealized_pl_pct` + a
  per-symbol table, then `## Unattributed (no follow claims this)`.
* JSON payload: `{"as_of", "attribution_basis": "proxy", "note": "...", "pilots":
  [{"pilot_id", "pilot_name", "attributed_market_value", "attributed_unrealized_pl",
  "attributed_unrealized_pl_pct", "positions": [{"symbol", "attributed_value",
  "attributed_unrealized_pl", "overlap_scaled": bool}], "mirrored_updated_at"}],
  "unattributed": [{"symbol", "value"}]}`.

### Settings/classification implications

None of the 4 tools need a new `settings.py` field. `get_portfolio_by_pilot` reads
existing settings only indirectly (via `HistoricalStore`'s own
`HISTORICAL_STORE_ENABLED`/`ROBINHOOD_AUTO_REFRESH_ENABLED`, already live). No new
`docs/settings_liveness.json` keys are introduced, but see Verification below — the
file's per-key **line-number** annotations for pre-existing settings usages inside
`investyo_mcp_server.py` WILL shift once ~300-500 new lines are added, exactly as
PR #658 (`b16c0f33`) had to regenerate it for a 1-line diff.

---

## Ordered Implementation Sequence

1. **No settings changes** — skip straight to core logic.
2. **`unfollow_pilot`** (simplest, no new files) — add to
   `investyo_mcp_server.py`'s Pilots Marketplace section, immediately after
   `follow_pilot` (~line 3219). Add `tests/test_pilots_follows.py` coverage for the
   `upsert(0.0)`-preserves-`mirrored` contract if not already covered (it likely is,
   given the module docstring's explicit claim — verify, don't assume) plus a new
   server-level test in `tests/test_investyo_mcp_server.py`.
3. **`get_quote`** (simplest data-layer integration, no widget) — add near
   `get_ticker_context`/other Advisory & Market Intelligence tools, or inline in the
   Pilots section if preferred for PR locality; either is defensible, but grouping with
   the other market-data tools (not Pilots) better matches
   `docs/architecture/observability-and-apis.md`'s existing category grouping.
4. **`get_portfolio_by_pilot`** (new proxy-attribution logic, no widget) — implement the
   normalization algorithm as a **new pure function in `pilots/scoring.py` or a new
   `pilots/portfolio_attribution.py`** (mirroring `pilots/attribution.py`'s existing
   "pure function, caller supplies already-fetched inputs, no I/O" pattern —
   `pilots/attribution.py:1-57`), THEN a thin MCP-tool wrapper in
   `investyo_mcp_server.py` that fetches `account_snapshot` + all follow rows and calls
   it. Keeping the math in `pilots/` (not inline in the MCP tool body) matches this
   repo's established layering and makes it independently unit-testable without an
   MCP server in the loop, exactly like `pilots/attribution.py`'s two existing
   functions.
5. **`compare_pilots`** (widget work, most moving parts) — in this order:
   a. `mcp_widgets/templates/pilot-compare.html` (new file, copy `pilot-picker.html`'s
      skeleton).
   b. `_common.js`: add `renderComparePanel` + `renderEquityOverlaySvg`.
   c. `_common.css`: add `.compare-*` classes + the 3 ordinal color tokens.
   d. `mcp_widget_resources.py`: one new `_WIDGET_RESOURCES` tuple.
   e. `investyo_mcp_server.py`: `_PILOT_COMPARE_UI` constant + the `compare_pilots` tool
      itself.
   f. Rebuilding the vendored bundle is NOT needed (the ext-apps bundle itself is
      unchanged — only new template/CSS/JS text, substituted at read time by
      `render_widget_html`, same as the other 3 templates).
6. **Tests** (can interleave with 2-5, but land before calling any step "done"):
   * `tests/test_investyo_mcp_server.py` — one test class per new tool (function-call
     level, matching the file's existing convention of calling `@mcp.tool()`-decorated
     functions directly as plain Python).
   * `tests/test_investyo_mcp_tool_annotations.py` — extend with
     `compare_pilots`/`get_quote`/`get_portfolio_by_pilot` (`readOnlyHint=True`) and
     `unfollow_pilot` (must NOT have it), following the exact pattern PR #658 set.
   * `tests/test_investyo_mcp_widgets.py` — extend `TestToolMetaWiringConsistency`
     (or add a sibling class) for `_PILOT_COMPARE_UI` + the new template's
     placeholder-substitution round-trip, mirroring the existing 3-template coverage.
   * New `tests/test_pilots_portfolio_attribution.py` (or extend
     `tests/test_pilots_attribution.py`) for the overlap-normalization math in
     isolation — this is the one genuinely novel algorithm in this PR and deserves
     dedicated unit coverage (single-pilot claim, overlapping-pilot claim requiring
     scale-down, zero-market-value position, missing `mirrored` field, stale account
     snapshot).
   * Extend `tests/test_pilots_follows.py` if `upsert(0.0)`-preserves-`mirrored` isn't
     already pinned by an existing test.
7. **Docs**:
   * `docs/architecture/observability-and-apis.md` — extend the "(6) Pilots Marketplace"
     tool-inventory bullet (`docs/architecture/observability-and-apis.md:16`, the long
     paragraph enumerating the 6 existing tools) with the 4 new ones + a short note on
     the new `pilots/portfolio_attribution.py` (or wherever the math lands) and the new
     widget, following that paragraph's existing terse style. Also touch the widget
     bullet at line 12 to mention the 4th template.
   * Regenerate `docs/settings_liveness.json` (`python scripts/settings_liveness.py`,
     check `--help` for the exact write flag — PR #658's diff shows it's a
     line-number-only diff auto-generated from the file, not hand-edited) since new
     lines shift every existing `investyo_mcp_server.py:<N>` site reference in that
     file. Do this LAST, after all code changes land, so the line numbers are final.
8. **Verify** (see below).

---

## Verification

1. **Lint (genuine-bug rules only):**
   ```
   python -m ruff check . --select=F821,F822,F823,E9
   ```
2. **Offline test suite** (mirrors CI's `test` job):
   ```
   make ci
   ```
   i.e. `pytest -m "not network and not slow"` — must stay deterministic; none of the 4
   tools should need network/live-broker access to test (they all read
   already-persisted JSON/DB state, same as their siblings).
3. **Targeted test run while iterating**, before the full offline suite:
   ```
   pytest tests/test_investyo_mcp_server.py tests/test_investyo_mcp_widgets.py \
          tests/test_investyo_mcp_tool_annotations.py tests/test_pilots_follows.py \
          tests/test_pilots_scoring.py tests/test_pilots_performance.py \
          tests/test_pilots_mirror.py tests/test_pilots_attribution.py -v
   ```
4. **Widget smoke test** (new, mirroring `tests/test_mcp_oauth_flow_smoke.py`'s
   pattern of exercising the real flow headlessly rather than only unit-testing
   pieces): a small `TestPilotCompareWidgetSmoke` in `tests/test_investyo_mcp_widgets.py`
   that (a) runs the real `mcp_widgets/build/` npm build if the vendored bundle isn't
   present in CI (same conditional skip the existing widget tests already use for
   `TestRealBundleIfPresent`), (b) calls `mcp_widget_resources.render_widget_html
   ("pilot-compare.html")`, (c) asserts no leftover `__..._PLACEHOLDER__` tokens, (d)
   asserts the rendered HTML contains the new `renderComparePanel`/
   `renderEquityOverlaySvg` function names (a cheap "did the new JS actually make it
   into the bundle" check, same spirit as the OAuth smoke test's "did the actual token
   exchange happen" checks, scaled to this widget's much smaller surface — no real
   browser/DOM execution needed, matching this file's existing all-Python approach).
5. Do NOT run `make verify` (the deeper, live-broker-touching gate) unprompted — offer
   it to the operator per the `/verify` skill's own guidance, since `get_quote` and
   `get_portfolio_by_pilot` are the two tools in this batch that COULD, in principle, be
   exercised against a live provider/broker in that deeper gate.

---

## Branch / PR Strategy

**Recommendation: ship as 2 PRs on top of the existing `add-pilot-marketplace-tools`
branch/worktree, not 1 and not 4.**

* **PR A — `unfollow_pilot` + `get_quote` + `get_portfolio_by_pilot`** (no new widget
  assets, no npm build dependency, no `mcp_widgets/` touches). These three are
  independently reviewable, low-risk (no new UI surface to visually verify), and
  together read as one coherent "close the obvious gaps" change. `get_portfolio_by_pilot`
  is the one with real new algorithmic logic (the overlap-normalization), but it's still
  plain-markdown output and isolated to a new pure function + its own test file — a
  reviewer can evaluate its math without also reviewing a widget diff.
* **PR B — `compare_pilots`** (new widget template + `_common.js`/`_common.css`
  additions + `mcp_widget_resources.py` registration + the new SVG chart code).
  Reasoning to split THIS one out on its own, not bundled into PR A and not further
  split:
  * It is the only one of the 4 touching `mcp_widgets/` — a strictly different, more
    visual review (a reviewer needs to actually render the widget, per
    `verify-webapp`-style manual/browser verification, not just read a diff) than the 3
    plain-markdown tools.
  * It is the only one introducing genuinely new shared UI code
    (`renderEquityOverlaySvg`) that could regress the 3 EXISTING widgets if a shared
    `_common.js`/`_common.css` edit is careless — isolating it lets a bad widget-CSS
    change be reverted/bisected without touching the other 3 tools' unrelated logic.
  * It doesn't depend on PR A's tools or vice versa — no ordering constraint forces them
    together, and splitting reduces the blast radius of "the compare widget's SVG chart
    needs another design pass" blocking the 3 simpler, already-solid tools from
    shipping.

Do NOT further split PR A into 3 — `unfollow_pilot`/`get_quote`/`get_portfolio_by_pilot`
are small enough individually (and share no code with each other) that 3 separate PRs
would be process overhead without a corresponding review-risk reduction; they're already
independently testable within one PR via separate test classes/files.

---

## Critical Files for Implementation

- `investyo_mcp_server.py`
- `pilots/follows_store.py`
- `pilots/mirror.py`
- `pilots/performance.py`
- `pilots/scoring.py`
- `pilots/attribution.py`
- `data/market_data.py`
- `mcp_widget_resources.py`
- `mcp_widgets/templates/_common.js`
- `mcp_widgets/templates/_common.css`
- `mcp_widgets/templates/pilot-picker.html` (template to copy for `pilot-compare.html`)
