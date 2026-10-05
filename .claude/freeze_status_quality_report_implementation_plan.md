# Freeze status "trade quality" report - implementation plan

Status: PLAN ONLY (phase 1). No code edited.
Scope: observability-only extension of `scripts/feature_freeze_status.py` (allowed under the step-7 freeze).
Branch when built: `freeze-status-quality-report` (lowercase-kebab, PR; it is script code, so not the direct-to-main tier).

## 1. Why

The gate counts closed `main_pipeline` trades, but those will be dominated by quick round trips (the one closed pipeline trade, SPY, was held 0.96 days; a replay of 805 snapshots gave median hold 1.9 days with about 40% under 1 day). The conviction positions (ARR/PK/DX/AGNC, held BUY since 9/14) stay open and never count. Entries are clumped (8 of 9 buys on 2026-09-30) and sector-concentrated (REITs/high-yield). Reaching 30 may therefore say little about the pipeline's real edge. The report makes that visible without changing the gate.

## 2. Hard constraints

- Gate semantics unchanged: `closed_pipeline_trades >= --min` -> exit 0, else exit 2. All existing JSON keys keep names, types and values. New data is additive, under a new top-level `"quality"` key.
- The quality block is computed in its own try/except. On failure: `"quality": {"error": "<reason>"}` and the exit code is unaffected. The gate path must not depend on it.
- Read-only: no writes, no network by default, DB opened via `PaperAccountStore(readonly=True)` / `db_config.create_readonly_db_engine` (SQLite `?mode=ro`).
- CONSTRAINT #4: unmeasurable -> `None` in JSON, `n/a` / `unavailable` in text. Never 0, never cost-basis-as-mark.

## 3. Data-source findings (verified)

| Need | Source | Network? | Notes |
|---|---|---|---|
| Closed trades | `store.get_full_closed_trades(limit=1_000_000)` (already used) | no | Has `holding_period_days` (nullable), `entry_ts`, `exit_ts` (ISO UTC), `close_reason` (NOT NULL), `symbol`, `realized_pnl`. |
| Open positions (qty, avg entry, entry_ts) | `PaperPosition` rows via `store.Session` filtered `qty != 0`, `strategy_id == main_pipeline` | no | `open_position_symbols()` returns only symbols. `entry_ts` is nullable (legacy rows) -> days_held None. |
| Marks, existing convention | `get_open_positions()` -> `_resolve_position_prices()` -> `_fetch_stock_prices()` -> `pilots.price_provider.get_latest_prices` (FMP, yfinance fallback) | YES | Also falls back to `avg_entry_price` when a quote is missing (flagged only via `mark_is_estimated`), so reusing it would silently report unrealized P&L = 0 for unpriced names. The existing test pins that `main()` never calls `get_open_positions`. Do NOT use it. |
| Marks, offline | raw read-only SQL on `price_bars` (`close`, latest `date` per symbol) in the same DB (live DB confirmed: 635k rows, latest 2026-10-02, has ARR/PK/DX/AGNC) | no | Do NOT use `HistoricalStore.get_bars` (it tops up from the provider over the network and may create tables). Use `close`, not `adj_close`, since the entry fill is an unadjusted quote. |
| Sector | latest `fundamentals_history.raw_json` -> `sector` per symbol (raw read-only SQL) | no | Verified live for all 8 open pipeline symbols plus SPY (Real Estate x4, Financial Services x2, Energy x2). `industry` is absent, so sector only. Missing row/key -> `"unavailable"`. `sector_snapshots` is keyed by sector name, not symbol, so unusable here. |

## 4. Output fields (added under `"quality"`)

```
quality: {
  holding_period: {
    n_closed: int, n_measured: int, n_unmeasured: int,   # measured = holding_period_days, else exit_ts-entry_ts, else unmeasured
    median_days: float|None, mean_days: float|None,
    share_under_1d: float|None,                           # over measured only
    buckets: {"<1d": n, "1-3d": n, "3-7d": n, ">=7d": n}
  },
  close_reasons: {"flatten": n, ...},                    # Counter over own closed trades; missing -> "unavailable"
  entries: {
    distinct_entry_dates: int|None,                      # closed+open pipeline trades, date(entry_ts) in America/New_York
    entries_per_date: {"2026-09-30": 8, ...},
    max_entries_single_date: int|None,
    max_single_date_share: float|None,
    n_entries_without_ts: int                            # excluded, counted
  },
  open_positions: [ {
    symbol, side, qty, avg_entry_price, entry_ts, days_held|None,
    mark_price|None, mark_source: "stored_close"|"live_quote"|"unavailable",
    mark_date|None, mark_age_days|None,
    unrealized_pnl|None, unrealized_pnl_pct|None, sector
  } ],
  open_summary: { n_open, n_marked, n_unmarked, total_unrealized_pnl|None (sum over marked only, with n_marked), median_days_held|None },
  sector_concentration: {
    basis: "cost_basis_notional", by_sector: {sector: {n_positions, share}}, top_sector, top_sector_share|None,
    n_unknown_sector, closed_trades_by_sector: {...}
  },
  closed_vs_open: { closed_pipeline_trades, open_pipeline_positions }  # makes the survivorship skew explicit
}
```

Rules: option symbols (`_is_option_symbol`) are not marked (`unavailable`). Shorts use the sign convention in `get_open_positions` (`(avg-mark)*abs(qty)`). A stale mark (`mark_age_days` > 4) is still shown but flagged via `mark_age_days`; never hidden. `total_unrealized_pnl` is None when n_marked == 0 and excludes unmarked rows (the count is shown next to it).

