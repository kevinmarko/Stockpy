"""
tests/test_forecast_ledger_contamination_and_naive_backfill.py
==============================================================
* ``scripts/clean_forecast_ledger.py`` category (d), price contamination:
  a test-price row is flagged, a real row is not, a real model blow-up and a
  sub-$1 row are not, a row with no bars is unverifiable.
* ``scripts/backfill_naive_forecasts.py``: the backfilled naive row equals
  the one the live engine writes for the same inputs; its maturity equals
  what the real ``ForecastTracker.update_actuals`` stamps; (d) rows never
  seed a backfill; missing bars / sub-$1 / today are skipped; dry run writes
  nothing; ``--apply`` backs up first and the rows are identifiable.

Every test uses its own tmp ledger with an explicit path -- never the live DB.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest

import forecasting_engine
from forecasting.forecast_tracker import ForecastTracker
from forecasting_engine import ForecastingEngine
from scripts import backfill_naive_forecasts as bnf
from scripts import clean_forecast_ledger as clf
from settings import settings

ET = "America/New_York"
TODAY = pd.Timestamp("2026-09-28")  # override for plan_backfill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_ledger(tmp_path, name: str = "ledger.db") -> str:
    db = str(tmp_path / name)
    ForecastTracker(db_path=db)  # forecast_errors
    from data.historical_store import HistoricalStore

    HistoricalStore(db_path=db).engine.dispose()  # price_bars (real schema)
    return db


def _bars(db: str, symbol: str, closes: Sequence[Tuple[str, float]]) -> None:
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany(
            """INSERT OR REPLACE INTO price_bars
               (symbol, date, open, high, low, close, adj_close, volume, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, NULL, 1000, 'test', '2026-09-01T00:00:00+00:00')""",
            [(symbol, d, c, c, c, c) for d, c in closes],
        )


def _row(db: str, symbol: str, model: str, h: int, ts: str, price: float,
         actual: Optional[float] = None, day: Optional[str] = None) -> int:
    se = None if actual is None else (actual - price) ** 2
    with closing(sqlite3.connect(db)) as conn, conn:
        cur = conn.execute(
            """INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts,
                   forecast_price, actual_price, squared_error, recorded_at, forecast_day)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, model, h, ts, price, actual, se, ts, day),
        )
        return int(cur.lastrowid)


def _q(db: str, sql: str, params=()) -> List[tuple]:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql, params).fetchall()


def _find_d(db: str, symbols=None):
    conn = clf._connect(db, readonly=True)
    try:
        return clf.find_price_contamination_rows(conn, symbols=symbols)
    finally:
        conn.close()


def _plan(db: str, today=TODAY, **kw):
    conn = clf._connect(db, readonly=True)
    try:
        return bnf.plan_backfill(conn, db, today_et=today, **kw)
    finally:
        conn.close()


# 2026-08-10 is a Monday. 21:00Z = 17:00 ET (after the close); 12:00Z = 08:00 ET.
AFTER_CLOSE = "2026-08-10T21:00:00+00:00"
PRE_OPEN = "2026-08-10T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Category (d)
# ---------------------------------------------------------------------------

