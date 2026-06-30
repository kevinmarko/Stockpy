"""
tests/test_robinhood_orders.py — Robinhood realized-P&L engine tests
====================================================================
Covers ``data/robinhood_orders.py`` (READ-ONLY, ADVISORY ONLY):

* FIFO round-trip reconstruction — full/partial lot matching, multi-symbol
  isolation, chronological ordering, short/excess-sell dropping (CONSTRAINT #4)
* realized-performance summary — win rate, profit factor, averages, NaN-shaped
  empty result (no fabricated zeros)
* Robinhood order-record parsing — filled-only filter, symbol resolution,
  timestamp fallbacks, malformed-record tolerance
* cache round-trip + fetch dead-letter resilience (injected fetcher; no network)
* ADVISORY-ONLY source guard — no order-submission keywords

All tests are fully offline — every Robinhood call is injected/monkeypatched.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from data.robinhood_orders import (
    ClosedTrade,
    OrderFill,
    fetch_filled_orders,
    parse_orders,
    realized_performance,
    realized_pnl_summary,
    reconstruct_closed_trades,
)
import data.robinhood_orders as rho


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fill(symbol: str, side: str, qty: float, price: float, day: int) -> OrderFill:
    return OrderFill(
        symbol=symbol, side=side, quantity=qty, price=price,
        timestamp=datetime(2026, 1, day, 15, 0, tzinfo=timezone.utc),
        order_id=f"{symbol}-{side}-{day}",
    )


# ===========================================================================
# FIFO reconstruction
# ===========================================================================


class TestFifoReconstruction:
    def test_simple_round_trip(self):
        trades = reconstruct_closed_trades([
            _fill("AAPL", "buy", 10, 100.0, 1),
            _fill("AAPL", "sell", 10, 120.0, 5),
        ])
        assert len(trades) == 1
        t = trades[0]
        assert t.quantity == 10
        assert t.entry_price == 100.0 and t.exit_price == 120.0
        assert t.realized_pnl == pytest.approx(200.0)
        assert t.return_pct == pytest.approx(20.0)
        assert t.holding_days == pytest.approx(4.0)

    def test_partial_lot_split_across_two_buys(self):
        # buy 10@100, buy 10@110, sell 15@120 → two closed trades (10 + 5).
        trades = reconstruct_closed_trades([
            _fill("AAPL", "buy", 10, 100.0, 1),
            _fill("AAPL", "buy", 10, 110.0, 2),
            _fill("AAPL", "sell", 15, 120.0, 10),
        ])
        assert len(trades) == 2
        assert trades[0].quantity == 10 and trades[0].entry_price == 100.0
        assert trades[1].quantity == 5 and trades[1].entry_price == 110.0
        assert sum(t.realized_pnl for t in trades) == pytest.approx(200.0 + 50.0)

    def test_remaining_lot_kept_for_later_sell(self):
        # buy 10@100, sell 4@110, sell 6@120 → two trades from one lot.
        trades = reconstruct_closed_trades([
            _fill("AAPL", "buy", 10, 100.0, 1),
            _fill("AAPL", "sell", 4, 110.0, 2),
            _fill("AAPL", "sell", 6, 120.0, 3),
        ])
        assert len(trades) == 2
        assert trades[0].quantity == 4 and trades[0].exit_price == 110.0
        assert trades[1].quantity == 6 and trades[1].exit_price == 120.0

    def test_loss_trade(self):
        trades = reconstruct_closed_trades([
            _fill("XYZ", "buy", 5, 100.0, 1),
            _fill("XYZ", "sell", 5, 80.0, 2),
        ])
        assert trades[0].realized_pnl == pytest.approx(-100.0)
        assert trades[0].return_pct == pytest.approx(-20.0)

    def test_excess_sell_dropped_not_fabricated(self):
        # Sell exceeds open lots → match what exists, DROP the excess.
        trades = reconstruct_closed_trades([
            _fill("S", "buy", 5, 10.0, 1),
            _fill("S", "sell", 8, 12.0, 2),
        ])
        assert len(trades) == 1
        assert trades[0].quantity == 5  # only the 5 real shares matched

    def test_sell_with_no_buy_yields_nothing(self):
        trades = reconstruct_closed_trades([_fill("S", "sell", 5, 12.0, 2)])
        assert trades == []

    def test_open_position_not_closed(self):
        # Buy with no sell → no closed trade.
        trades = reconstruct_closed_trades([_fill("S", "buy", 5, 10.0, 1)])
        assert trades == []

    def test_multi_symbol_isolation(self):
        trades = reconstruct_closed_trades([
            _fill("AAA", "buy", 1, 10.0, 1),
            _fill("BBB", "buy", 1, 20.0, 1),
            _fill("AAA", "sell", 1, 11.0, 2),
            _fill("BBB", "sell", 1, 19.0, 2),
        ])
        by_sym = {t.symbol: t for t in trades}
        assert by_sym["AAA"].realized_pnl == pytest.approx(1.0)
        assert by_sym["BBB"].realized_pnl == pytest.approx(-1.0)

    def test_output_sorted_by_exit_ts(self):
        trades = reconstruct_closed_trades([
            _fill("AAA", "buy", 1, 10.0, 1),
            _fill("BBB", "buy", 1, 10.0, 1),
            _fill("BBB", "sell", 1, 11.0, 3),
            _fill("AAA", "sell", 1, 11.0, 2),
        ])
        assert [t.symbol for t in trades] == ["AAA", "BBB"]  # exit day 2 before day 3

    def test_zero_and_negative_fills_ignored(self):
        trades = reconstruct_closed_trades([
            _fill("S", "buy", 0, 10.0, 1),
            _fill("S", "buy", 5, 0.0, 1),
            _fill("S", "buy", 5, 10.0, 2),
            _fill("S", "sell", 5, 12.0, 3),
        ])
        assert len(trades) == 1 and trades[0].entry_price == 10.0


# ===========================================================================
# Realized-performance summary
# ===========================================================================


class TestSummary:
    def test_empty_is_nan_shaped(self):
        s = realized_pnl_summary([])
        assert s["n_trades"] == 0
        assert s["total_realized_pnl"] == 0.0  # a sum over zero trades
        for k in ("win_rate", "avg_win", "avg_loss", "profit_factor",
                  "avg_return_pct", "avg_holding_days"):
            assert math.isnan(s[k]), k

    def test_win_rate_and_profit_factor(self):
        trades = reconstruct_closed_trades([
            _fill("A", "buy", 1, 100.0, 1), _fill("A", "sell", 1, 120.0, 2),  # +20
            _fill("B", "buy", 1, 100.0, 1), _fill("B", "sell", 1, 90.0, 2),   # -10
            _fill("C", "buy", 1, 100.0, 1), _fill("C", "sell", 1, 110.0, 2),  # +10
        ])
        s = realized_pnl_summary(trades)
        assert s["n_trades"] == 3
        assert s["win_rate"] == pytest.approx(2 / 3)
        assert s["total_realized_pnl"] == pytest.approx(20.0)
        assert s["gross_profit"] == pytest.approx(30.0)
        assert s["gross_loss"] == pytest.approx(-10.0)
        assert s["profit_factor"] == pytest.approx(3.0)
        assert s["best_trade_pnl"] == pytest.approx(20.0)
        assert s["worst_trade_pnl"] == pytest.approx(-10.0)

    def test_profit_factor_nan_when_no_losses(self):
        trades = reconstruct_closed_trades([
            _fill("A", "buy", 1, 100.0, 1), _fill("A", "sell", 1, 110.0, 2),
        ])
        s = realized_pnl_summary(trades)
        assert s["win_rate"] == 1.0
        assert math.isnan(s["profit_factor"])
        assert math.isnan(s["avg_loss"])

    def test_avg_holding_days(self):
        trades = reconstruct_closed_trades([
            _fill("A", "buy", 1, 100.0, 1), _fill("A", "sell", 1, 110.0, 5),  # 4d
            _fill("B", "buy", 1, 100.0, 1), _fill("B", "sell", 1, 110.0, 3),  # 2d
        ])
        s = realized_pnl_summary(trades)
        assert s["avg_holding_days"] == pytest.approx(3.0)


# ===========================================================================
# Order-record parsing
# ===========================================================================


class TestParseOrders:
    def _resolver(self, url: str) -> Optional[str]:
        return "AAPL" if "uuid1" in url else ("MSFT" if "uuid2" in url else None)

    def test_filled_only(self):
        raw = [
            {"state": "filled", "side": "buy", "cumulative_quantity": "10",
             "average_price": "100", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "a"},
            {"state": "cancelled", "side": "buy", "cumulative_quantity": "5",
             "average_price": "90", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "b"},
            {"state": "queued", "side": "buy", "cumulative_quantity": "5",
             "average_price": "90", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "c"},
        ]
        fills = parse_orders(raw, self._resolver)
        assert len(fills) == 1 and fills[0].order_id == "a"

    def test_symbol_resolution_and_skip_unresolved(self):
        raw = [
            {"state": "filled", "side": "buy", "cumulative_quantity": "1",
             "average_price": "10", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid2/", "id": "a"},
            {"state": "filled", "side": "buy", "cumulative_quantity": "1",
             "average_price": "10", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/unknown/", "id": "b"},
        ]
        fills = parse_orders(raw, self._resolver)
        assert len(fills) == 1 and fills[0].symbol == "MSFT"

    def test_timestamp_fallback_chain(self):
        raw = [{
            "state": "filled", "side": "sell", "cumulative_quantity": "3",
            "average_price": "50", "updated_at": "2026-02-02T10:00:00Z",
            "instrument": "https://x/inst/uuid1/", "id": "z",
        }]
        fills = parse_orders(raw, self._resolver)
        assert len(fills) == 1
        assert fills[0].timestamp.year == 2026 and fills[0].timestamp.month == 2

    def test_zero_qty_or_price_skipped(self):
        raw = [
            {"state": "filled", "side": "buy", "cumulative_quantity": "0",
             "average_price": "10", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "a"},
            {"state": "filled", "side": "buy", "cumulative_quantity": "5",
             "average_price": "0", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "b"},
        ]
        assert parse_orders(raw, self._resolver) == []

    def test_price_fallback_to_price_field(self):
        raw = [{
            "state": "filled", "side": "buy", "cumulative_quantity": "2",
            "average_price": None, "price": "42.5",
            "last_transaction_at": "2026-01-01T15:00:00Z",
            "instrument": "https://x/inst/uuid1/", "id": "p",
        }]
        fills = parse_orders(raw, self._resolver)
        assert len(fills) == 1 and fills[0].price == pytest.approx(42.5)

    def test_malformed_record_skipped(self):
        raw = [
            {"state": "filled", "side": "buy", "cumulative_quantity": "abc",
             "average_price": "10", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "bad"},
            {"state": "filled", "side": "buy", "cumulative_quantity": "1",
             "average_price": "10", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "good"},
        ]
        fills = parse_orders(raw, self._resolver)
        assert len(fills) == 1 and fills[0].order_id == "good"

    def test_empty_input(self):
        assert parse_orders([], self._resolver) == []


# ===========================================================================
# Cache + fetch (injected — no network)
# ===========================================================================


class TestFetchAndCache:
    @pytest.fixture(autouse=True)
    def _isolate_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rho, "_CACHE_PATH", tmp_path / "robinhood_orders.json")

    def _raw(self):
        return [
            {"state": "filled", "side": "buy", "cumulative_quantity": "10",
             "average_price": "100", "last_transaction_at": "2026-01-01T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "a"},
            {"state": "filled", "side": "sell", "cumulative_quantity": "10",
             "average_price": "120", "last_transaction_at": "2026-01-05T15:00:00Z",
             "instrument": "https://x/inst/uuid1/", "id": "b"},
        ]

    def test_fetch_with_injected_fetcher(self):
        fills = fetch_filled_orders(
            force=True,
            orders_fetcher=self._raw,
            symbol_resolver=lambda u: "AAPL",
        )
        assert len(fills) == 2
        assert {f.side for f in fills} == {"buy", "sell"}

    def test_fetch_writes_then_reads_cache(self):
        # First fetch (force) writes the cache.
        fetch_filled_orders(force=True, orders_fetcher=self._raw,
                            symbol_resolver=lambda u: "AAPL")
        # Second fetch (no force) must read the cache WITHOUT calling the fetcher.
        sentinel = {"called": False}

        def _boom():
            sentinel["called"] = True
            raise AssertionError("fetcher must not be called when cache is fresh")

        fills = fetch_filled_orders(force=False, orders_fetcher=_boom,
                                    symbol_resolver=lambda u: "AAPL")
        assert sentinel["called"] is False
        assert len(fills) == 2

    def test_fetch_failure_returns_empty_when_no_cache(self):
        def _boom():
            raise RuntimeError("network down")

        fills = fetch_filled_orders(force=True, orders_fetcher=_boom,
                                    symbol_resolver=lambda u: "AAPL")
        assert fills == []  # dead-letter resilient

    def test_fetch_failure_returns_stale_cache(self):
        # Seed the cache via a good fetch…
        fetch_filled_orders(force=True, orders_fetcher=self._raw,
                            symbol_resolver=lambda u: "AAPL")

        # …then a forced fetch that fails should fall back to the stale cache.
        def _boom():
            raise RuntimeError("network down")

        fills = fetch_filled_orders(force=True, orders_fetcher=_boom,
                                    symbol_resolver=lambda u: "AAPL")
        assert len(fills) == 2

    def test_realized_performance_end_to_end(self):
        perf = realized_performance(force=True, orders_fetcher=self._raw,
                                    symbol_resolver=lambda u: "AAPL")
        assert perf["n_fills"] == 2
        assert perf["summary"]["n_trades"] == 1
        assert perf["summary"]["total_realized_pnl"] == pytest.approx(200.0)
        assert perf["trades"][0].symbol == "AAPL"

    def test_orderfill_dict_roundtrip(self):
        f = _fill("AAPL", "buy", 10, 100.0, 1)
        assert OrderFill.from_dict(f.to_dict()) == f


# ===========================================================================
# Module surface / safety
# ===========================================================================


class TestModuleSurface:
    def test_closedtrade_frozen(self):
        t = ClosedTrade("A", 1, datetime.now(timezone.utc), datetime.now(timezone.utc),
                        1.0, 2.0, 1.0, 100.0, 0.0)
        with pytest.raises(Exception):
            t.symbol = "B"  # type: ignore[misc]

    def test_no_order_submission_keywords_in_source(self):
        src = Path(rho.__file__).read_text(encoding="utf-8").lower()
        for kw in ("submit_order", "place_order", "buy_order", "sell_order",
                   "place_equity_order", "place_option_order", "order_buy",
                   "order_sell", "cancel_order"):
            assert kw not in src, f"forbidden order keyword present: {kw}"