Opt-in `--live-quotes` (default off): marks via `pilots.price_provider.get_latest_prices` directly (not `_resolve_position_prices`); symbols absent from the result get `None`, never cost basis; any exception -> all `unavailable`. Text output says which source was used.

## 5. File changes

1. `scripts/feature_freeze_status.py`
   - Keep `summarize()` signature and output keys exactly. Add pure helpers: `holding_period_stats(own_closed)`, `close_reason_counts(own_closed)`, `entry_date_stats(own_closed, open_rows)`, `mark_open_positions(rows, price_lookup, now)`, `sector_concentration(open_marked, closed, sector_of)`, `build_quality(...)`.
   - Add `--live-quotes` flag; add `quality` to `--json`, and a short "Quality" section to text output (hold median / share <1d, close reasons, distinct entry dates and clump max, open P&L table, top sector share). Add a one-line caveat when `share_under_1d` is high or open positions outnumber closed.
   - New private readers: `_read_open_pipeline_rows(store)` (Session + `PaperPosition`, DB only), `_read_stored_closes(engine, symbols)`, `_read_sectors(engine, symbols)`; each returns `{}` on any error or missing table (`inspect().has_table`).
   - `main()`: gate computed first and exit code decided from it; the quality block is wrapped in try/except.
2. No change to `data/paper_account_store.py` (avoids touching the execution store). If the reviewer prefers a store method for the open-row read, add `get_open_position_rows(strategy_id)` DB-only, readonly-safe like `open_position_symbols`.
3. `tests/test_feature_freeze_status.py` extended (see section 6).

## 6. Tests (existing pattern: pure `summarize` tests plus a monkeypatched `main` test)

- Existing tests unchanged and still green (proves key compatibility).
- Hold stats: median/mean/share<1d on a known set; `holding_period_days=None` falls back to ts diff; both missing -> unmeasured, counted not zeroed; empty -> all None; bucket edges (exactly 1.0, 3.0, 7.0).
- Only `main_pipeline` rows feed quality fields (manual/hedge rows ignored).
- Close-reason counter, including missing -> `unavailable`.
- Entry dates: 8 same-day + 1 other -> distinct 2, max 8, share 8/9; ET date-boundary case (UTC 02:00 belongs to the prior ET date); NULL `entry_ts` excluded and counted; empty -> None.
- Marking: stored close present -> pnl/pct right for long and short; no bars -> all None and `unrealized_pnl` None (not 0, not cost-basis); option symbol -> unavailable; partial marking -> sum over marked with n_marked; stale `mark_age_days` reported.
- Sector: shares sum to 1 over known; missing sector -> `unavailable` and `n_unknown_sector`; top sector share; none known -> None.
- Gate invariance: parametrized test that `main()` exit code and `freeze_can_end` are identical with quality block succeeding vs. monkeypatched `build_quality` raising (rc still 0 at >=30, 2 below); `--json` output still contains all legacy keys.
- No-network: patch `pilots.price_provider.get_latest_prices`, `PaperAccountStore.get_open_positions`, and `HistoricalStore.get_bars` to raise; default `main()` passes. With `--live-quotes` the patched `get_latest_prices` is used, and a missing symbol yields None.
- Read-only: build a tmp SQLite file DB with the three tables (paper_positions, paper_closed_trades, price_bars/fundamentals_history), run `main(["--json"])` against it via a patched `resolve_database_url`, assert file mtime and content hash are unchanged. Also assert no `INSERT/UPDATE/CREATE` happens (the engine is `mode=ro`).
- Honesty-auditor agent pass on the diff before merge (CONSTRAINT #4 focus).

Verification: `pytest tests/test_feature_freeze_status.py`, then `python scripts/feature_freeze_status.py` and `--json` against the live DB (expected: SPY closed, hold 0.96 d, `share_under_1d` 1.0 at n=1 flagged as low-sample; open table for 8 pipeline names marked from stored closes dated 2026-10-02, sectors Real Estate 4 / Financial Services 2 / Energy 2); confirm the exit code is still 2. Small-sample caveat: print `n` next to every statistic and note "n<10" in text.

## 7. Documentation updates

- `CLAUDE.md` and `AGENTS.md` (exact mirrors, edit both; the sync hook handles it): one sentence in the "Feature freeze" section, after the `feature_freeze_status.py` command, saying the report also shows hold-time distribution, close reasons, entry clumping, open P&L (stored closes, `--live-quotes` opt-in) and sector concentration, and that the gate is still only the closed-trade count.
- `docs/RUNBOOK.md`: grep found no existing freeze mention, so add a short "Feature freeze progress check" subsection (command, fields, how to read the caveats, exit codes). Suggested location: near the daily/pre-market checks or beside the §3.15 cutover steps.
- `docs/architecture/webapp-and-gui.md`: only if it lists `scripts/feature_freeze_status.py` (to check at build time; a grep found it only in CLAUDE.md, AGENTS.md, the script, its test and `.claude/` walkthroughs). Skip if absent.
- Task-scoped artifacts on the branch: `.claude/freeze_status_quality_report_task.md`, `.claude/freeze_status_quality_report_walkthrough.md`. Also update `.claude/shrink_step7_feature_freeze_walkthrough.md` with a one-line pointer.

## 8. Risks and open questions

- Stored `close` can be up to a few days stale (latest bar 2026-10-02 vs today 2026-10-05); mitigated by showing `mark_date` / `mark_age_days`, with `--live-quotes` as the fresh alternative.
- Sector comes from the latest fundamentals snapshot (not point-in-time); acceptable for monitoring, labelled as such.
- `holding_period_days` for legacy rows may be NULL; handled via fallback and the unmeasured count.
- Decision for the operator (not in scope here): whether a "conviction positions don't count" finding should lead to a change in the gate. This plan deliberately does not change it.