@pytest.fixture
def contaminated_ledger(tmp_path):
    db = _new_ledger(tmp_path)
    _bars(db, "AAPL", [("2026-08-07", 299.0), ("2026-08-10", 300.0)])
    _bars(db, "XYZ", [("2026-08-10", 50.0)])
    _bars(db, "QQQ", [("2026-08-10", 20.0)])
    _bars(db, "PENNY", [("2026-08-10", 0.0001)])
    _bars(db, "NEAR", [("2026-08-10", 10.0)])
    _bars(db, "STALE", [("2026-07-20", 10.0)])
    ids = {}
    # Test-price cycle (MockDataEngine ~$10) on a real ~$300 name.
    ids["contam"] = [
        _row(db, "AAPL", "naive", 10, "2026-08-10T15:00:00+00:00", 10.0, 10.1),
        _row(db, "AAPL", "monte_carlo", 10, "2026-08-10T15:00:00+00:00", 10.02, 10.1),
        _row(db, "AAPL", "monte_carlo", 90, "2026-08-10T15:00:00+00:00", 10.3),
    ]
    # Real cycle the same day.
    ids["real"] = [
        _row(db, "AAPL", "naive", 10, AFTER_CLOSE, 300.0),
        _row(db, "AAPL", "arima", 10, AFTER_CLOSE, 303.0),
        _row(db, "AAPL", "monte_carlo", 90, AFTER_CLOSE, 320.0),
    ]
    # A real model blow-up inside a healthy cycle: F2's problem, not (d)'s.
    ids["blowup"] = [
        _row(db, "XYZ", "naive", 10, AFTER_CLOSE, 50.0),
        _row(db, "XYZ", "arima", 90, AFTER_CLOSE, 5000.0),
    ]
    # Wrong INPUT price (naive/MC seeded at $100) but ARIMA at the real price.
    ids["bad_input_flagged"] = [
        _row(db, "QQQ", "naive", 10, AFTER_CLOSE, 100.0),
        _row(db, "QQQ", "monte_carlo", 10, AFTER_CLOSE, 101.0),
    ]
    ids["bad_input_real_arima"] = [_row(db, "QQQ", "arima", 10, AFTER_CLOSE, 20.1)]
    # Sub-$1 name with rounded bars: not checked.
    ids["penny"] = [_row(db, "PENNY", "naive", 10, AFTER_CLOSE, 0.4)]
    # No bars at all / bars too old: unverifiable.
    ids["nobars"] = [_row(db, "NOBARS", "naive", 10, AFTER_CLOSE, 10.0)]
    ids["stale"] = [_row(db, "STALE", "naive", 10, AFTER_CLOSE, 1.0)]
    # 2.5x: a near miss, kept.
    ids["near"] = [_row(db, "NEAR", "naive", 10, AFTER_CLOSE, 25.0)]
    # Saturday cycle checks against Friday's bar.
    ids["weekend"] = [_row(db, "AAPL", "naive", 10, "2026-08-08T15:00:00+00:00", 9.9)]
    return db, ids


