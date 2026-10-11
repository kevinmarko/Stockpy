# Google Trends store: stacked downloads, stale-scale tail and partial current day (2026-10)

**Status:** Fixed in code (branch `claude/google-trends-svi-live-drw74r`, Phase A of
`.claude/google_trends_svi_live_implementation_plan.md`). The live DB cleans itself on the
first complete download per term after the daemon restarts on the new code (see "Rollout").

## Symptom

The removed Trends stitching demo (#953) looked stale and broken against real data, even
though the daemon was fetching. A read-only look at the operator's DB on 2026-10-06 showed
why.

| Measurement | Value |
|---|---|
| Terms with data | 6 (AAL, ABR, AGNC, AM, AQN, ARCC) |
| Distinct raw `window_id`s for AAL | 96 (8,336 rows): 16 downloads of 6 windows, all kept |
| Refresh passes on 2026-10-06 | 3 (05:36, 08:21, 11:13) despite a 24 h interval |
| Stitched AAL days | 402: 366 from the latest stitch + 36 left over from older stitches |
| AAL 2026-10-05 across downloads | 45 → 66 → 80 during the day, 78 the next day |
| AAL 2026-10-03 (a complete day) across downloads | 78 / 79 / 80 / 81 |

## Root causes

1. **No notion of a download.** Each window got a random `uuid4` id, and nothing tied the 6
   windows of one download together. Rows were never replaced or pruned, so the table grew
   by about 540 rows per term per pass, and a reader could not tell which windows belonged
   together.
2. **Stale-scale tail.** Google normalizes each window to its own max of 100, so every stitch
   has its own scale. `save_stitched_series` upserted only the new stitch's 365 days. Days
   that had rolled out of the window kept an older stitch's scale, which is a level step in
   the stored series.
3. **Partial current day.** Google reports today's value while the day is still
   accumulating, and pytrends marks it `isPartial`. The client dropped that flag. The
   dashboard's `Google_Trends_ASVI` reads the **last** stored day, so it was usually
   computed from a partial, downward-biased value.
4. **In-memory throttle.** `_last_google_trends_refresh` lived in process memory, so every
   daemon restart triggered a full refetch. That means more 429s, and later terms in the
   pass failed more often (AAL 96 windows → ARCC 60).
5. **Settings ignored.** The daemon never passed `GOOGLE_TRENDS_WINDOW_DAYS` /
   `GOOGLE_TRENDS_OVERLAP_DAYS`. The function defaults happened to match.
6. **Universe.** The daemon used `DEFAULT_TICKERS`, not the tracked universe that CLAUDE.md
   requires (`compute_tracked_universe()`).
7. **ASVI warm-up lookahead.** `ASVICalculator.compute_asvi` filled the first 7 rows with the
   median of the first 84 days, which includes future dates.

## Fix

- New table `trends_download_batches`: one row per term per download, with `status`
  (`complete`/`partial`/`failed`) and a `reason`. Raw `window_id` is now
  `"<batch_id>/<start>/<end>"` (no schema change to `raw_trends_downloads`).
- Only a complete batch is stitched, and a batch is recorded `complete` only after its stitch
  produced rows. The stitch replaces the term's series in one transaction
  (`save_stitched_series(..., replace=True)`, which refuses an empty series). A cooldown that
  is already open stops the pass before any fetch.
- `fetch_overlapping_windows` drops `isPartial` rows.
- The schedule is per term and comes from the batch table (`TrendsStore.last_attempts()`),
  so it survives restarts. A term waits the full refresh interval after any attempt, including
  a failed one, and an exception is recorded as a `failed` batch, so a dead or erroring term is
  never retried on every wake.
- Window settings are passed through. The universe is `compute_tracked_universe()` (cached
  broker snapshot only), capped by `GOOGLE_TRENDS_MAX_TERMS_PER_PASS` (default 10) per pass,
  oldest attempt first (round-robin).
- Raw retention: `GOOGLE_TRENDS_RAW_RETENTION_BATCHES` (default 14) batches per term of each
  kind (complete; partial/failed), pruned after every batch.
  Pre-fix `uuid4` rows are pruned once a term has its first complete batch.
- ASVI warm-up rows are NaN.

## Behavior changes the operator should expect

- **The dashboard `Google_Trends_ASVI` value changes:** it is now the ASVI of the last
  *complete* day, not today's partial value. This was accepted (plan Decision 3).
- More terms get Trends data (the tracked universe instead of `DEFAULT_TICKERS`), at most
  10 new downloads per timer wake.
- A term with 7 or fewer stored days shows NaN instead of a value built on future data.

## Rollout

No migration. On the first wake after a restart with `GOOGLE_TRENDS_ENABLED=true`:
`create_all` adds the batch table, every term counts as never downloaded, and up to 10 are
fetched. Each complete batch replaces that term's stitched series (removing the orphan
tail) and prunes its old `uuid4` raw rows. No one-off cleanup script was written: the
operator chose to let the re-stitch clean up (plan Decision 7).

Verification queries after a day of running (read-only):

```sql
-- one complete batch per term per refresh interval
SELECT query_term, status, COUNT(*), MAX(started_at) FROM trends_download_batches GROUP BY 1, 2;
-- no legacy raw rows left for terms that have a complete batch
SELECT query_term, COUNT(*) FROM raw_trends_downloads WHERE window_id NOT LIKE '%/%' GROUP BY 1;
-- latest stored day is before today (no partial day)
SELECT query_term, MAX(date), COUNT(*) FROM stitched_google_trends GROUP BY 1;
```

## Still open

- No maximum-age check on the dashboard ASVI. If downloads keep failing, the last stored
  value is shown as current. The planned viewer (Phase B/C) will show batch age.

## Tests

`tests/test_trends_store.py`, `tests/test_google_trends_client.py`,
`tests/test_google_trends_daemon.py`, `tests/test_trends_stitcher.py`,
`tests/test_production_steps_google_trends.py`.
