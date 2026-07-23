# Feature: Sector Heat Factor

**File:** `data/sentiment_sources.py` (`GDELTVolumeSource`, `compute_sector_heat_factors`)
**Wiring:** `pipeline/production_steps.py::_apply_sector_heat_factor` (called from `StrategyEvalStep.run`)
**Column:** `Sector_Heat_Factor` (`config.COLUMN_SCHEMA`, `"format": "number"`)
**Master switch:** `settings.SECTOR_HEAT_ENABLED` (default `False`)

**This is NOT a registered `SignalModule`.** It ships as a dashboard feature
COLUMN only — it does not appear in `settings.SIGNAL_WEIGHTS`, is not listed
in `docs/signals/README.md`'s module index, and does not feed
`StrategyEngine.evaluate_security()`'s weighted score in any way. Wiring it
into scoring is explicitly deferred to a future iteration. This file lives
under `docs/signals/` because it is a per-sector, GDELT-sourced attention
feature that reads naturally alongside the sentiment-pipeline docs, not
because it is one of the 17 scored modules.

---

## Rationale

**Fu & Zhang (2024)** (referenced by this repo's existing sentiment-pipeline
research notes) find that abnormal news/comment **volume** — independent of
the tone/sentiment of that coverage — is itself a leading attention signal:
a sector attracting unusually heavy media coverage tends to see elevated
retail and institutional attention shortly after, regardless of whether the
coverage itself is bullish or bearish. This is distinct from (and
complementary to) `GDELTSource`'s existing tone-based sentiment ingestion in
this same file, which scores what the coverage *says*, not how much of it
there is.

The Sector Heat Factor operationalizes this as a per-**sector** (not
per-ticker) GDELT article-volume time series, Gaussian-smoothed to separate
a genuine attention-regime shift from single-day news noise, with "today's"
smoothed value surfaced as the `Sector_Heat_Factor` column for every ticker
in that sector.

## Why per-sector, not per-ticker

GDELT's free DOC 2.0 API has no authenticated quota, so it is rate-limited
by courtesy rather than a published per-key ceiling. Querying per TICKER
would mean one call per symbol in the universe every cycle (potentially
hundreds). Querying per SECTOR bounds the call count to the number of
distinct GICS sectors actually present in the current universe — single
digits up to the ~11 standard GICS sectors — regardless of how many tickers
are being tracked. `compute_sector_heat_factors()` enforces this: it takes a
list of sectors (already deduplicated by the caller,
`pipeline/production_steps.py::_apply_sector_heat_factor`, from
`dashboard_df['sector'].unique()`), not a list of tickers, and issues
exactly one GDELT `mode=timelinevol` call per distinct sector.

## API shape

`GDELTVolumeSource.fetch_daily_counts()` calls GDELT's DOC 2.0 API
(`https://api.gdeltproject.org/api/v2/doc/doc`, no auth) with
`mode=timelinevol` — this returns the WHOLE requested date range's volume
time series in a single response, unlike the existing `GDELTSource`'s
`mode=artlist` (which caps at 250 records/call and needs the windowed-
backfill pattern documented on that class). This is what keeps the call
count at exactly one call per sector rather than one call per sector per
day. The response's `timeline[0].data` points (`{"date": ..., "value": ...}`)
are bucketed to one point per calendar day (summing any sub-daily-resolution
points GDELT returns for short spans) and returned as an ascending
`{"YYYY-MM-DD": volume}` dict.

## Gaussian smoothing

The raw daily article-count series is smoothed with
`scipy.ndimage.gaussian_filter1d(series, sigma=settings.SECTOR_HEAT_SMOOTHING_SIGMA)`
before "today's" (the series' last point) value is taken as the sector's
heat factor. A single day of anomalous coverage (one viral story) should not
by itself register as a sustained attention shift; the Gaussian kernel
weights nearby days more heavily than distant ones, producing a continuous
estimate of "current attention level" rather than a noisy point sample.
Higher `SECTOR_HEAT_SMOOTHING_SIGMA` trades responsiveness for smoothness.

## Settings

| Setting | Default | Effect |
|---|---|---|
| `SECTOR_HEAT_ENABLED` | `False` | Master gate. `False` is a complete no-op — `compute_sector_heat_factors()` returns `{}` immediately with **no network call**, and `Sector_Heat_Factor` stays `NaN` for every row (byte-identical to the pre-existing PR #416 placeholder behavior). |
| `SECTOR_HEAT_SMOOTHING_SIGMA` | `1.0` | Gaussian smoothing sigma applied to each sector's raw daily article-count series. |
| `SECTOR_HEAT_LOOKBACK_DAYS` | `7` | Calendar days of GDELT article-volume history requested per sector per cycle. |

## Causality (no lookahead)

`GDELTVolumeSource.fetch_daily_counts(query, since, until)` sends `until` as
the query's `enddatetime` parameter to GDELT — the API itself is never asked
for anything past that instant, so the query is causal by construction, not
by client-side filtering alone. As a belt-and-suspenders defense, any
timeline point GDELT might still return dated after `until` (a malformed or
buggy response) is dropped before it can enter the smoothed series. See
`tests/test_sector_heat_lookahead.py` for the perturbation test proving a
point dated after the cycle's as-of time never changes "today's" computed
heat value.

## Failure modes (dead-letter, never fabricated)

Per CONSTRAINT #4/#6, every layer degrades to `NaN`/`{}`/`[]` rather than
raising or fabricating a value:

- `SECTOR_HEAT_ENABLED=False` → `Sector_Heat_Factor` stays `NaN` for every
  row, no network call.
- A sector's GDELT call fails (network error, non-2xx, malformed JSON) →
  that sector is simply absent from `compute_sector_heat_factors()`'s
  returned dict; `pipeline/production_steps.py`'s
  `dashboard_df['sector'].map(sector_heat_map)` then leaves every ticker in
  that sector `NaN` (pandas `Series.map` returns `NaN` for an unmapped key)
  — other sectors' values are unaffected.
- A ticker with no `sector` value (`NaN`/`""`/`"Unknown"`) is excluded from
  the sector list built for the GDELT query entirely, and therefore always
  reads `NaN` in this column.
- Any other exception anywhere in `_apply_sector_heat_factor()` — including
  a raised exception on the `compute_sector_heat_factors()` call itself — is
  caught and the WHOLE column is reset to `NaN`, never left partially
  populated with the exception surfaced to the caller.

## Where it's surfaced

- `config.COLUMN_SCHEMA` — `Sector_Heat_Factor` column (Google Sheets +
  Pandera-validated `dashboard_df`).
- `main_orchestrator.py::_write_state_snapshot()` — per-signal
  `sector_heat_factor` key in `output/state_snapshot.json`, same
  `_safe_float_or_none` NaN→JSON-`null` convention as the neighboring
  `multifactor_composite`/`value_z`/etc. fields, for the GUI/webapp to
  eventually read.

Not wired into: `signals/` package, `settings.SIGNAL_WEIGHTS`,
`StrategyEngine.evaluate_security()`'s weighted score, any GUI panel, or the
Pilots PWA. All of those are explicitly out of scope for this first cut.
