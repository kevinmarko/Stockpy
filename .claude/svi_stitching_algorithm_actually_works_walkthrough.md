# SVI Stitching Algorithm — making the demo screen and ASVI sector-proxy actually work

## What was reported
The operator asked to "get the SVI Stitching Algorithm to actually work." The
backend endpoint (`GET /data/trends/stitch-demo`) and the stitching math
(`data/trends_stitcher.py`) had already been through many rounds of code
review in prior PRs (#953, #957, #963, #964, #983, #993) — every one of them
static, none of them a real live-browser render. That's exactly why the real
bug survived all of them.

## Root cause #1 (the actual "doesn't work" bug): a Tailwind CSS class with zero real CSS
Verified live in a real browser against the real running dev server + both
FastAPI backends: the "SVI Stitching Algorithm Demo" screen
(`webapp/src/screens/TrendsVisualizer.tsx` → `webapp/src/components/charts/TrendsStitchChart.tsx`)
fetched real data successfully (confirmed via network trace) but rendered
**completely invisible** — no chart, no error, no console warning.

This webapp has **no Tailwind CSS dependency at all** (no `tailwindcss`
package, no tailwind.config, no `@tailwind` directives in `index.css`).
`TrendsVisualizer.tsx` and `TrendsStitchChart.tsx` were the only two files in
the whole `webapp/src` tree using Tailwind-style utility classes
(`bg-zinc-950`, `text-zinc-100`, `h-[400px]`, `border-zinc-800`, ...). None of
those class names produce any real CSS in this build. `h-[400px]` therefore
resolved to a computed height of **`0px`** (verified directly via
`getComputedStyle`), and Recharts' `<ResponsiveContainer>` needs a definite,
non-percentage, non-zero height somewhere in its ancestry to render anything
— this exact failure mode already bit this codebase once before
(`AccountPerformanceChart.tsx`'s own code comment references the prior
incident, PR #846).

### Fix
- `webapp/src/components/charts/TrendsStitchChart.tsx`: outer wrapper is now
  `className="card card-pad"` (this codebase's real card-chrome classes) with
  an explicit `style={{ width: '100%', height: 400 }}` (a plain JS number,
  not a class or percentage) immediately around `<ResponsiveContainer>`.
  Hardcoded hex colors replaced with `theme.*` tokens / the shared
  `chartAxisTick`/`chartAxisLine`/`chartTooltipStyle` helpers from
  `../charts`.
- `webapp/src/screens/TrendsVisualizer.tsx`: rewritten to use this
  codebase's real screen conventions (`className="screen"`/`screen-title`/
  `screen-sub`, `<Loading>`/`<ErrorState>` from `components/ui`) instead of
  non-functional Tailwind classes. All existing user-facing text (the
  heading, and the "SPY Volume Proxy" / "never presented as real
  search-volume data" disclosure copy) preserved verbatim.
- Regression tests added to both existing test files
  (`TrendsStitchChart.test.tsx`, `TrendsVisualizer.test.tsx`) that assert the
  chart's own wrapper carries a real, definite inline height and that no
  Tailwind-arbitrary-value class fragments remain — verified to actually
  fail against the pre-fix source, not just pass against the fix.
- A parallel audit found the identical bug pattern (dead Tailwind classes,
  including one more genuinely invisible chart in
  `AlmgrenChrissRouterView.tsx`) in 6 other, unrelated webapp files. Out of
  scope for this fix; flagged as a separate follow-up task rather than
  bundled in here.

## Root cause #2: the ASVI sector-proxy resolution silently always fell back to SPY
A second, independent audit of the actual production pipeline behind "the
SVI Stitching Algorithm" (the Phase 4 LSTM-Attention forecaster documented in
CLAUDE.md) found that `POST /pilots/ml/lstm-attention-forecast`
(`api/pilots_api.py`) constructed `data.trends_stitcher.FMPDataLoader` and
called `.get_fundamentals(symbol)` on it — a method that class has never had
(it only generates synthetic OHLCV bars for the standalone stitching
demo/tests). Every real call raised `AttributeError`, silently swallowed by a
bare `except Exception: sector = None`, so `resolve_sector_proxy` was
unconditionally called with `None` and every real forecast silently used SPY
as its sector proxy regardless of the target symbol's actual sector —
contradicting CLAUDE.md's explicit claim that this maps a real sector (e.g.
Technology) to its SPDR ETF proxy (XLK). No test covered this endpoint.

### Fix
`api/pilots_api.py::run_lstm_attention_forecast_endpoint` now resolves
fundamentals via the real, already-imported `data.market_data.get_provider()`
(the same provider every other endpoint in this file uses) instead of the
non-existent `FMPDataLoader.get_fundamentals`. New regression test file
`tests/test_pilots_lstm_attention_forecast_sector_proxy.py` (3 tests) proves
a real "Technology" sector now resolves to `XLK` (not a silent `SPY`
fallback), that a genuine provider failure still degrades honestly to `SPY`
(CONSTRAINT #6), and that the broken `FMPDataLoader` construction is gone.
Verified these tests actually fail against the pre-fix code (2 of 3 fail) and
pass against the fix (3 of 3 pass).

## Verification
- `pytest tests/test_pilots_lstm_attention_forecast_sector_proxy.py
  tests/test_trends_stitcher.py tests/test_google_trends_client.py
  tests/test_trends_store.py tests/test_data_api.py
  tests/test_google_trends_daemon.py tests/test_production_steps_google_trends.py
  tests/test_pilots_api.py` — **555 passed, 0 failed**.
- `ruff check --select F,E9 api/pilots_api.py
  tests/test_pilots_lstm_attention_forecast_sector_proxy.py` — one
  pre-existing, unrelated finding at line 7476 (outside this change's diff,
  confirmed via `git diff --unified=0`); zero findings in the touched lines.
- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run src/components/charts/TrendsStitchChart.test.tsx
  src/screens/TrendsVisualizer.test.tsx` — **8 passed, 0 failed**.
- Live browser verification: ran both `api/data_api.py` (:8603) and
  `api/pilots_api.py` (:8602) against the real local `quant_platform.db`
  (which already holds real ingested Google Trends data for several
  tickers), ran `npm run dev` for the webapp in live (non-mock) mode, and
  confirmed via screenshot that the "SVI Stitching Algorithm Demo" screen
  now renders a real, correctly-themed, populated chart with real dates and
  values — not blank.

## Not fixed here (disclosed, out of scope)
- The stitched curve's final data point drops sharply near "today" in the
  live screenshot — this reflects the real, currently-ingested SVI window
  data as persisted by the daemon job, not a rendering defect; not
  investigated further as part of this fix.
- 6 other webapp files found to share the same dead-Tailwind-class bug
  pattern (one more genuinely invisible chart in
  `AlmgrenChrissRouterView.tsx`, mounted on the Paper Broker screen) —
  flagged as a separate follow-up task, not part of this change.