class TestCategoryD:
    def test_flags_test_prices_and_nothing_real(self, contaminated_ledger):
        db, ids = contaminated_ledger
        flagged, rep = _find_d(db)
        assert flagged == sorted(ids["contam"] + ids["bad_input_flagged"] + ids["weekend"])
        assert set(rep["matched_by_symbol"]) == {"AAPL", "QQQ"}
        assert rep["matched_by_symbol"]["AAPL"]["rows"] == 4
        assert rep["matched_by_symbol"]["AAPL"]["reference_close_range"] == [299.0, 300.0]
        for key in ("real", "blowup", "bad_input_real_arima", "penny", "nobars", "stale", "near"):
            assert not set(ids[key]) & set(flagged), key

    def test_unverifiable_and_not_checked_are_counted(self, contaminated_ledger):
        db, _ids = contaminated_ledger
        _flagged, rep = _find_d(db)
        assert rep["unverifiable_rows"] == 2  # NOBARS + STALE (bar 21 days old)
        assert set(rep["unverifiable_by_symbol_top"]) == {"NOBARS", "STALE"}
        assert rep["sub_dollar_not_checked_rows"] == 1
        assert rep["near_miss_by_symbol"] == {"NEAR": 1}

    def test_missing_price_bars_table_flags_nothing(self, tmp_path):
        db = str(tmp_path / "nobars.db")
        ForecastTracker(db_path=db)
        _row(db, "AAPL", "naive", 10, AFTER_CLOSE, 10.0)
        flagged, rep = _find_d(db)
        assert flagged == [] and rep["unverifiable_rows"] == 1
        assert rep["bars_error"]

    def test_symbol_filter_restricts_deletion_not_the_report(self, contaminated_ledger):
        db, ids = contaminated_ledger
        flagged, rep = _find_d(db, symbols=("AAPL",))
        assert flagged == sorted(ids["contam"] + ids["weekend"])
        assert rep["matched_by_symbol"]["QQQ"]["selected"] is False
        assert rep["rows_matched"] == 6 and rep["rows_flagged"] == 4

    def test_d_is_opt_in_and_counted_in_totals(self, contaminated_ledger):
        db, _ids = contaminated_ledger
        before = _q(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        conn = clf._connect(db, readonly=True)
        try:
            default, _ = clf.analyze(conn, 365, 3)
            with_d, _ = clf.analyze(conn, 365, 3, ("d",))
        finally:
            conn.close()
        assert default["d_price"] is None and default["rows_deleted_total"] == 0
        assert with_d["rows_deleted_total"] == 6
        assert with_d["rows_after_cleanup"] == before - 6
        assert _q(db, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before

    def test_apply_backs_up_then_deletes_only_d(self, contaminated_ledger, tmp_path):
        db, ids = contaminated_ledger
        before = _q(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        s = clf.apply_cleanup(db, 365, 3, tmp_path / "backups", ("d",), d_symbols=("AAPL",))
        assert Path(s["backup_path"]).exists()
        assert _q(s["backup_path"], "SELECT COUNT(*) FROM forecast_errors")[0][0] == before
        left = {r[0] for r in _q(db, "SELECT id FROM forecast_errors")}
        assert not left & set(ids["contam"] + ids["weekend"])
        assert set(ids["bad_input_flagged"]) <= left  # QQQ not selected
        assert s["rows_remaining"] == before - 4

    def test_cli(self, contaminated_ledger, capsys):
        db, _ids = contaminated_ledger
        assert clf.main(["--db", db, "--window-days", "365", "--min-obs", "3", "--categories", "d"]) == 0
        out = capsys.readouterr().out
        assert "(d) price-contamination rows:" in out and "AAPL" in out and "DRY RUN" in out
        with pytest.raises(SystemExit):
            clf.main(["--db", db, "--d-symbols", "AAPL"])  # needs category d


# ---------------------------------------------------------------------------
# Backfill: the naive price and maturity equal the live code's
# ---------------------------------------------------------------------------

@pytest.fixture
def _deterministic_engine(monkeypatch):
    monkeypatch.setattr(forecasting_engine, "PROPHET_AVAILABLE", False)
    monkeypatch.setattr(forecasting_engine, "TENSORFLOW_AVAILABLE", False)
    monkeypatch.setattr(settings, "FORECAST_MC_RANDOM_SEED", 42)
    monkeypatch.setattr(settings, "FORECAST_SKILL_WEIGHTING_ENABLED", False)
    monkeypatch.setattr(settings, "FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED", False)
    monkeypatch.setattr(settings, "BERT_LLA_ENABLED", False)
    monkeypatch.setattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False)


class TestBackfillEqualsLiveCode:
    def test_backfilled_naive_equals_live_engine_naive(self, tmp_path, _deterministic_engine):
        """Run the REAL engine (current_price = the last completed session's
        close, which is what the live Price is before the open), delete the
        naive rows it wrote, backfill them, and compare."""
        db = _new_ledger(tmp_path)
        today = pd.Timestamp.now(tz=ET).tz_localize(None).normalize()
        dates = pd.bdate_range(end=today - pd.offsets.BDay(1), periods=260)
        rng = np.random.RandomState(0)
        closes = 50.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, len(dates))))
        closes = np.round(closes, 4)
        _bars(db, "EQL", [(d.strftime("%Y-%m-%d"), float(c)) for d, c in zip(dates, closes, strict=True)])
        history = pd.Series(closes, index=dates, name="Close")

        engine = ForecastingEngine(tracker=ForecastTracker(db_path=db))
        engine.generate_forecast(
            pd.Series({"Symbol": "EQL", "sector": "Unknown"}), float(history.iloc[-1]),
            history_series=history,
            precomputed_garch_term_structure={10: 0.3, 30: 0.3, 60: 0.3, 90: 0.3},
        )
        live = sorted(_q(db, """SELECT horizon_days, forecast_ts, forecast_day, forecast_price, actual_price
                                FROM forecast_errors WHERE model_name = 'naive'"""))
        assert [r[0] for r in live] == [10, 30, 60, 90]
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("DELETE FROM forecast_errors WHERE model_name = 'naive'")

        rows, summary, _ = _plan(db, today=today + pd.Timedelta(days=1))
        got = sorted(zip(rows["horizon_days"], rows["forecast_ts"], rows["forecast_day"],
                         rows["forecast_price"], [None if math.isnan(a) else a for a in rows["actual_price"]],
                         strict=True))
        assert got == live
        assert summary["rows_to_insert"] == 4

    def test_maturity_equals_real_update_actuals(self, tmp_path, monkeypatch):
        """A live naive row shares its cycle's forecast_ts, so the real
        update_actuals stamps it with its siblings' due-date price. The
        backfill copies that stamped value: prove it is identical."""
        bars = [(d.strftime("%Y-%m-%d"), 40.0 + i * 0.25)
                for i, d in enumerate(pd.bdate_range("2026-07-01", "2026-09-25"))]
        bars_df = pd.DataFrame({"Close": [c for _, c in bars]}, index=pd.DatetimeIndex([d for d, _ in bars]))

        class _FakeStore:
            def get_bars(self, *_a, **_k):
                return bars_df

        ts = "2026-08-10T21:00:00+00:00"
        now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
        close_0810 = dict(bars)["2026-08-10"]  # 17:00 ET cycle -> that day's close

        # Ledger A: the live world -- naive and arima pending in one cycle.
        a = _new_ledger(tmp_path, "a.db")
        _row(a, "SYM", "arima", 10, ts, 41.0, day="2026-08-10")
        _row(a, "SYM", "naive", 10, ts, close_0810, day="2026-08-10")
        # Ledger B: pre-naive history -- only arima, matured by the same call.
        b = _new_ledger(tmp_path, "b.db")
        _bars(b, "SYM", bars)
        _row(b, "SYM", "arima", 10, ts, 41.0, day="2026-08-10")
        with monkeypatch.context() as mp:
            import data.historical_store as hs

            mp.setattr(settings, "FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED", True)
            mp.setattr(hs, "HistoricalStore", _FakeStore)
            assert ForecastTracker(db_path=a).update_actuals("SYM", 10, 99.0, now) == 2
            assert ForecastTracker(db_path=b).update_actuals("SYM", 10, 99.0, now) == 1
        (live_actual, live_se), = _q(a, "SELECT actual_price, squared_error FROM forecast_errors "
                                        "WHERE model_name = 'naive'")
        assert live_actual != 99.0  # the due-date close, not the fallback

        rows, _s, _ = _plan(b)
        assert len(rows) == 1
        r = rows.iloc[0]
        assert r["forecast_ts"] == ts and r["forecast_day"] == "2026-08-10"
        assert r["forecast_price"] == close_0810
        assert r["actual_price"] == live_actual
        assert r["squared_error"] == pytest.approx(live_se, rel=1e-15)


