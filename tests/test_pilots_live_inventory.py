"""Tests for ``pilots/live_inventory.py`` — the coverage-reconciliation
diagnostic reader backing ``GET /universe/coverage``.

Covers: cold start (no cache file), corrupt cache, empty cache, a realistic
mixed-coverage cache (counts + row shaping), and the NaN->None honesty
conversion (the underlying ``data.portfolio_sync.write_cache`` persists NaN
as a literal JSON token via plain ``json.dumps``, which this reader must
never re-surface — both because CONSTRAINT #4 forbids a fabricated 0.0/NaN
sentinel, and because a literal ``NaN`` token is invalid JSON the frontend's
``JSON.parse`` cannot consume).
"""
from __future__ import annotations

import json

from pilots import live_inventory as li


def _write_cache(tmp_path, **fields):
    payload = {
        "generated_at": "2026-07-20T12:00:00+00:00",
        "positions": [],
        "watchlists": {},
        "provider_source": "alpaca",
        "fundamentals_source": "yahoo",
        "symbols": {},
        **fields,
    }
    p = tmp_path / "sync_report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _row(**overrides):
    base = {
        "symbol": "AAPL",
        "coverage": "full",
        "held": True,
        "quantity": 40.0,
        "avg_cost": 150.0,
        "current_price": 224.15,
        "cost_basis_delta_per_share": 74.15,
        "market_value": 8966.0,
        "is_stale_quote": False,
        "quote_source": "alpaca",
        "has_fundamentals": True,
        "forecast_available": True,
        "watchlists": ["Tech"],
        "diagnostic": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Degradation (CONSTRAINT #6)
# ---------------------------------------------------------------------------


def test_missing_cache_has_reason_and_empty_shape(tmp_path):
    out = li.universe_coverage(cache_path=str(tmp_path / "does_not_exist.json"))
    assert out["reason"] is not None
    assert out["n_total"] == 0
    assert out["symbols"] == []
    assert out["counts"] == {
        "full": 0, "stale": 0, "quotes_only": 0,
        "equity_only": 0, "uncovered": 0, "unknown": 0,
    }
    assert out["generated_at"] is None
    assert out["provider_source"] is None


def test_corrupt_cache_never_raises(tmp_path):
    p = tmp_path / "sync_report.json"
    p.write_text("{not valid json", encoding="utf-8")
    out = li.universe_coverage(cache_path=str(p))  # must not raise
    assert out["reason"] is not None
    assert out["symbols"] == []


def test_empty_symbols_dict_has_reason(tmp_path):
    path = _write_cache(tmp_path, symbols={})
    out = li.universe_coverage(cache_path=path)
    assert out["reason"] == li._EMPTY_CACHE_REASON
    assert out["n_total"] == 0
    # top-level fields ARE present even with zero symbols, unlike a missing cache.
    assert out["provider_source"] == "alpaca"
    assert out["generated_at"] == "2026-07-20T12:00:00+00:00"


def test_symbols_not_a_dict_degrades_to_empty(tmp_path):
    path = _write_cache(tmp_path, symbols="not-a-dict")
    out = li.universe_coverage(cache_path=path)
    assert out["symbols"] == []
    assert out["n_total"] == 0


def test_malformed_row_without_symbol_key_is_dropped(tmp_path):
    path = _write_cache(tmp_path, symbols={
        "AAPL": _row(symbol="AAPL"),
        "bad": {"coverage": "full"},  # no "symbol" key at all
    })
    out = li.universe_coverage(cache_path=path)
    assert out["n_total"] == 1
    assert out["symbols"][0]["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Happy path — counts + row shaping
# ---------------------------------------------------------------------------


def test_counts_and_rows_for_mixed_coverage(tmp_path):
    path = _write_cache(tmp_path, symbols={
        "AAPL": _row(symbol="AAPL", coverage="full"),
        "MSFT": _row(symbol="MSFT", coverage="stale"),
        "ZZZ": _row(symbol="ZZZ", coverage="equity_only", held=True),
        "QQQ": _row(symbol="QQQ", coverage="quotes_only"),
        "UNK": _row(symbol="UNK", coverage="uncovered"),
    })
    out = li.universe_coverage(cache_path=path)
    assert out["reason"] is None
    assert out["n_total"] == 5
    assert out["counts"] == {
        "full": 1, "stale": 1, "quotes_only": 1,
        "equity_only": 1, "uncovered": 1, "unknown": 0,
    }
    # sorted by symbol
    assert [r["symbol"] for r in out["symbols"]] == ["AAPL", "MSFT", "QQQ", "UNK", "ZZZ"]


def test_unrecognized_coverage_value_buckets_as_unknown(tmp_path):
    path = _write_cache(tmp_path, symbols={
        "AAPL": _row(coverage="some_future_status"),
    })
    out = li.universe_coverage(cache_path=path)
    assert out["counts"]["unknown"] == 1
    assert out["symbols"][0]["coverage"] == "unknown"


def test_full_row_shape(tmp_path):
    path = _write_cache(tmp_path, symbols={"AAPL": _row()})
    row = li.universe_coverage(cache_path=path)["symbols"][0]
    assert row == {
        "symbol": "AAPL",
        "coverage": "full",
        "held": True,
        "quantity": 40.0,
        "avg_cost": 150.0,
        "current_price": 224.15,
        "cost_basis_delta_per_share": 74.15,
        "market_value": 8966.0,
        "is_stale_quote": False,
        "quote_source": "alpaca",
        "has_fundamentals": True,
        "forecast_available": True,
        "watchlists": ["Tech"],
        "diagnostic": None,  # empty string -> None (CONSTRAINT #4)
    }


# ---------------------------------------------------------------------------
# Honesty — NaN -> None, never a literal NaN JSON token (CONSTRAINT #4/#6)
# ---------------------------------------------------------------------------


def test_nan_numeric_fields_null_not_fabricated(tmp_path):
    path = _write_cache(tmp_path, symbols={
        "ZZZ": _row(
            symbol="ZZZ", coverage="equity_only",
            current_price=float("nan"),
            cost_basis_delta_per_share=float("nan"),
            market_value=float("nan"),
            quote_source="", diagnostic="quote:NotFoundError",
        ),
    })
    # The cache file itself round-trips a NaN literal via plain json.dumps —
    # confirm the fixture actually exercises that path, not a pre-nulled one.
    text = open(path, encoding="utf-8").read()
    assert "NaN" in text

    out = li.universe_coverage(cache_path=path)
    row = out["symbols"][0]
    assert row["current_price"] is None
    assert row["cost_basis_delta_per_share"] is None
    assert row["market_value"] is None
    assert row["quote_source"] is None  # empty string -> None
    assert row["diagnostic"] == "quote:NotFoundError"

    # Never a fabricated 0.0.
    assert row["current_price"] != 0.0

    # The re-serialized API response must be valid JSON — no literal NaN token.
    reserialized = json.dumps(out)
    assert "NaN" not in reserialized
    # And it must round-trip through a strict JSON parser (allow_nan=False),
    # exactly the constraint a browser's JSON.parse enforces.
    json.loads(reserialized)


def test_infinite_value_is_nulled(tmp_path):
    path = _write_cache(tmp_path, symbols={
        "AAPL": _row(market_value=float("inf")),
    })
    out = li.universe_coverage(cache_path=path)
    assert out["symbols"][0]["market_value"] is None


def test_quantity_zero_is_kept_not_nulled(tmp_path):
    # A genuine "not held / zero shares" quantity is a real 0.0, not an
    # honest-null case — must not be conflated with a missing value.
    path = _write_cache(tmp_path, symbols={
        "AAPL": _row(held=False, quantity=0.0, avg_cost=None),
    })
    row = li.universe_coverage(cache_path=path)["symbols"][0]
    assert row["quantity"] == 0.0
    assert row["avg_cost"] is None
