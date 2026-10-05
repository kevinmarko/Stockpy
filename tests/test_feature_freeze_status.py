"""Tests for scripts/feature_freeze_status.py (step 7 freeze progress)."""
from types import SimpleNamespace

import main_orchestrator
from scripts import feature_freeze_status as ffs


def _t(strategy_id, pnl):
    return {"strategy_id": strategy_id, "realized_pnl": pnl}


def test_constant_matches_the_executor():
    assert ffs.PIPELINE_STRATEGY_ID == main_orchestrator.PIPELINE_STRATEGY_ID


def test_only_pipeline_trades_count():
    closed = [_t("main_pipeline", 5.0), _t("Manual Trade", 100.0),
              _t("Delta Hedge", -3.0), _t("main_pipeline", -2.0)]
    s = ffs.summarize(closed, [])
    assert s["closed_pipeline_trades"] == 2
    assert s["win_rate"] == 0.5
    assert s["total_realized_pnl"] == 3.0
    assert s["freeze_can_end"] is False


def test_threshold_reached():
    s = ffs.summarize([_t("main_pipeline", 1.0)] * 30, [])
    assert s["freeze_can_end"] is True


def test_empty_history_reports_nulls_not_zeros():
    s = ffs.summarize([], [])
    assert s["closed_pipeline_trades"] == 0
    assert s["win_rate"] is None and s["total_realized_pnl"] is None


def test_unmeasured_pnl_is_counted_but_excluded():
    s = ffs.summarize([_t("main_pipeline", None), _t("main_pipeline", 4.0)], [])
    assert s["closed_pipeline_trades"] == 2
    assert s["unmeasured_pnl_trades"] == 1
    assert s["win_rate"] == 1.0


def test_open_positions_filtered_to_pipeline():
    pos = [SimpleNamespace(symbol="AGNC", strategy_id="main_pipeline"),
           SimpleNamespace(symbol="ABR", strategy_id="Manual Trade")]
    assert ffs.summarize([], pos)["open_pipeline_positions"] == ["AGNC"]


def test_main_reads_open_positions_from_the_database_only(monkeypatch, capsys):
    """The status check must not mark positions over the network."""
    import data.paper_account_store as pas

    def _no_network(self):
        raise AssertionError("get_open_positions() marks prices over the network")

    monkeypatch.setattr(pas.PaperAccountStore, "get_open_positions", _no_network)
    monkeypatch.setattr(pas.PaperAccountStore, "get_full_closed_trades", lambda self, **kw: [])
    monkeypatch.setattr(pas.PaperAccountStore, "open_position_symbols", lambda self, sid: {"AGNC"})
    rc = ffs.main(["--json"])
    out = capsys.readouterr().out
    assert rc == 2
    assert '"AGNC"' in out


# ---------------------------------------------------------------------------
# Trade-quality report (observability only; the gate must be unaffected)
# ---------------------------------------------------------------------------
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

_NOW = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)


def _is_option(sym):
    return " " in sym  # option symbols contain spaces


def _ct(hold=None, reason="flatten", entry=None, exit_=None, sym="SPY", sid="main_pipeline", pnl=1.0):
    return {"strategy_id": sid, "symbol": sym, "holding_period_days": hold,
            "close_reason": reason, "entry_ts": entry, "exit_ts": exit_, "realized_pnl": pnl}


def test_hold_stats_median_share_and_buckets():
    own = [_ct(0.5), _ct(0.9), _ct(1.0), _ct(3.0), _ct(7.0)]
    h = ffs.holding_period_stats(own)
    assert h["median_days"] == 1.0 and h["n_measured"] == 5
    assert h["share_under_1d"] == pytest.approx(2 / 5)
    assert h["buckets"] == {"<1d": 2, "1-3d": 1, "3-7d": 1, ">=7d": 1}


def test_hold_stats_fallback_to_timestamps_and_unmeasured_not_zero():
    own = [_ct(None, entry="2026-10-01T00:00:00+00:00", exit_="2026-10-02T12:00:00+00:00"),
           _ct(None),
           _ct(None, entry="2026-10-03T00:00:00+00:00", exit_="2026-10-02T00:00:00+00:00")]
    h = ffs.holding_period_stats(own)
    assert h["n_measured"] == 1 and h["n_unmeasured"] == 2
    assert h["median_days"] == pytest.approx(1.5)


def test_hold_stats_empty_is_none():
    h = ffs.holding_period_stats([])
    assert h["median_days"] is None and h["mean_days"] is None and h["share_under_1d"] is None


def test_close_reasons_missing_is_unavailable():
    c = ffs.close_reason_counts([_ct(reason="flatten"), _ct(reason="flatten"),
                                 _ct(reason=""), _ct(reason=None)])
    assert c == {"flatten": 2, "unavailable": 2}