# ---------------------------------------------------------------------------
# Backfill: what is and is not written
# ---------------------------------------------------------------------------

@pytest.fixture
def backfill_ledger(tmp_path):
    db = _new_ledger(tmp_path)
    _bars(db, "GOOD", [("2026-08-06", 19.0), ("2026-08-07", 20.0), ("2026-08-10", 21.0), ("2026-08-11", 22.0)])
    _bars(db, "LOW", [("2026-08-10", 0.8)])
    # GOOD: after-close cycle on 08-10 (matured siblings), pre-open cycle on
    # 08-11 (pending), an existing naive on 08-07.
    _row(db, "GOOD", "arima", 10, "2026-08-10T14:00:00+00:00", 20.0, 23.0)
    _row(db, "GOOD", "arima", 10, AFTER_CLOSE, 21.5, 23.0)
    _row(db, "GOOD", "holt_winters", 10, AFTER_CLOSE, 21.2, 23.0)
    _row(db, "GOOD", "arima", 30, "2026-08-11T12:00:00+00:00", 21.7)
    _row(db, "GOOD", "arima", 10, "2026-08-07T21:00:00+00:00", 20.1, 22.0)
    _row(db, "GOOD", "naive", 10, "2026-08-07T21:00:00+00:00", 20.0, 22.0)
    # Measurement-only names never seed a backfill.
    _row(db, "GOOD", "blend", 60, AFTER_CLOSE, 21.0)
    # No bars / sub-$1 / today.
    _row(db, "NOBARS", "arima", 10, AFTER_CLOSE, 5.0)
    _row(db, "LOW", "arima", 10, AFTER_CLOSE, 0.8)
    _row(db, "GOOD", "arima", 90, "2026-09-28T14:00:00+00:00", 22.0)
    return db


