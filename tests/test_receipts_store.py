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


def test_reconcile_quantity_mismatch_with_known_qty_is_not_a_silent_match(out_dir):
    """A same-key placed/fill pair whose KNOWN quantities diverge wildly
    (placed 100, filled 3) must not be folded into matched_keys, nor
    misrepresented as unmatched_placed/unexpected_fills -- it is a distinct
    failure mode surfaced honestly in its own quantity_mismatch bucket, and
    must flip ok=False."""
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "qty": 100, "ts": ts}, out_dir)

    orders = [_raw_order("url/AAPL/", "buy", 3, 190.0, ts, "o1")]
    fetcher = lambda: orders
    resolver = _resolver({"url/AAPL/": "AAPL"})

    report = rs.reconcile(out_dir, orders_fetcher=fetcher, symbol_resolver=resolver, force=True)
    assert report["ok"] is False
    assert report["placed_count"] == 1
    assert report["filled_matched"] == 0
    # A key-based match exists (same date/symbol/side), so it must NOT be
    # misrepresented as "no fill at all" or "an unexplained fill".
    assert report["unmatched_placed"] == []
    assert report["unexpected_fills"] == []
    assert len(report["quantity_mismatch"]) == 1
    mismatch = report["quantity_mismatch"][0]
    assert mismatch["symbol"] == "AAPL"
    assert mismatch["side"] == "buy"
    assert mismatch["placed_qty"] == 100.0
    assert mismatch["filled_qty"] == 3.0
    assert mismatch["diff"] == pytest.approx(97.0)


def test_reconcile_within_tolerance_qty_still_matches(out_dir):
    """A known placed qty that matches the fill qty exactly must still count
    as a genuine match (no false-positive mismatch on the happy path)."""
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "MSFT", "side": "sell", "qty": 10, "ts": ts}, out_dir)

    orders = [_raw_order("url/MSFT/", "sell", 10, 300.0, ts, "o2")]
    fetcher = lambda: orders
    resolver = _resolver({"url/MSFT/": "MSFT"})

    report = rs.reconcile(out_dir, orders_fetcher=fetcher, symbol_resolver=resolver, force=True)
    assert report["ok"] is True
    assert report["filled_matched"] == 1
    assert report["quantity_mismatch"] == []


def test_reconcile_notional_based_buy_with_unknown_qty_stays_key_matched(out_dir):
    """A notional-based BUY intent (qty=None, target_notional set) can't be
    quantity-compared in advance -- key presence alone must remain the
    honest match criterion, unaffected by whatever share count actually
    filled (no false-positive mismatch on a legitimate notional order)."""
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed(
        {"symbol": "NVDA", "side": "buy", "qty": None, "target_notional": 500.0, "ts": ts},
        out_dir,
    )

    orders = [_raw_order("url/NVDA/", "buy", 4.37, 114.4, ts, "o3")]
    fetcher = lambda: orders
    resolver = _resolver({"url/NVDA/": "NVDA"})

    report = rs.reconcile(out_dir, orders_fetcher=fetcher, symbol_resolver=resolver, force=True)
    assert report["ok"] is True
    assert report["filled_matched"] == 1
    assert report["quantity_mismatch"] == []


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


# ---------------------------------------------------------------------------
# Robinhood trade-approval mode (four-agent plan, Phase 1)
# ---------------------------------------------------------------------------

def test_make_ref_id_is_deterministic_uuid():
    import uuid
    a = rs.make_ref_id("coid-123")
    assert a == rs.make_ref_id("coid-123")          # retry → same ref_id
    assert a != rs.make_ref_id("coid-124")          # new logical order → new ref_id
    assert str(uuid.UUID(a)) == a                   # a real UUID string


def test_make_ref_id_pinned_namespace():
    """The namespace must never change: ref_ids already sent to Robinhood were
    derived from it. Pin one known output so a namespace edit fails loudly."""
    import uuid
    expected = str(uuid.uuid5(uuid.UUID("6f1c2e0a-5b7d-4c3e-9a41-8d2f6b0e7c15"), "pin"))
    assert rs.make_ref_id("pin") == expected


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_make_ref_id_blank_is_none_not_random(blank):
    assert rs.make_ref_id(blank) is None


@pytest.mark.parametrize("raw,expected", [
    ("pending", "pending"),
    ("APPROVED", "approved"),
    ("STATE_DECLINED", "declined"),
    ("approval_state_expired", "expired"),
    ("auto-approved", "auto_approved"),
    ("Auto Approved", "auto_approved"),
    ("STATE_AUTO_APPROVED", "auto_approved"),
    ("cancelled", "unknown"),
    (None, "unknown"),
])
def test_normalise_approval_state(raw, expected):
    assert rs.normalise_approval_state(raw) == expected


def test_append_placed_carries_approval_fields_and_legacy_rows_get_none(out_dir):
    rs.append_placed({"symbol": "AAPL", "side": "buy", "ts": "2026-07-06T10:00:00Z"}, out_dir)
    rs.append_placed(
        {"symbol": "MSFT", "side": "buy", "ts": "2026-07-06T10:01:00Z",
         "approval_id": "appr-1", "ref_id": "ref-1", "run_id": "run-1"},
        out_dir,
    )
    legacy, new = rs.read_placed_ledger(out_dir)
    assert legacy["approval_id"] is None and legacy["ref_id"] is None and legacy["run_id"] is None
    assert (new["approval_id"], new["ref_id"], new["run_id"]) == ("appr-1", "ref-1", "run-1")


