# Google Trends live viewer — Phases B + C interface contract

Every agent building Phases B/C codes against **this file**. If you need to change a name or a field, stop and report it instead of improvising: other agents are building against the same contract in parallel.

Design rationale: `.claude/google_trends_svi_live_implementation_plan.md` §3 (Phases B, C). Build order: `.claude/google_trends_svi_live_phase_bc_task.md`.

## Python: store read helpers (`data/trends_store.py`, methods on `TrendsStore`)

Each returns an empty value (`{}` or `[]`) on **any** exception, logs a warning, and never raises (same pattern as the existing `latest_batch`).

```python
def latest_batches(self) -> dict[str, dict[str, Optional[dict]]]
    # {term: {"latest": batch_dict | None, "complete": batch_dict | None}}
    # batch_dict = _batch_to_dict(row): batch_id, query_term, started_at, finished_at,
    #              windows_expected, windows_fetched, status, reason
def stitched_summary(self) -> dict[str, dict[str, Any]]
    # {term: {"days": int, "first_date": date, "last_date": date, "stitched_at": datetime}}
def get_stitched_rows(self, query_term: str) -> list[dict]
    # [{"date": date, "value": float, "stitched_at": datetime}, ...] ordered by date asc
```

## Python: view builders (new `data/trends_view.py`)

Pure. Reads the store only, never imports pytrends or anything from `api/`.

```python
TERM_RE: re.Pattern          # r"^[A-Z0-9.\-]{1,12}$"
STALE_INTERVALS: float = 2.0
ASVI_LOOKBACK_DAYS: int      # inspect.signature(ASVICalculator.compute_asvi).parameters["lookback_weeks"].default * 7  (=84)

def config_echo() -> dict                                    # the CONFIG fields below
def empty_svi(term: str, status: str, reason: str | None) -> dict   # full SVI shape, empty lists, None objects
def build_terms_view(store, tracked_universe: Iterable[str] | None, *, now: datetime | None = None) -> dict
def build_svi_view(store, term: str, *, now: datetime | None = None) -> dict   # term already validated + upper-cased
```

- Both builders catch every exception and return `status: "error"`. The `reason` names the exception **type** only, never the message.
- `now` is naive UTC; the default is `datetime.now(timezone.utc).replace(tzinfo=None)`.

## HTTP (api/data_api.py, port 8603, `dependencies=[Depends(require_token)]`, output through `_clean_nan`)

- `GET /data/trends/terms` → `TrendsTermsResponse`. The universe for coverage comes from the module's own `get_universe()["effective_symbols"]`, inside a try (`None` on failure). If opening `TrendsStore(readonly=True)` fails, return `status:"error"`.
- `GET /data/trends/svi?term=X` → `TrendsSviResponse`. `term` is stripped and upper-cased. A `TERM_RE` miss raises `HTTPException(422, detail="invalid_term: …")`. A valid term with no data is **200**, never 404.

## JSON payloads (field names are exact; TS types mirror them 1:1)

Encodings:
- Datetimes: ISO 8601 UTC with `Z`, e.g. `"2026-10-11T12:00:00Z"`.
- Dates: `"YYYY-MM-DD"`.
- Points: `[epoch_ms_at_utc_midnight, value_or_null]`.
- Any number that cannot be computed is `null`, never `0`.

**CONFIG fields** (in both responses):
`enabled: bool, window_days: int, overlap_days: int, refresh_interval_hours: float, max_terms_per_pass: int, retention_batches: int, stale_after_hours: float, asvi_lookback_days: int`

**Batch** (`TrendsBatch`):
`batch_id: str, status: "complete"|"partial"|"failed", reason: str|null, started_at: str|null, finished_at: str|null, windows_expected: int|null, windows_fetched: int|null, age_hours: float|null, stale: bool|null`
(`age_hours` is measured from `finished_at`, falling back to `started_at`. `stale` = `age_hours > stale_after_hours`; `null` when age is null.)

**TrendsTermsResponse** = CONFIG +
```
status: "ok" | "no_data" | "disabled" | "error"
reason: str | null
tracked_universe_size: int | null
tracked_with_data: int | null        # tracked terms with stitched_days > 0
terms: TrendsTermRow[]               # union of batch terms and stitched terms, sorted by term
```
- `no_data`: no terms and refresh is enabled. `disabled`: no terms and refresh is disabled.

**TrendsTermRow**:
```
term: str
in_tracked_universe: bool | null     # null when the universe is unknown
last_attempt_at: str | null          # latest batch (any status) started_at
last_attempt_status: "complete"|"partial"|"failed"|null
last_attempt_reason: str | null
last_complete_at: str | null         # latest complete batch finished_at
age_hours: float | null              # from last complete batch, else from stitched_at
stale: bool | null
stitched_days: int                   # 0 when no stitched series (a real count, not a placeholder)
last_date: str | null                # last stitched date
latest_asvi: float | null            # compute_asvi(stitched).dropna().iloc[-1]
latest_asvi_date: str | null
has_complete_batch: bool
```