class TestBackfillPlan:
    def test_rows_prices_and_maturity(self, backfill_ledger):
        rows, s, _ = _plan(backfill_ledger)
        got = {(r.horizon_days, r.forecast_day): r for r in rows.itertuples()}
        assert set(got) == {(10, "2026-08-10"), (30, "2026-08-11")}
        after = got[(10, "2026-08-10")]
        assert after.forecast_ts == AFTER_CLOSE  # the day's LAST cycle
        assert after.forecast_price == 21.0  # after the close: 08-10's close
        assert after.actual_price == 23.0
        assert after.squared_error == pytest.approx((23.0 - 21.0) ** 2)
        pre = got[(30, "2026-08-11")]
        assert pre.forecast_price == 21.0  # 08:00 ET: the previous session's close
        assert math.isnan(pre.actual_price)  # siblings pending -> pending
        assert s["skipped"]["no_bar"] == 1 and s["skipped"]["sub_dollar"] == 1
        assert s["skipped"]["today_or_later"] == 1

    def test_stale_bar_is_skipped(self, tmp_path):
        db = _new_ledger(tmp_path)
        _bars(db, "OLD", [("2026-08-01", 30.0)])
        _row(db, "OLD", "arima", 10, AFTER_CLOSE, 30.0)
        rows, s, _ = _plan(db)
        assert rows.empty and s["skipped"]["no_bar"] == 1

    def test_sibling_actual_conflict_is_skipped(self, tmp_path):
        db = _new_ledger(tmp_path)
        _bars(db, "C", [("2026-08-10", 30.0)])
        _row(db, "C", "arima", 10, AFTER_CLOSE, 30.0, 31.0)
        _row(db, "C", "holt_winters", 10, AFTER_CLOSE, 30.0, 32.0)
        rows, s, _ = _plan(db)
        assert rows.empty and s["skipped"]["sibling_actual_conflict"] == 1

    def test_contaminated_rows_never_seed_and_block_until_cleaned(self, contaminated_ledger, tmp_path):
        db, _ids = contaminated_ledger
        # A key whose only model rows are contamination, on a day with no naive.
        _row(db, "AAPL", "monte_carlo", 30, "2026-08-10T15:00:00+00:00", 10.1)
        rows, s, _ = _plan(db)
        assert not ((rows["symbol"] == "AAPL") & (rows["horizon_days"] == 30)).any()
        # AAPL@10 on 08-10 has a real naive -> nothing to add; the Saturday
        # contaminated naive blocks nothing (no real model row that day).
        assert s["excluded_rows"]["d"] == 7
        # QQQ@10: its only naive is (d) -> blocked until clean (d) runs.
        assert s["skipped"]["blocked_by_uncleaned_d"] == 1
        clf.apply_cleanup(db, 365, 3, tmp_path / "b", ("d",))
        rows2, s2, _ = _plan(db)
        q = rows2[(rows2["symbol"] == "QQQ") & (rows2["horizon_days"] == 10)]
        assert len(q) == 1 and q.iloc[0]["forecast_price"] == 20.0
        assert s2["skipped"]["blocked_by_uncleaned_d"] == 0