def test_entry_dates_clump_et_boundary_and_missing():
    closed = [_ct(entry="2026-10-01T02:00:00+00:00")]  # 22:00 ET on 09-30
    opens = [{"entry_ts": datetime(2026, 9, 30, 14, 34)} for _ in range(8)] + [{"entry_ts": None}]
    e = ffs.entry_date_stats(closed, opens)
    assert e["distinct_entry_dates"] == 1  # both land on 2026-09-30 ET
    assert e["max_entries_single_date"] == 9 and e["n_entries_without_ts"] == 1
    assert e["max_single_date_share"] == 1.0
    assert ffs.entry_date_stats([], [])["distinct_entry_dates"] is None


def _rows(*specs):
    return [{"symbol": s, "qty": q, "avg_entry_price": a, "entry_ts": datetime(2026, 10, 1, 16, 0)}
            for s, q, a in specs]


def test_marking_long_short_missing_and_option():
    rows = _rows(("AAA", 10, 10.0), ("BBB", -10, 10.0), ("CCC", 5, 10.0),
                 ("DDD 2026-12-18 $5 C", 1, 1.0))
    marks = {"AAA": {"price": 11.0, "date": "2026-10-02"},
             "BBB": {"price": 9.0, "date": "2026-10-02"},
             "DDD 2026-12-18 $5 C": {"price": 2.0, "date": "2026-10-02"}}
    out = {p["symbol"]: p for p in ffs.mark_open_positions(
        rows, marks, now=_NOW, is_option=_is_option, sectors={"AAA": "Energy"}, source="stored_close")}
    assert out["AAA"]["unrealized_pnl"] == pytest.approx(10.0)
    assert out["AAA"]["unrealized_pnl_pct"] == pytest.approx(0.10)
    assert out["BBB"]["unrealized_pnl"] == pytest.approx(10.0)  # short gains when price falls
    assert out["CCC"]["unrealized_pnl"] is None and out["CCC"]["mark_source"] == "unavailable"
    assert out["DDD 2026-12-18 $5 C"]["mark_price"] is None  # options never marked
    assert out["AAA"]["mark_age_days"] == 3 and out["AAA"]["days_held"] == pytest.approx(4.0)
    assert out["BBB"]["sector"] == "unavailable"
    summ = ffs.open_summary(list(out.values()))
    assert summ["n_marked"] == 2 and summ["n_unmarked"] == 2
    assert summ["total_unrealized_pnl"] == pytest.approx(20.0)


def test_unmarked_everywhere_gives_none_not_zero():
    out = ffs.mark_open_positions(_rows(("AAA", 1, 5.0)), {}, now=_NOW, is_option=_is_option,
                                  sectors={}, source="stored_close")
    assert out[0]["unrealized_pnl"] is None
    assert ffs.open_summary(out)["total_unrealized_pnl"] is None


def test_sector_concentration_shares_and_unknown():
    marked = ffs.mark_open_positions(
        _rows(("A", 10, 10.0), ("B", 10, 10.0), ("C", 20, 10.0), ("D", 1, 1.0)), {}, now=_NOW,
        is_option=_is_option, sectors={"A": "RE", "B": "RE", "C": "Energy"}, source="stored_close")
    sc = ffs.sector_concentration(marked, [_ct(sym="A"), _ct(sym="Z")], {"A": "RE"})
    assert sc["by_sector"]["RE"]["share"] == pytest.approx(0.5)
    assert sc["n_unknown_sector"] == 1
    assert sc["closed_trades_by_sector"] == {"RE": 1, "unavailable": 1}
    none = ffs.sector_concentration([], [], {})
    assert none["top_sector"] is None and none["top_sector_share"] is None


