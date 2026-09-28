"""
scripts/backfill_naive_forecasts.py
===================================
Forecasting rebuild follow-up to F3: backfill the ``naive`` baseline rows the
live cycles never wrote. **DRY-RUN BY DEFAULT** -- without ``--apply`` it opens
the database read-only and only reports.

Why: the F3 naive gate admits a model only after ``n >= 60`` scored daily
pairs with ``naive`` for that symbol and horizon, but live ``naive`` rows only
exist from 2026-09-06, while the other models' rows go back to 2026-07-10.

What it inserts: for every ``(symbol, horizon, US/Eastern forecast day)`` that
has at least one REAL model row (a blend-eligible name, i.e. not in
``NON_BLEND_MODEL_NAMES``) but no ``naive`` row, the one ``naive`` row the live
engine would have written for that day's LAST cycle:

* ``forecast_ts`` = the latest real-model ``forecast_ts`` of that key (the
  cycle ``_latest_row_per_day`` keeps), and ``forecast_day`` = its US/Eastern
  date -- the same key ``ForecastTracker.record_forecasts`` writes.
* ``forecast_price`` = the live naive definition. Live, ``naive`` is
  ``current_price``, the cycle's dashboard ``Price`` (the latest price the
  data layer had). With daily bars only, the closest lookahead-free
  reconstruction is the LAST COMPLETED SESSION'S close at ``forecast_ts``: the
  close of the forecast's own US/Eastern day when the cycle ran at or after
  16:00 ET, otherwise the close of the last trading day before it. It comes
  from ``HistoricalStore`` opened read-only and must be at most 5 calendar
  days old; otherwise the key is skipped (``no_bar``), never guessed. Intraday
  cycles are the one place this differs from live (live saw an intraday
  price); the dry run measures the difference on the days where both exist.
* ``actual_price`` / ``squared_error``: a live ``naive`` row shares its
  cycle's ``forecast_ts``, so the real ``ForecastTracker.update_actuals``
  stamps it in the same call and with the same due-date price as that
  cycle's model rows (the price depends only on ``forecast_ts`` and the
  horizon). So a key whose cycle siblings are already matured gets the
  siblings' ``actual_price`` (all must agree, else the key is skipped as
  ``sibling_actual_conflict``); a key whose siblings are still pending is
  inserted pending and the live ``update_actuals`` matures it with them.
  ``squared_error`` uses ``update_actuals``'s formula.

F1 rules honoured: a reconstructed price under $1 is skipped (sub-$1 names are
excluded from every naive comparison); ``TEST`` rows (clean category a), the
2026-08-14 Monte Carlo seed rows (b) and price-contaminated rows (d) never
count as real model rows, and a key whose only ``naive`` row is a (d) row is
skipped (``blocked_by_uncleaned_d``) -- run ``clean_forecast_ledger.py
--categories d --apply`` FIRST. The current US/Eastern day (and anything
later) is never touched; the live daemon owns it.

Identifying backfilled rows (the schema has no provenance column):
``model_name = 'naive' AND recorded_at = '<the run's stamp>'``. The stamp and
the inserted ids are printed and written to
``<backup-dir>/naive-backfill-manifest-<stamp>.json``. More generally a
backfilled row's ``recorded_at`` is days or weeks after its ``forecast_ts``,
while a live row's are the same cycle. To undo:
``DELETE FROM forecast_errors WHERE model_name = 'naive' AND recorded_at = '<stamp>'``.

``--apply`` (the operator runs this, not an agent): takes a verified sqlite
online backup first (``clean_forecast_ledger.make_backup``), then re-plans and
inserts in ONE transaction, checking the inserted count.

Usage::

    python scripts/backfill_naive_forecasts.py                  # dry run
    python scripts/backfill_naive_forecasts.py --json
    python scripts/backfill_naive_forecasts.py --apply          # backup + insert
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts import clean_forecast_ledger as clf  # noqa: E402

NAIVE = "naive"
ET_TZ = "America/New_York"
SESSION_CLOSE_HOUR_ET = 16
NAIVE_BAR_MAX_AGE_DAYS = 5
MIN_PRICE = 1.0
SIBLING_ACTUAL_RTOL = 1e-9
HORIZONS = (10, 30, 60, 90)
PROJECTION_HORIZONS = (10, 30)
# A (symbol, model, horizon) with a row in the last N business days is assumed
# to keep producing one row per trading day (projection only).
ACTIVE_LOOKBACK_BDAYS = 5
PROJECTION_MAX_BDAYS = 400
KEY = ["symbol", "horizon_days", "dayts"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def et_today() -> pd.Timestamp:
    return pd.Timestamp.now(tz=ET_TZ).tz_localize(None).normalize()


def load_ledger(conn: sqlite3.Connection) -> pd.DataFrame:
    """Every row with parsed ``ts`` (UTC), ``ts_et`` and ``dayts`` (the
    ``COALESCE(forecast_day, ET day of forecast_ts)`` key as tz-naive
    midnight, like ``_latest_row_per_day``)."""
    df = pd.read_sql_query(
        """SELECT id, symbol, model_name, horizon_days, forecast_ts, forecast_day,
                  forecast_price, actual_price
           FROM forecast_errors""",
        conn,
    )
    df["ts"] = pd.to_datetime(df["forecast_ts"], utc=True, format="ISO8601", errors="coerce")
    df["ts_et"] = df["ts"].dt.tz_convert(ET_TZ)
    legacy = df["ts_et"].dt.tz_localize(None).dt.normalize()
    stored = pd.to_datetime(df["forecast_day"], format="%Y-%m-%d", errors="coerce")
    df["dayts"] = stored.where(stored.notna(), legacy)
    df["forecast_price"] = pd.to_numeric(df["forecast_price"], errors="coerce")
    df["actual_price"] = pd.to_numeric(df["actual_price"], errors="coerce")
    return df


def excluded_row_ids(conn: sqlite3.Connection, db_path: Optional[str]) -> Dict[str, Any]:
    """Ids of cleanup categories (a), (b), (d), which never count as real rows."""
    a_ids = set(clf.find_test_rows(conn))
    b_ids, _ = clf.find_mc_seed_rows(conn)
    d_ids, d_report = clf.find_price_contamination_rows(conn, db_path=db_path)
    return {"a": a_ids, "b": set(b_ids), "d": set(d_ids), "d_report": d_report}


def attach_last_completed_close(frame: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Adds ``naive_price`` and ``bar_day``: the close of the last session that
    had ended at ``frame['ts_et']`` (same ET day at/after 16:00, else the last
    trading day before), at most ``NAIVE_BAR_MAX_AGE_DAYS`` old; NaN otherwise.
    ``frame`` needs ``symbol`` and ``ts_et``; row order is preserved."""
    out = frame.copy()
    out["naive_price"] = np.nan
    out["bar_day"] = pd.NaT
    ok = out["ts_et"].notna()
    if bars.empty or not ok.any():
        return out
    t = out.loc[ok, "ts_et"]
    day = t.dt.tz_localize(None).dt.normalize()
    after_close = t.dt.hour >= SESSION_CLOSE_HOUR_ET
    cutoff = day.where(after_close, day - pd.Timedelta(days=1))
    left = pd.DataFrame({
        "_row": out.index[ok], "symbol": out.loc[ok, "symbol"].to_numpy(),
        "cutoff": cutoff.to_numpy(), "day": day.to_numpy(),
    }).sort_values("cutoff")
    merged = pd.merge_asof(
        left, bars.sort_values("bar_day"), left_on="cutoff", right_on="bar_day",
        by="symbol", direction="backward",
    )
    fresh = (merged["day"] - merged["bar_day"]) <= pd.Timedelta(days=NAIVE_BAR_MAX_AGE_DAYS)
    merged.loc[~fresh.fillna(False), ["close", "bar_day"]] = [np.nan, pd.NaT]
    merged = merged.set_index("_row")
    out.loc[merged.index, "naive_price"] = merged["close"].astype(float)
    out.loc[merged.index, "bar_day"] = merged["bar_day"]
    return out