class TestBackfillCli:
    def test_dry_run_writes_nothing(self, backfill_ledger, capsys, monkeypatch):
        monkeypatch.setattr(bnf, "et_today", lambda: TODAY)
        before = _q(backfill_ledger, "SELECT COUNT(*), MAX(id) FROM forecast_errors")
        assert bnf.main(["--db", backfill_ledger, "--window-days", "365"]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "rows to insert: 2" in out
        assert "Naive-gate n" in out
        assert _q(backfill_ledger, "SELECT COUNT(*), MAX(id) FROM forecast_errors") == before

    def test_apply_backs_up_inserts_and_is_identifiable(self, backfill_ledger, tmp_path, monkeypatch):
        monkeypatch.setattr(bnf, "et_today", lambda: TODAY)
        before = _q(backfill_ledger, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        s = bnf.apply_backfill(backfill_ledger, tmp_path / "backups")
        assert _q(s["backup_path"], "SELECT COUNT(*) FROM forecast_errors")[0][0] == before
        assert "naive-backfill" in Path(s["backup_path"]).name
        stamped = _q(backfill_ledger, "SELECT horizon_days, forecast_day FROM forecast_errors "
                                      "WHERE model_name = 'naive' AND recorded_at = ?", (s["recorded_at_stamp"],))
        assert sorted(stamped) == [(10, "2026-08-10"), (30, "2026-08-11")]
        manifest = json.loads(Path(s["manifest_path"]).read_text())
        assert len(manifest["inserted_ids"]) == 2 == s["inserted"]
        # Idempotent: nothing left to backfill.
        rows, _s, _ = _plan(backfill_ledger)
        assert rows.empty

    def test_apply_writes_nothing_when_backup_fails(self, backfill_ledger, tmp_path, monkeypatch):
        before = _q(backfill_ledger, "SELECT COUNT(*) FROM forecast_errors")[0][0]

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(clf, "make_backup", _boom)
        with pytest.raises(OSError):
            bnf.apply_backfill(backfill_ledger, tmp_path / "b")
        assert _q(backfill_ledger, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before

    def test_apply_rolls_back_on_mid_transaction_failure(self, backfill_ledger, tmp_path, monkeypatch):
        """A row that violates NOT NULL after good rows were inserted: the
        whole transaction rolls back."""
        monkeypatch.setattr(bnf, "et_today", lambda: TODAY)
        before = _q(backfill_ledger, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        real_plan = bnf.plan_backfill

        def _bad_plan(*a, **k):
            rows, s, ctx = real_plan(*a, **k)
            bad = rows.iloc[:1].copy()
            bad["symbol"] = None
            return pd.concat([rows, bad], ignore_index=True), s, ctx

        monkeypatch.setattr(bnf, "plan_backfill", _bad_plan)
        with pytest.raises(sqlite3.IntegrityError):
            bnf.apply_backfill(backfill_ledger, tmp_path / "b")
        assert _q(backfill_ledger, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before


# ---------------------------------------------------------------------------
# Projection: the real gate read on a scratch copy counts the backfilled days
# ---------------------------------------------------------------------------

class TestProjection:
    def test_backfill_raises_gate_n_and_moves_the_n60_date_earlier(self, tmp_path, monkeypatch):
        db = _new_ledger(tmp_path)
        today = pd.Timestamp.now(tz=ET).tz_localize(None).normalize()
        days = pd.bdate_range(end=today - pd.offsets.BDay(15), periods=70)
        _bars(db, "P", [(d.strftime("%Y-%m-%d"), 30.0) for d in pd.bdate_range(days[0] - pd.Timedelta(days=7), today)])
        for d in days:
            ts = f"{d.strftime('%Y-%m-%d')}T21:00:00+00:00"
            _row(db, "P", "arima", 10, ts, 30.5, 30.0, day=d.strftime("%Y-%m-%d"))
        rows, _s, ctx = _plan(db, today=today)
        assert len(rows) == 70 and rows["actual_price"].notna().all()
        rep = bnf.projection_report(ctx, rows, window_days=365, gate_min_obs=60, min_improvement=0.005)
        v = rep["per_symbol"]["P"]["h10"]
        assert v["n_now_before"] == 0
        assert v["n_now_after"] == 70  # every day matured before today
        assert v["first_n60_after"] == today  # already reachable
        assert rep["daybased_below_real_read_pairs"] == 0