def _make_db(path, n_closed):
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE paper_positions (symbol TEXT, strategy_id TEXT, pilot_id TEXT, experiment_arm TEXT,
        qty REAL, avg_entry_price REAL, entry_ts TEXT, entry_snapshot_id TEXT,
        PRIMARY KEY (symbol, strategy_id));
    CREATE TABLE paper_closed_trades (trade_id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT,
        pilot_id TEXT, experiment_arm TEXT, symbol TEXT, side TEXT, qty REAL, entry_ts TEXT,
        entry_price REAL, exit_ts TEXT, exit_price REAL, commission REAL, realized_pnl REAL,
        realized_pnl_pct REAL, holding_period_days REAL, close_reason TEXT, leg_group_id TEXT,
        entry_snapshot_id TEXT, bridge_status TEXT, bridged_trade_id INTEGER, bridge_error TEXT,
        bridged_at TEXT);
    CREATE TABLE price_bars (symbol TEXT, date TEXT, close REAL, PRIMARY KEY (symbol, date));
    CREATE TABLE fundamentals_history (symbol TEXT, as_of TEXT, raw_json TEXT,
        PRIMARY KEY (symbol, as_of));
    """)
    c.execute("INSERT INTO paper_positions VALUES ('ARR','main_pipeline',NULL,NULL,10,10.0,"
              "'2026-10-01 14:00:00.000000',NULL)")
    c.execute("INSERT INTO paper_positions VALUES ('ABR','Manual Trade',NULL,NULL,5,5.0,NULL,NULL)")
    for _ in range(n_closed):
        c.execute("INSERT INTO paper_closed_trades (strategy_id,symbol,side,qty,entry_ts,entry_price,"
                  "exit_ts,exit_price,commission,realized_pnl,holding_period_days,close_reason,"
                  "bridge_status) VALUES ('main_pipeline','SPY','long',1,'2026-09-30 14:00:00',1,"
                  "'2026-10-01 14:00:00',1,0,1.0,1.0,'flatten','not_attempted')")
    c.execute("INSERT INTO price_bars VALUES ('ARR','2026-10-02',11.0)")
    c.execute("INSERT INTO fundamentals_history VALUES ('ARR','2026-10-05','{\"sector\": \"Real Estate\"}')")
    c.commit()
    c.close()


def _point_store_at_tmp_db(monkeypatch, tmp_path, n_closed=2):
    import data.paper_account_store as pas
    db = tmp_path / "t.db"
    _make_db(str(db), n_closed)
    real_init = pas.PaperAccountStore.__init__
    monkeypatch.setattr(
        pas.PaperAccountStore, "__init__",
        lambda self, db_url=None, *, readonly=False: real_init(self, f"sqlite:///{db}", readonly=True))
    return db


def _boom(*a, **k):
    raise AssertionError("network / marking path must not be used")


def test_main_quality_offline_json_reads_stored_close_and_is_read_only(monkeypatch, tmp_path, capsys):
    import data.historical_store as hs
    import data.paper_account_store as pas
    import pilots.price_provider as pp
    monkeypatch.setattr(pas.PaperAccountStore, "get_open_positions", _boom)
    monkeypatch.setattr(pp, "get_latest_prices", _boom)
    monkeypatch.setattr(hs.HistoricalStore, "get_bars", _boom)
    db = _point_store_at_tmp_db(monkeypatch, tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    rc = ffs.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert rc == 2 and out["closed_pipeline_trades"] == 2
    q = out["quality"]
    assert [p["symbol"] for p in q["open_positions"]] == ["ARR"]  # Manual Trade row excluded
    assert q["open_positions"][0]["unrealized_pnl"] == pytest.approx(10.0)
    assert q["open_positions"][0]["sector"] == "Real Estate"
    assert q["mark_source"] == "stored_close"
    for k in ("closed_pipeline_trades", "min_trades", "target_trades", "freeze_can_end", "win_rate",
              "total_realized_pnl", "unmeasured_pnl_trades", "open_pipeline_positions"):
        assert k in out


def test_main_text_output_has_quality_section(monkeypatch, tmp_path, capsys):
    _point_store_at_tmp_db(monkeypatch, tmp_path)
    ffs.main([])
    text = capsys.readouterr().out
    assert "Trade quality" in text and "ARR" in text and "Real Estate" in text


@pytest.mark.parametrize("n_closed,expected_rc", [(2, 2), (30, 0)])
def test_gate_exit_code_unaffected_by_quality_failure(monkeypatch, tmp_path, capsys, n_closed, expected_rc):
    _point_store_at_tmp_db(monkeypatch, tmp_path, n_closed)
    ok_rc = ffs.main(["--json"])
    ok = json.loads(capsys.readouterr().out)

    def _raise(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(ffs, "collect_quality", _raise)
    bad_rc = ffs.main(["--json"])
    bad = json.loads(capsys.readouterr().out)
    assert ok_rc == bad_rc == expected_rc
    assert "RuntimeError" in bad["quality"]["error"]
    assert ({k: v for k, v in ok.items() if k != "quality"}
            == {k: v for k, v in bad.items() if k != "quality"})


def test_live_quotes_opt_in_missing_symbol_is_none_not_cost_basis(monkeypatch, tmp_path, capsys):
    import pilots.price_provider as pp
    monkeypatch.setattr(pp, "get_latest_prices", lambda syms: {})
    _point_store_at_tmp_db(monkeypatch, tmp_path)
    ffs.main(["--json", "--live-quotes"])
    q = json.loads(capsys.readouterr().out)["quality"]
    assert q["mark_source"] == "live_quote"
    assert q["open_positions"][0]["unrealized_pnl"] is None
    assert q["open_summary"]["total_unrealized_pnl"] is None
