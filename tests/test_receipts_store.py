# =============================================================================
# TESTS: execution/receipts_store.py  —  fully offline.
#
# Covers:
#   * receipt parsing incl. blank/corrupt lines (dead-letter tolerance)
#   * ledger append + read round-trip (atomic, schema-normalised)
#   * dedup_key stability across timestamps within a UTC day, and difference
#     across days
#   * already_placed true/false
#   * reconciliation happy path (ledger matches fills), unmatched placed,
#     unexpected fill, and error-shaped fallback when the fills source raises
#
# No network I/O: reconciliation injects an `orders_fetcher` + `symbol_resolver`
# so `data.robinhood_orders.fetch_filled_orders` runs entirely offline, but we
# also point its module-level cache at a tmp path via monkeypatch to avoid any
# on-disk cache bleed between tests.
# =============================================================================

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from execution import receipts_store as rs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "output"


@pytest.fixture(autouse=True)
def _isolate_orders_cache(tmp_path, monkeypatch):
    """Point data.robinhood_orders' daily cache at a tmp path so reconciliation
    never reads/writes the real cache/robinhood_orders.json."""
    import data.robinhood_orders as ro
    monkeypatch.setattr(ro, "_CACHE_PATH", tmp_path / "rh_orders_cache.json", raising=True)


def _write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# dedup_key
# ---------------------------------------------------------------------------

def test_dedup_key_stable_within_day():
    t1 = datetime(2026, 7, 6, 9, 31, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 6, 15, 59, 30, tzinfo=timezone.utc)
    k1 = rs.make_dedup_key("aapl", "buy", t1)
    k2 = rs.make_dedup_key("AAPL", "BUY", t2)
    assert k1 == k2 == "2026-07-06:AAPL:buy"


def test_dedup_key_differs_across_days():
    t1 = datetime(2026, 7, 6, 23, 59, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 7, 0, 1, tzinfo=timezone.utc)
    assert rs.make_dedup_key("MSFT", "sell", t1) != rs.make_dedup_key("MSFT", "sell", t2)


def test_dedup_key_accepts_iso_string():
    assert rs.make_dedup_key("tsla", "buy", "2026-07-06T12:00:00+00:00") == "2026-07-06:TSLA:buy"


def test_dedup_key_uses_utc_date_from_ts():
    # 02:00 UTC on the 6th is still the 6th in UTC regardless of local offsets.
    assert rs.make_dedup_key("NVDA", "buy", "2026-07-06T02:00:00Z") == "2026-07-06:NVDA:buy"


# ---------------------------------------------------------------------------
# read_receipts — parsing incl. corrupt lines
# ---------------------------------------------------------------------------

def test_read_receipts_missing_file_returns_empty(out_dir):
    assert rs.read_receipts(out_dir) == []


def test_read_receipts_skips_blank_and_corrupt_lines(out_dir):
    path = out_dir / rs.RECEIPTS_FILENAME
    good1 = '{"ts":"2026-07-06T10:00:00+00:00","symbol":"AAPL","side":"buy","qty":3,"action":"placed","mcp_order_id":"abc","note":""}'
    good2 = '{"ts":"2026-07-06T10:05:00+00:00","symbol":"MSFT","side":"sell","qty":1,"action":"skipped","mcp_order_id":null,"note":"n"}'
    _write_lines(path, [
        good1,
        "",                       # blank line
        "   ",                    # whitespace-only line
        "{not valid json",        # corrupt line
        "[1,2,3]",                # valid JSON but not an object
        good2,
    ])
    recs = rs.read_receipts(out_dir)
    assert len(recs) == 2
    assert recs[0]["symbol"] == "AAPL"
    assert recs[1]["symbol"] == "MSFT"
    assert recs[0]["action"] == "placed"


# ---------------------------------------------------------------------------
# append_placed + read_placed_ledger round-trip
# ---------------------------------------------------------------------------

def test_append_placed_round_trip(out_dir):
    rs.append_placed(
        {"symbol": "aapl", "side": "buy", "qty": None, "target_notional": 500.0,
         "client_order_id": "coid1", "ts": "2026-07-06T10:00:00+00:00"},
        out_dir,
    )
    rs.append_placed(
        {"symbol": "MSFT", "side": "sell", "qty": 2.0, "target_notional": 800.0,
         "client_order_id": "coid2", "mcp_order_id": "mcp2", "ts": "2026-07-06T11:00:00+00:00"},
        out_dir,
    )
    ledger = rs.read_placed_ledger(out_dir)
    assert len(ledger) == 2

    r0 = ledger[0]
    assert r0["symbol"] == "AAPL"
    assert r0["side"] == "buy"
    assert r0["dedup_key"] == "2026-07-06:AAPL:buy"
    assert r0["qty"] is None                 # None preserved, not fabricated 0.0
    assert r0["target_notional"] == 500.0
    assert r0["client_order_id"] == "coid1"
    assert r0["mcp_order_id"] is None        # absent → None

    r1 = ledger[1]
    assert r1["dedup_key"] == "2026-07-06:MSFT:sell"
    assert r1["qty"] == 2.0
    assert r1["mcp_order_id"] == "mcp2"


def test_append_placed_derives_dedup_key_and_ts(out_dir):
    rs.append_placed({"symbol": "TSLA", "side": "BUY"}, out_dir)
    ledger = rs.read_placed_ledger(out_dir)
    assert len(ledger) == 1
    assert ledger[0]["dedup_key"].endswith(":TSLA:buy")
    # ts auto-populated as ISO8601 UTC
    assert ledger[0]["ts"].endswith("+00:00")