def _session_bucket(ts_et: pd.Series) -> pd.Series:
    minutes = ts_et.dt.hour * 60 + ts_et.dt.minute
    return pd.Series(
        np.select([minutes < 9 * 60 + 30, minutes < SESSION_CLOSE_HOUR_ET * 60],
                  ["pre_open", "in_session"], "after_close"),
        index=ts_et.index,
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def plan_backfill(
    conn: sqlite3.Connection,
    db_path: Optional[str] = None,
    today_et: Optional[pd.Timestamp] = None,
    min_price: float = MIN_PRICE,
    ledger: Optional[pd.DataFrame] = None,
    exclusions: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """Returns ``(rows, summary, ctx)``. ``rows`` are the naive rows to insert
    (``symbol, horizon_days, forecast_ts, forecast_day, forecast_price,
    actual_price, squared_error``, plus diagnostics). ``ctx`` carries the loaded
    frames for the dry-run analyses. Reads only."""
    from forecasting.forecast_tracker import NON_BLEND_MODEL_NAMES, eastern_trading_day

    db_path = db_path or clf.db_file_of(conn)
    today = pd.Timestamp(today_et).normalize() if today_et is not None else et_today()
    df = load_ledger(conn) if ledger is None else ledger
    ex = excluded_row_ids(conn, db_path) if exclusions is None else exclusions
    excluded = df["id"].isin(ex["a"] | ex["b"] | ex["d"])
    usable = df["ts"].notna() & df["dayts"].notna()

    real = df[usable & ~excluded & ~df["model_name"].isin(NON_BLEND_MODEL_NAMES)]
    naive_ok = df[usable & ~excluded & (df["model_name"] == NAIVE)]
    naive_bad = df[usable & excluded & (df["model_name"] == NAIVE)]

    skipped: Dict[str, int] = {}
    keys = real[KEY].drop_duplicates()
    keys = keys.merge(naive_ok[KEY].drop_duplicates(), on=KEY, how="left", indicator=True)
    keys = keys[keys["_merge"] == "left_only"].drop(columns="_merge")
    blocked = keys.merge(naive_bad[KEY].drop_duplicates(), on=KEY, how="left", indicator=True)
    skipped["blocked_by_uncleaned_d"] = int((blocked["_merge"] == "both").sum())
    keys = blocked[blocked["_merge"] == "left_only"].drop(columns="_merge")
    not_past = keys["dayts"] >= today
    skipped["today_or_later"] = int(not_past.sum())
    keys = keys[~not_past]

    # The key's last cycle.
    last = (real.sort_values(["ts", "id"]).groupby(KEY, as_index=False).tail(1)
            [KEY + ["forecast_ts", "ts_et"]])
    cand = keys.merge(last, on=KEY, how="left")

    # Cycle siblings: real rows of the same (symbol, horizon, forecast_ts).
    sib = real.merge(cand[["symbol", "horizon_days", "forecast_ts"]].drop_duplicates(),
                     on=["symbol", "horizon_days", "forecast_ts"], how="inner")
    agg = sib.groupby(["symbol", "horizon_days", "forecast_ts"]).agg(
        n_sib=("id", "size"), n_matured=("actual_price", "count"),
        a_min=("actual_price", "min"), a_max=("actual_price", "max"),
    ).reset_index()
    cand = cand.merge(agg, on=["symbol", "horizon_days", "forecast_ts"], how="left")

    bars, bars_error = clf.load_reference_closes(
        db_path, sorted(cand["symbol"].unique()), cand["dayts"].min() if len(cand) else today,
    )
    cand = attach_last_completed_close(cand.reset_index(drop=True), bars)

    no_bar = cand["naive_price"].isna()
    skipped["no_bar"] = int(no_bar.sum())
    skipped_no_bar_symbols = sorted(cand.loc[no_bar, "symbol"].unique().tolist())
    cand = cand[~no_bar]
    sub = cand["naive_price"] < min_price
    skipped["sub_dollar"] = int(sub.sum())
    cand = cand[~sub]

    matured = cand["n_matured"] > 0
    spread = (cand["a_max"] - cand["a_min"]).abs()
    agree = spread <= SIBLING_ACTUAL_RTOL * cand["a_max"].abs()
    conflict = matured & ~agree
    skipped["sibling_actual_conflict"] = int(conflict.sum())
    cand = cand[~conflict].copy()
    matured = cand["n_matured"] > 0
    mixed = matured & (cand["n_matured"] < cand["n_sib"])
    cand["actual_price"] = cand["a_max"].where(matured)
    cand["squared_error"] = (cand["actual_price"] - cand["naive_price"]) ** 2
    cand["forecast_day"] = [eastern_trading_day(ts) for ts in cand["forecast_ts"]]
    cand["session"] = _session_bucket(cand["ts_et"])
    rows = cand.rename(columns={"naive_price": "forecast_price"})[
        ["symbol", "horizon_days", "forecast_ts", "forecast_day", "forecast_price",
         "actual_price", "squared_error", "dayts", "bar_day", "session", "ts_et"]
    ].sort_values(["symbol", "horizon_days", "dayts"]).reset_index(drop=True)

    live_first = naive_ok["dayts"].min() if len(naive_ok) else pd.NaT
    by_h: Dict[int, Dict[str, int]] = {}
    for h, g in rows.groupby("horizon_days"):
        by_h[int(h)] = {
            "rows": int(len(g)),
            "matured": int(g["actual_price"].notna().sum()),
            "pending": int(g["actual_price"].isna().sum()),
            "symbols": int(g["symbol"].nunique()),
        }
    summary: Dict[str, Any] = {
        "today_et": today.strftime("%Y-%m-%d"),
        "rows_to_insert": int(len(rows)),
        "matured": int(rows["actual_price"].notna().sum()),
        "pending": int(rows["actual_price"].isna().sum()),
        "mixed_sibling_maturity": int(mixed.sum()),
        "by_horizon": dict(sorted(by_h.items())),
        "symbols": int(rows["symbol"].nunique()),
        "day_range": ([rows["dayts"].min().strftime("%Y-%m-%d"), rows["dayts"].max().strftime("%Y-%m-%d")]
                      if len(rows) else None),
        "first_live_naive_day": live_first.strftime("%Y-%m-%d") if pd.notna(live_first) else None,
        "gap_fill_rows_on_or_after_first_live_naive_day": int(
            (rows["dayts"] >= live_first).sum()) if pd.notna(live_first) else 0,
        "last_cycle_session": {str(k): int(v) for k, v in rows["session"].value_counts().items()},
        "skipped": skipped,
        "skipped_no_bar_symbols": skipped_no_bar_symbols[:30],
        "skipped_no_bar_symbol_count": len(skipped_no_bar_symbols),
        "excluded_rows": {"a": len(ex["a"]), "b": len(ex["b"]), "d": len(ex["d"])},
        "d_matched_by_symbol": {s: v["rows"] for s, v in (ex["d_report"] or {}).get("matched_by_symbol", {}).items()},
        "bars_error": bars_error,
        "naive_price_rule": (
            "last completed session's close at forecast_ts (same ET day at/after 16:00 ET, else the last "
            f"trading day before), HistoricalStore read-only, at most {NAIVE_BAR_MAX_AGE_DAYS} calendar days old"
        ),
    }
    ctx = {"ledger": df, "excluded": excluded, "real": real, "naive_ok": naive_ok, "bars": bars,
           "today": today, "min_price": min_price}
    return rows, summary, ctx


# ---------------------------------------------------------------------------
# Dry-run analyses
# ---------------------------------------------------------------------------

def reconstruction_check(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Compare the reconstruction with the LIVE naive rows where both exist:
    each key's last live naive row, re-derived from bars at its own
    forecast_ts. Also shows the rejected alternative (always the previous
    session's close)."""
    nv = ctx["naive_ok"]
    if nv.empty:
        return {"n": 0}
    last = nv.sort_values(["ts", "id"]).groupby(KEY, as_index=False).tail(1).reset_index(drop=True)
    rec = attach_last_completed_close(last, ctx["bars"])
    prev = last.copy()
    prev["ts_et"] = prev["ts_et"].dt.normalize()  # 00:00 ET -> always the day before
    prev = attach_last_completed_close(prev, ctx["bars"])
    rec = rec[rec["naive_price"].notna() & (rec["forecast_price"] > 0)]
    if rec.empty:
        return {"n": 0}
    err = (np.log(rec["naive_price"] / rec["forecast_price"])).abs()
    perr = (np.log(prev.loc[rec.index, "naive_price"] / rec["forecast_price"])).abs()
    rec = rec.assign(err=err, perr=perr, session=_session_bucket(rec["ts_et"]))
    out: Dict[str, Any] = {
        "n": int(len(rec)),
        "exact_share": round(float((err < 1e-9).mean()), 4),
        "median_abs_log_diff": float(err.median()),
        "p90_abs_log_diff": float(err.quantile(0.9)),
        "alt_prev_close_median_abs_log_diff": float(perr.median()),
        "by_session": {},
    }
    for s, g in rec.groupby("session"):
        out["by_session"][str(s)] = {
            "n": int(len(g)),
            "exact_share": round(float((g["err"] < 1e-9).mean()), 4),
            "median_abs_log_diff": float(g["err"].median()),
            "alt_prev_close_median_abs_log_diff": float(g["perr"].median()),
        }
    return out


def skill_weight_impact(ctx: Dict[str, Any], rows: pd.DataFrame, window_days: int, min_obs: int) -> Dict[str, Any]:
    """``naive`` stays in the live skill-weight ARITHMETIC (F1). Recompute the
    live weights per (symbol, horizon) before/after adding the matured
    backfilled rows, renormalized over the real models the way
    ``_blend_with_skill`` does, and report the largest change."""
    from forecasting.forecast_tracker import NON_BLEND_MODEL_NAMES, compute_skill_weights_from_stats

    df = ctx["ledger"]
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=window_days)
    base = df[~ctx["excluded"] & df["actual_price"].notna() & (df["ts"] >= since)].copy()
    base["se"] = (base["actual_price"] - base["forecast_price"]) ** 2
    add = rows[rows["actual_price"].notna()].copy()
    add["ts"] = pd.to_datetime(add["forecast_ts"], utc=True, format="ISO8601", errors="coerce")
    add = add[add["ts"] >= since]
    add = add.assign(model_name=NAIVE, se=add["squared_error"])

    def stats(frame):
        g = frame.groupby(["symbol", "horizon_days", "model_name"])["se"].agg(["size", "mean"])
        out: Dict[Tuple[str, int], Dict[str, Tuple[int, float]]] = {}
        for (s, h, m), r in g.iterrows():
            out.setdefault((s, int(h)), {})[m] = (int(r["size"]), float(r["mean"]))
        return out

    before = stats(base)
    after = stats(pd.concat([base[["symbol", "horizon_days", "model_name", "se"]],
                             add[["symbol", "horizon_days", "model_name", "se"]]]))
    max_shift, raw_changed, cold_flip = 0.0, 0, 0
    for key, st_after in after.items():
        wb = compute_skill_weights_from_stats(before.get(key, {}), min_obs)
        wa = compute_skill_weights_from_stats(st_after, min_obs)
        if wb != wa:
            raw_changed += 1

        def real_norm(w):
            r = {m: v for m, v in w.items() if m not in NON_BLEND_MODEL_NAMES}
            t = sum(r.values())
            return {m: v / t for m, v in r.items()} if t > 0 else {}

        rb, ra = real_norm(wb), real_norm(wa)
        for m in set(rb) | set(ra):
            max_shift = max(max_shift, abs(rb.get(m, 0.0) - ra.get(m, 0.0)))

        def warm(stt):
            return any(n >= min_obs and m not in NON_BLEND_MODEL_NAMES for m, (n, _) in stt.items())

        if warm(before.get(key, {})) != warm(st_after):
            cold_flip += 1
    return {
        "window_days": window_days, "min_obs": min_obs,
        "pairs_whose_raw_weights_dict_changes": raw_changed,
        "pairs_whose_cold_start_status_changes": cold_flip,
        "max_real_model_weight_shift_after_renormalization": max_shift,
    }


def _temp_ledger(ctx: Dict[str, Any], rows: Optional[pd.DataFrame], path: str) -> None:
    """A scratch copy of the COMPLETED, non-excluded rows (plus the matured
    backfilled rows) for the real ``naive_gate_stats`` read."""
    from forecasting.forecast_tracker import ForecastTracker

    ForecastTracker(db_path=path)  # creates the table
    df = ctx["ledger"]
    base = df[~ctx["excluded"] & df["actual_price"].notna()]
    recs = list(zip(
        base["id"].astype(int), base["symbol"], base["model_name"], base["horizon_days"].astype(int),
        base["forecast_ts"], base["forecast_price"].astype(float), base["actual_price"].astype(float),
        base["forecast_day"].where(base["forecast_day"].notna(), None),
        strict=True,
    ))
    if rows is not None and len(rows):
        add = rows[rows["actual_price"].notna()]
        start = int(df["id"].max()) + 1 if len(df) else 1
        recs += list(zip(
            range(start, start + len(add)), add["symbol"], [NAIVE] * len(add), add["horizon_days"].astype(int),
            add["forecast_ts"], add["forecast_price"].astype(float), add["actual_price"].astype(float),
            add["forecast_day"], strict=True,
        ))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """INSERT INTO forecast_errors (id, symbol, model_name, horizon_days, forecast_ts,
                   forecast_price, actual_price, recorded_at, forecast_day)
               VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)""",
            recs,
        )
    conn.close()


def gate_read_now(ctx: Dict[str, Any], rows: Optional[pd.DataFrame], symbols: Sequence[str],
                  window_days: int, horizons: Sequence[int]) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """The REAL ``ForecastTracker.naive_gate_stats`` on a scratch copy, at
    now: per (symbol, horizon) the model count and the max scored n."""
    from forecasting.forecast_tracker import ForecastTracker

    out: Dict[Tuple[str, int], Dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="naive-backfill-") as tmp:
        path = str(Path(tmp) / "ledger.db")
        _temp_ledger(ctx, rows, path)
        t = ForecastTracker(db_path=path, readonly=True)
        now = datetime.now(timezone.utc)
        for sym in symbols:
            res = t.naive_gate_stats(sym, list(horizons), window_days=window_days, as_of=now)
            for h in horizons:
                st = (res.get("by_horizon") or {}).get(h) or {}
                out[(sym, h)] = {"max_n": max((int(v.get("n", 0)) for v in st.values()), default=0),
                                 "stats": st}
    return out


def project_first_n(
    ctx: Dict[str, Any], rows: Optional[pd.DataFrame], horizons: Sequence[int], window_days: int, min_obs: int,
) -> Dict[Tuple[str, int], Dict[str, Any]]:
    """Per (symbol, horizon): the first US/Eastern trading day on which some
    model reaches ``min_obs`` scored daily pairs with naive, under the gate's
    maturity rule (``day + BDay(h) < as_of day``, window on ``day``).

    Counts every paired day (matured, pending, or future); future days assume
    each (symbol, model, horizon) active in the last ``ACTIVE_LOOKBACK_BDAYS``
    business days keeps writing one row per trading day. It does not project
    whether the model then BEATS naive -- only when ``n`` allows a decision."""
    today = ctx["today"]
    min_price = ctx["min_price"]
    real = ctx["real"]
    nv = ctx["naive_ok"][["symbol", "horizon_days", "dayts", "forecast_price"]]
    if rows is not None and len(rows):
        nv = pd.concat([nv, rows[["symbol", "horizon_days", "dayts", "forecast_price"]]])
    nv = nv[(nv["forecast_price"] >= min_price) & nv["horizon_days"].isin(horizons)]
    nv_days = nv[KEY].drop_duplicates()
    m = real[real["horizon_days"].isin(horizons)][["symbol", "model_name", "horizon_days", "dayts"]]
    m = m[m["dayts"] < today].drop_duplicates()
    paired = m.merge(nv_days, on=KEY, how="inner")

    recent_cut = today - pd.offsets.BDay(ACTIVE_LOOKBACK_BDAYS)
    active = real[(real["dayts"] >= recent_cut) & real["horizon_days"].isin(horizons)][
        ["symbol", "model_name", "horizon_days"]].drop_duplicates()
    future = pd.bdate_range(today, periods=PROJECTION_MAX_BDAYS)
    grid = future.to_numpy()

    out: Dict[Tuple[str, int], Dict[str, Any]] = {}
    groups = {k: g["dayts"].to_numpy() for k, g in paired.groupby(["symbol", "model_name", "horizon_days"])}
    active_set = set(map(tuple, active.to_numpy()))
    for key in set(groups) | active_set:
        sym, model, h = key
        days = groups.get(key, np.array([], dtype="datetime64[ns]"))
        if key in active_set:
            days = np.concatenate([days, future.to_numpy()])
        if not len(days):
            continue
        days = np.sort(np.unique(days))
        due = (pd.DatetimeIndex(days) + pd.offsets.BDay(int(h))).to_numpy()
        # n(T) = #{d : due(d) < T and d >= T - window}
        n_due = np.searchsorted(np.sort(due), grid, side="left")
        lo = grid - np.timedelta64(int(window_days), "D")
        n_old = np.searchsorted(days, lo, side="left")  # days before the window (all long due)
        n = n_due - np.minimum(n_old, n_due)
        hit = np.nonzero(n >= min_obs)[0]
        first = pd.Timestamp(grid[hit[0]]) if len(hit) else None
        n_now = int(n[0])
        rec = out.setdefault((sym, int(h)), {"first_date": None, "n_now_daybased": 0, "model": None})
        rec["n_now_daybased"] = max(rec["n_now_daybased"], n_now)
        if first is not None and (rec["first_date"] is None or first < rec["first_date"]):
            rec["first_date"], rec["model"] = first, model
    return out


def projection_report(
    ctx: Dict[str, Any], rows: pd.DataFrame, window_days: int, gate_min_obs: int,
    min_improvement: float, horizons: Sequence[int] = PROJECTION_HORIZONS,
) -> Dict[str, Any]:
    from forecasting.forecast_tracker import compute_naive_gate

    today = ctx["today"]
    real = ctx["real"]
    recent_cut = today - pd.offsets.BDay(ACTIVE_LOOKBACK_BDAYS)
    active_symbols = sorted(real.loc[real["dayts"] >= recent_cut, "symbol"].unique().tolist())
    naive_syms = set(ctx["naive_ok"]["symbol"]) | set(rows["symbol"])
    symbols = sorted(naive_syms)

    before_now = gate_read_now(ctx, None, symbols, window_days, horizons)
    after_now = gate_read_now(ctx, rows, symbols, window_days, horizons)
    proj_before = project_first_n(ctx, None, horizons, window_days, gate_min_obs)
    proj_after = project_first_n(ctx, rows, horizons, window_days, gate_min_obs)

    per_symbol: Dict[str, Dict[str, Any]] = {}
    admitted_now = {h: 0 for h in horizons}
    for sym in symbols:
        rec: Dict[str, Any] = {"active": sym in active_symbols}
        for h in horizons:
            st = after_now[(sym, h)]["stats"]
            dec = compute_naive_gate(st, candidates=list(st), min_improvement=min_improvement, min_obs=gate_min_obs)
            if dec["admitted"]:
                admitted_now[h] += 1
            pa = proj_after.get((sym, h), {})
            pb = proj_before.get((sym, h), {})
            rec[f"h{h}"] = {
                "n_now_before": before_now[(sym, h)]["max_n"],
                "n_now_after": after_now[(sym, h)]["max_n"],
                "first_n{}_after".format(gate_min_obs): pa.get("first_date"),
                "first_n{}_before".format(gate_min_obs): pb.get("first_date"),
                "admitted_now_after": dec["admitted"],
            }
        per_symbol[sym] = rec

    def dist(key_fmt: str, h: int, only_active: bool) -> Dict[str, Any]:
        dates = [v[f"h{h}"][key_fmt] for s, v in per_symbol.items()
                 if (v["active"] or not only_active) and v[f"h{h}"][key_fmt] is not None]
        if not dates:
            return {"symbols_reaching": 0}
        dates = sorted(dates)
        return {"symbols_reaching": len(dates), "earliest": dates[0].strftime("%Y-%m-%d"),
                "median": dates[len(dates) // 2].strftime("%Y-%m-%d"), "latest": dates[-1].strftime("%Y-%m-%d")}

    k_after = f"first_n{gate_min_obs}_after"
    k_before = f"first_n{gate_min_obs}_before"
    summary_h = {}
    for h in horizons:
        summary_h[h] = {
            "active_symbols": len(active_symbols),
            "max_n_now_before": max((before_now[(s, h)]["max_n"] for s in symbols), default=0),
            "max_n_now_after": max((after_now[(s, h)]["max_n"] for s in symbols), default=0),
            "admitted_now_after": admitted_now[h],
            "first_n_date_active_after": dist(k_after, h, True),
            "first_n_date_active_before": dist(k_before, h, True),
        }
    # Day-based projection at today vs the real gate read (sanity check).
    mismatch = sum(
        1 for (s, h), v in proj_after.items()
        if h in horizons and (s, h) in after_now and v["n_now_daybased"] < after_now[(s, h)]["max_n"]
    )
    return {"window_days": window_days, "gate_min_obs": gate_min_obs, "min_improvement": min_improvement,
            "by_horizon": summary_h, "per_symbol": per_symbol, "active_symbols": active_symbols,
            "daybased_below_real_read_pairs": mismatch}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_backfill(
    db_path: str,
    backup_dir: Path,
    today_et: Optional[pd.Timestamp] = None,
    min_price: float = MIN_PRICE,
) -> Dict[str, Any]:
    """Back up, then re-plan and insert in ONE transaction."""
    backup = clf.make_backup(db_path, backup_dir, label="naive-backfill")  # raises -> nothing written
    stamp = datetime.now(timezone.utc).isoformat()
    conn = clf._connect(db_path, readonly=False)  # noqa: SLF001
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not clf._has_column(conn, "forecast_day"):  # noqa: SLF001
                conn.execute("ALTER TABLE forecast_errors ADD COLUMN forecast_day TEXT")
            rows, summary, _ctx = plan_backfill(conn, db_path, today_et=today_et, min_price=min_price)
            before = conn.execute("SELECT COUNT(*) FROM forecast_errors").fetchone()[0]

            def _f(v):
                return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

            conn.executemany(
                """INSERT INTO forecast_errors
                   (symbol, model_name, horizon_days, forecast_ts, forecast_price,
                    actual_price, squared_error, recorded_at, forecast_day)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(r.symbol, NAIVE, int(r.horizon_days), r.forecast_ts, float(r.forecast_price),
                  _f(r.actual_price), _f(r.squared_error), stamp, r.forecast_day)
                 for r in rows.itertuples(index=False)],
            )
            after = conn.execute("SELECT COUNT(*) FROM forecast_errors").fetchone()[0]
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM forecast_errors WHERE model_name = ? AND recorded_at = ? ORDER BY id",
                (NAIVE, stamp),
            ).fetchall()]
            if after - before != len(rows) or len(ids) != len(rows):
                raise RuntimeError(
                    f"insert count mismatch: +{after - before} rows, {len(ids)} stamped, planned {len(rows)}"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    manifest = Path(backup_dir) / f"naive-backfill-manifest-{stamp.replace(':', '').replace('+', 'Z')}.json"
    manifest.write_text(json.dumps({"recorded_at_stamp": stamp, "db": db_path, "backup": str(backup),
                                    "inserted_ids": ids}, indent=1))
    summary.update({"applied": True, "backup_path": str(backup), "recorded_at_stamp": stamp,
                    "inserted": len(ids), "manifest_path": str(manifest)})
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_date(v) -> str:
    return v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else ("never (in 400 bdays)" if v is None else str(v))


def _render(summary: Dict[str, Any], db_path: str, applied: bool) -> str:
    head = "APPLIED" if applied else "DRY RUN (nothing written; pass --apply to insert)"
    lines = [
        f"naive backfill -- {head}",
        f"ledger: {db_path}",
        f"today (US/Eastern, never touched): {summary['today_et']}",
        f"naive price rule: {summary['naive_price_rule']}",
        f"rows to insert: {summary['rows_to_insert']:,} ({summary['matured']:,} matured from their cycle's "
        f"siblings, {summary['pending']:,} pending) in {summary['symbols']} symbols",
        f"forecast days: {summary['day_range']}  (first live naive day {summary['first_live_naive_day']}; "
        f"{summary['gap_fill_rows_on_or_after_first_live_naive_day']:,} of the rows fill gaps on/after it)",
        "by horizon:",
    ]
    for h, v in summary["by_horizon"].items():
        lines.append(f"  h={h:>2}: {v['rows']:>7,} rows  matured {v['matured']:>7,}  pending {v['pending']:>6,}  "
                     f"symbols {v['symbols']}")
    lines += [
        f"last-cycle session of the backfilled days: {summary['last_cycle_session']}",
        f"cycles whose siblings were only partly matured (used the matured value): "
        f"{summary['mixed_sibling_maturity']:,}",
        f"skipped: {summary['skipped']}",
        f"  no-bar symbols ({summary['skipped_no_bar_symbol_count']}): {summary['skipped_no_bar_symbols']}",
        f"excluded as real-model rows (clean categories): {summary['excluded_rows']}",
    ]
    if summary["excluded_rows"]["d"]:
        lines.append(
            f"  NOTE: {summary['excluded_rows']['d']:,} (d) price-contamination rows are still in the ledger "
            f"({summary['d_matched_by_symbol']}); run clean_forecast_ledger.py --categories d --apply first."
        )
    if summary.get("bars_error"):
        lines.append(f"bars: {summary['bars_error']}")
    rc = summary.get("reconstruction_check")
    if rc and rc.get("n"):
        lines += [
            "",
            f"Reconstruction vs LIVE naive rows (n={rc['n']:,} keys where both exist): exact "
            f"{rc['exact_share']:.1%}, median |ln diff| {rc['median_abs_log_diff']:.5f}, p90 "
            f"{rc['p90_abs_log_diff']:.5f}  (rejected 'always previous close': median "
            f"{rc['alt_prev_close_median_abs_log_diff']:.5f})",
        ]
        for s, v in rc["by_session"].items():
            lines.append(f"  {s:<12} n={v['n']:>6,}  exact {v['exact_share']:.1%}  median {v['median_abs_log_diff']:.5f}"
                         f"  (prev-close rule {v['alt_prev_close_median_abs_log_diff']:.5f})")
    sw = summary.get("skill_weight_impact")
    if sw:
        lines += [
            "",
            f"Live skill-weight impact (naive is in the skill arithmetic; window={sw['window_days']}d, "
            f"min_obs={sw['min_obs']}): raw weight dicts change for {sw['pairs_whose_raw_weights_dict_changes']:,} "
            f"(symbol, horizon) pairs; cold-start status changes for {sw['pairs_whose_cold_start_status_changes']} "
            f"; max real-model weight shift after _blend_with_skill's renormalization "
            f"{sw['max_real_model_weight_shift_after_renormalization']:.3g}",
        ]
    pr = summary.get("projection")
    if pr:
        n = pr["gate_min_obs"]
        lines += ["", f"Naive-gate n (window={pr['window_days']}d, min_obs={n}, margin "
                      f"{pr['min_improvement']:.2%}); 'now' = the real naive_gate_stats read on a scratch copy:"]
        for h, v in pr["by_horizon"].items():
            a, b = v["first_n_date_active_after"], v["first_n_date_active_before"]
            lines.append(
                f"  h={h}: max n now {v['max_n_now_before']} -> {v['max_n_now_after']}; admitted now "
                f"{v['admitted_now_after']}; first day some model reaches n={n} (active symbols, "
                f"{v['active_symbols']}): WITH backfill earliest {a.get('earliest')} median {a.get('median')} "
                f"latest {a.get('latest')} ({a['symbols_reaching']} symbols) | WITHOUT earliest "
                f"{b.get('earliest')} median {b.get('median')}"
            )
        lines.append(f"  per active symbol (n now before->after | first n={n} date after (before)):")
        for sym in pr["active_symbols"]:
            v = pr["per_symbol"].get(sym)
            if v is None:
                lines.append(f"    {sym:<7} no naive pairs")
                continue
            parts = []
            for h in PROJECTION_HORIZONS:
                x = v[f"h{h}"]
                parts.append(f"h{h}: {x['n_now_before']:>2}->{x['n_now_after']:>2} | "
                             f"{_fmt_date(x[f'first_n{n}_after'])} ({_fmt_date(x[f'first_n{n}_before'])})")
            lines.append(f"    {sym:<7} " + "   ".join(parts))
        if pr["daybased_below_real_read_pairs"]:
            lines.append(f"  (projection sanity: {pr['daybased_below_real_read_pairs']} pairs where the day-based "
                         f"count today is below the real read)")
    if applied:
        lines += ["", f"backup: {summary['backup_path']}", f"recorded_at stamp: {summary['recorded_at_stamp']}",
                  f"inserted: {summary['inserted']:,}", f"manifest: {summary['manifest_path']}",
                  f"undo: DELETE FROM forecast_errors WHERE model_name = 'naive' AND recorded_at = "
                  f"'{summary['recorded_at_stamp']}'"]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill missing naive forecast rows (dry run by default).")
    parser.add_argument("--db", default=None, help="SQLite path (default: the platform ledger).")
    parser.add_argument("--apply", action="store_true", help="Back up, then insert in one transaction.")
    parser.add_argument("--backup-dir", default=None, help="Backup directory (default: <LOCAL_DATA_ROOT>/backups).")
    parser.add_argument("--window-days", type=int, default=None,
                        help="Skill/gate window (default: FORECAST_SKILL_WINDOW_DAYS).")
    parser.add_argument("--gate-min-obs", type=int, default=None,
                        help="Gate n floor (default: FORECAST_NAIVE_GATE_MIN_OBS).")
    parser.add_argument("--skill-min-obs", type=int, default=None,
                        help="Skill-weight maturity (default: FORECAST_SKILL_MIN_OBS).")
    parser.add_argument("--no-projection", action="store_true", help="Skip the (slower) gate projection.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    from settings import settings

    db_path = args.db or clf._default_db_path()  # noqa: SLF001
    if not Path(db_path).exists():
        print(f"no database at {db_path}", file=sys.stderr)
        return 2
    window = args.window_days if args.window_days is not None else int(settings.FORECAST_SKILL_WINDOW_DAYS)
    gate_min_obs = (args.gate_min_obs if args.gate_min_obs is not None
                    else int(settings.FORECAST_NAIVE_GATE_MIN_OBS))
    skill_min_obs = args.skill_min_obs if args.skill_min_obs is not None else int(settings.FORECAST_SKILL_MIN_OBS)
    margin = float(settings.FORECAST_NAIVE_GATE_MIN_IMPROVEMENT)

    if args.apply:
        backup_dir = Path(args.backup_dir) if args.backup_dir else Path(settings.LOCAL_DATA_ROOT) / "backups"
        summary = apply_backfill(db_path, backup_dir)
    else:
        conn = clf._connect(db_path, readonly=True)  # noqa: SLF001
        try:
            rows, summary, ctx = plan_backfill(conn, db_path)
        finally:
            conn.close()
        summary["applied"] = False
        summary["reconstruction_check"] = reconstruction_check(ctx)
        summary["skill_weight_impact"] = skill_weight_impact(ctx, rows, window, skill_min_obs)
        if not args.no_projection:
            summary["projection"] = projection_report(ctx, rows, window, gate_min_obs, margin)

    if args.json:
        print(json.dumps(summary, indent=2, default=_json_default))
    else:
        print(_render(summary, db_path, applied=bool(args.apply)))
    return 0


def _json_default(v):
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return str(v)


if __name__ == "__main__":
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())