**TrendsSviResponse** = CONFIG +
```
term: str
status: "ok" | "stitched_only" | "partial_only" | "no_data" | "disabled" | "error"
reason: str | null
batch: TrendsBatch | null            # latest COMPLETE batch
latest_attempt: TrendsBatch | null   # latest batch of any status
raw_windows: TrendsRawWindow[]
seams: TrendsSeam[]
rescaled_windows: { index: int, points: Point[] }[]
stitched: TrendsStitched | null
recompute_check: TrendsRecomputeCheck | null
asvi: TrendsAsvi | null
```

Status rules, checked in order:
1. `no_data` / `disabled`: no batch and no stitched rows. `disabled` only when `enabled` is false.
2. Else compute `stitched` + `asvi` if stitched rows exist.
3. No complete batch: `stitched_only` if there are stitched rows, else `partial_only`. Both carry an explanatory `reason`.
4. Else `ok`. Build `raw_windows`, `seams`, `rescaled_windows` and `recompute_check` from the complete batch's raw rows: `GoogleTrendsStitcher.group_raw_windows_into_series(store.load_batch_windows(batch_id))`. If there are no raw rows, return `error` with a reason.
5. Any exception: `error`.

Stored data is still returned when `enabled` is false, as `ok`/`stitched_only` with `enabled:false`.

**TrendsRawWindow**: `index: int, start: str, end: str, points: Point[], peak_date: str|null, peak_value: float|null`

**TrendsSeam**: `index: int (1-based, = b_window), a_window: int, b_window: int, overlap_start: str, overlap_end: str, overlap_days: int, sum_a: float|null, sum_b: float|null, applied_factor: float|null, pair_factor: float|null, degenerate_guard: bool`
- Chain walk: `stitched = windows[0]`, then for each `k ≥ 1`:
  - `meta = get_scaling_metadata(stitched, windows[k])`
  - `sum_a`/`sum_b` = overlap sums of `stitched` and `windows[k]` over `meta["overlap_dates"]`
  - `applied_factor = meta["f"]`
  - `degenerate_guard = sum_a <= 1e-9 or sum_b <= 1e-9`
  - `pair_factor` = Σ`windows[k-1]` / Σ`windows[k]` over their own overlap, or `null` if either sum ≤ 1e-9
  - `rescaled[k] = windows[k] * meta["f"]`
  - `stitched = stitch_intervals(stitched, windows[k])`
  - `rescaled[0] = windows[0]`

**TrendsStitched**: `points: Point[], first_date: str, last_date: str, days: int, persisted_at: str|null, from_latest_batch: bool` (`from_latest_batch` is true only when every row's `stitched_at` equals the complete batch's `finished_at`).

**TrendsRecomputeCheck**: `compared_days: int, max_abs_diff: float|null, same_dates: bool, matches: bool` (`matches` = `same_dates and max_abs_diff < 1e-9`). Only set when status is `ok` and stitched rows exist.

**TrendsAsvi**: `points: Point[], latest: float|null, latest_date: str|null, lookback_days: int` (warm-up points have `null` values).

## Frontend names

- `webapp/src/api/types.ts`: `TrendsStatus`, `TrendsTermsStatus`, `TrendsBatchStatus`, `TrendsPoint = [number, number | null]`, `TrendsConfig`, `TrendsBatch`, `TrendsTermRow`, `TrendsTermsResponse`, `TrendsRawWindow`, `TrendsSeam`, `TrendsRescaledWindow`, `TrendsStitched`, `TrendsRecomputeCheck`, `TrendsAsvi`, `TrendsSviResponse`.
- `client.ts` / `mock.ts` (`liveApi`/`mockApi`): `getTrendsTerms(): Promise<TrendsTermsResponse>`, `getTrendsSvi(term: string): Promise<TrendsSviResponse>`.
- Mock terms:
  - `AAL`: ok
  - `ABR`: ok + stale
  - `AGNC`: stitched_only
  - `AM`: ok + recompute mismatch
  - `AQN`: ok + one degenerate seam
  - `ARCC`: partial_only
  - `ZZZZ`: disabled-style. Note that mock can't flip `enabled` per call, so `getTrendsSvi("ZZZZ")` returns `status:"disabled"`.
  - `ERR`: error
  - anything else: `no_data`
- Chart components (flat in `webapp/src/components/`, import helpers from `./charts`):
  - `TrendsRawWindowsChart({ windows: TrendsRawWindow[] })`
  - `TrendsStitchChart({ rescaled: TrendsRescaledWindow[], stitched: TrendsStitched | null, seams: TrendsSeam[] })`
  - `TrendsAsviChart({ asvi: TrendsAsvi | null })`
  - `TrendsSeamsTable({ seams: TrendsSeam[] })`
- Screen: `webapp/src/screens/TrendsAttention.tsx`, exporting `function TrendsAttention()`. Route `/research/trends`. `TAB_HELP` key `trends`.