def test_append_placed_atomic_no_tmp_left(out_dir):
    rs.append_placed({"symbol": "AAPL", "side": "buy", "ts": "2026-07-06T10:00:00Z"}, out_dir)
    files = {p.name for p in out_dir.iterdir()}
    assert rs.PLACED_FILENAME in files
    assert not any(name.endswith(".tmp") for name in files)


# ---------------------------------------------------------------------------
# already_placed
# ---------------------------------------------------------------------------

def test_already_placed_true_and_false(out_dir):
    d = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)
    rs.append_placed(
        {"symbol": "AAPL", "side": "buy", "ts": d.isoformat()}, out_dir,
    )
    # Same day, same symbol/side (different time) → already placed.
    later = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)
    assert rs.already_placed("AAPL", "buy", out_dir, on_date=later) is True
    # Different side → not placed.
    assert rs.already_placed("AAPL", "sell", out_dir, on_date=later) is False
    # Different day → not placed.
    next_day = datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)
    assert rs.already_placed("AAPL", "buy", out_dir, on_date=next_day) is False
    # Unknown symbol → not placed.
    assert rs.already_placed("ZZZZ", "buy", out_dir, on_date=later) is False


def test_already_placed_empty_ledger(out_dir):
    assert rs.already_placed("AAPL", "buy", out_dir) is False


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------

def _raw_order(symbol_url, side, qty, price, ts_iso, oid):
    return {
        "state": "filled",
        "side": side,
        "cumulative_quantity": qty,
        "average_price": price,
        "last_transaction_at": ts_iso,
        "instrument": symbol_url,
        "id": oid,
    }


def _resolver(mapping):
    return lambda url: mapping.get(url)


def test_reconcile_happy_path(out_dir):
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "qty": 3, "ts": ts}, out_dir)

    orders = [_raw_order("url/AAPL/", "buy", 3, 190.0, ts, "o1")]
    fetcher = lambda: orders
    resolver = _resolver({"url/AAPL/": "AAPL"})

    report = rs.reconcile(out_dir, orders_fetcher=fetcher, symbol_resolver=resolver, force=True)
    assert report["ok"] is True
    assert report["placed_count"] == 1
    assert report["filled_matched"] == 1
    assert report["unmatched_placed"] == []
    assert report["unexpected_fills"] == []


def test_reconcile_unmatched_placed(out_dir):
    ts = "2026-07-06T14:00:00+00:00"
    # Ledger says we placed AAPL, but there is NO corresponding fill.
    rs.append_placed({"symbol": "AAPL", "side": "buy", "qty": 3, "ts": ts}, out_dir)

    fetcher = lambda: []                      # no fills at all
    resolver = _resolver({})

    report = rs.reconcile(out_dir, orders_fetcher=fetcher, symbol_resolver=resolver, force=True)
    assert report["ok"] is False
    assert report["filled_matched"] == 0
    assert len(report["unmatched_placed"]) == 1
    assert report["unmatched_placed"][0]["symbol"] == "AAPL"
    assert report["unexpected_fills"] == []


def test_reconcile_unexpected_fill(out_dir):
    ts = "2026-07-06T14:00:00+00:00"
    # Empty ledger, but a real fill exists → unexpected.
    orders = [_raw_order("url/NVDA/", "buy", 5, 120.0, ts, "o9")]
    fetcher = lambda: orders
    resolver = _resolver({"url/NVDA/": "NVDA"})

    report = rs.reconcile(out_dir, orders_fetcher=fetcher, symbol_resolver=resolver, force=True)
    assert report["ok"] is False
    assert report["placed_count"] == 0
    assert report["filled_matched"] == 0
    assert report["unmatched_placed"] == []
    assert len(report["unexpected_fills"]) == 1
    uf = report["unexpected_fills"][0]
    assert uf["symbol"] == "NVDA"
    assert uf["side"] == "buy"
    assert uf["total_qty"] == 5.0
    assert uf["dedup_key"] == "2026-07-06:NVDA:buy"


def test_reconcile_error_shaped_when_fetch_raises(out_dir):
    def boom():
        raise RuntimeError("network down")

    # fetch_filled_orders itself is dead-letter resilient (returns [] on fetcher
    # failure), so to prove the error-shaped fallback we make the ledger read
    # blow up instead by monkeypatching read_placed_ledger.
    import execution.receipts_store as mod
    orig = mod.read_placed_ledger
    try:
        def raising_reader(*a, **k):
            raise RuntimeError("ledger unreadable")
        mod.read_placed_ledger = raising_reader
        report = rs.reconcile(out_dir, orders_fetcher=boom, symbol_resolver=lambda u: None)
    finally:
        mod.read_placed_ledger = orig

    assert report["ok"] is False
    assert "error" in report
    assert report["placed_count"] == 0
    assert report["unmatched_placed"] == []
    assert report["unexpected_fills"] == []


def test_reconcile_fetcher_failure_degrades_to_no_fills(out_dir):
    """fetch_filled_orders swallows a fetcher exception and returns [] — so a
    placed ledger entry surfaces as unmatched, and reconcile never raises."""
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "qty": 3, "ts": ts}, out_dir)

    def boom():
        raise RuntimeError("network down")

    report = rs.reconcile(out_dir, orders_fetcher=boom, symbol_resolver=lambda u: None, force=True)
    assert report["ok"] is False
    assert len(report["unmatched_placed"]) == 1
    assert report["unexpected_fills"] == []