def test_approval_events_last_line_wins_and_pending_ids(out_dir):
    assert rs.append_approval_event({"approval_id": "a1", "state": "pending"}, out_dir)
    assert rs.append_approval_event({"approval_id": "a2", "state": "pending"}, out_dir)
    assert rs.append_approval_event({"approval_id": "a1", "state": "STATE_APPROVED"}, out_dir)

    latest = rs.latest_approval_states(out_dir)
    assert latest["a1"]["state"] == "approved"
    assert latest["a1"]["state_raw"] == "STATE_APPROVED"
    assert latest["a2"]["state"] == "pending"
    assert rs.pending_approval_ids(out_dir) == ["a2"]
    files = {p.name for p in out_dir.iterdir()}
    assert not any(name.endswith(".tmp") for name in files)


def test_append_approval_event_without_id_is_refused(out_dir):
    assert rs.append_approval_event({"state": "pending"}, out_dir) is False
    assert rs.read_approval_events(out_dir) == []


def test_unknown_approval_state_kept_raw_not_guessed(out_dir):
    rs.append_approval_event({"approval_id": "a9", "state": "STATE_WEIRD"}, out_dir)
    rec = rs.latest_approval_states(out_dir)["a9"]
    assert rec["state"] == "unknown"
    assert rec["state_raw"] == "STATE_WEIRD"


def test_reconcile_declined_and_expired_are_not_approved_not_discrepancies(out_dir):
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "ts": ts, "approval_id": "d1"}, out_dir)
    rs.append_placed({"symbol": "MSFT", "side": "buy", "ts": ts, "approval_id": "e1"}, out_dir)
    rs.append_approval_event({"approval_id": "d1", "state": "pending"}, out_dir)
    rs.append_approval_event({"approval_id": "d1", "state": "declined"}, out_dir)
    rs.append_approval_event({"approval_id": "e1", "state": "expired"}, out_dir)

    report = rs.reconcile(out_dir, orders_fetcher=lambda: [], symbol_resolver=_resolver({}), force=True)
    assert report["ok"] is True
    assert report["unmatched_placed"] == []
    assert sorted(r["symbol"] for r in report["not_approved"]) == ["AAPL", "MSFT"]
    assert {r["approval_state"] for r in report["not_approved"]} == {"declined", "expired"}
    assert report["awaiting_approval"] == []


def test_reconcile_pending_approval_is_awaiting(out_dir):
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "ts": ts, "approval_id": "p1"}, out_dir)
    rs.append_approval_event({"approval_id": "p1", "state": "pending"}, out_dir)

    report = rs.reconcile(out_dir, orders_fetcher=lambda: [], symbol_resolver=_resolver({}), force=True)
    assert report["ok"] is True
    assert [r["symbol"] for r in report["awaiting_approval"]] == ["AAPL"]
    assert report["unmatched_placed"] == []


def test_reconcile_approved_but_unfilled_or_unrecorded_stays_unmatched(out_dir):
    """An approved request with no fill, or one whose outcome was never
    recorded, is still a real gap — it must not be hidden in a quiet bucket."""
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "ts": ts, "approval_id": "ok1"}, out_dir)
    rs.append_placed({"symbol": "MSFT", "side": "buy", "ts": ts, "approval_id": "nolog"}, out_dir)
    rs.append_approval_event({"approval_id": "ok1", "state": "approved"}, out_dir)

    report = rs.reconcile(out_dir, orders_fetcher=lambda: [], symbol_resolver=_resolver({}), force=True)
    assert report["ok"] is False
    assert sorted(r["symbol"] for r in report["unmatched_placed"]) == ["AAPL", "MSFT"]
    assert report["not_approved"] == [] and report["awaiting_approval"] == []


def test_approved_and_filled_matches_normally(out_dir):
    ts = "2026-07-06T14:00:00+00:00"
    rs.append_placed({"symbol": "AAPL", "side": "buy", "qty": 3, "ts": ts, "approval_id": "f1"}, out_dir)
    rs.append_approval_event({"approval_id": "f1", "state": "approved"}, out_dir)
    orders = [_raw_order("url/AAPL/", "buy", 3, 190.0, ts, "o1")]
    report = rs.reconcile(out_dir, orders_fetcher=lambda: orders,
                          symbol_resolver=_resolver({"url/AAPL/": "AAPL"}), force=True)
    assert report["ok"] is True
    assert report["filled_matched"] == 1


def test_declined_request_still_blocks_same_day_resubmit(out_dir):
    """A decline is an operator decision: the dedup_key ledger line must keep
    the same symbol/side from being re-submitted that UTC day."""
    d = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    rs.append_placed({"symbol": "AAPL", "side": "buy", "ts": d.isoformat(), "approval_id": "d1"}, out_dir)
    rs.append_approval_event({"approval_id": "d1", "state": "declined"}, out_dir)
    assert rs.already_placed("AAPL", "buy", out_dir, on_date=d) is True
