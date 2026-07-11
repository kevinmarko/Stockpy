"""
tests/test_equity_curve_metrics.py
===================================
Offline unit tests for evaluation_engine.calculate_equity_curve_metrics().

No DB fixtures needed -- the function takes a plain DataFrame (the shape
returned by data.historical_store.HistoricalStore.account_snapshot_history()).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from evaluation_engine import calculate_equity_curve_metrics, MIN_SNAPSHOTS_FOR_STATS


def _mk_df(equities: list, start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [start + timedelta(days=i) for i in range(len(equities))]
    return pd.DataFrame(
        {
            "fetched_at": dates,
            "total_equity": equities,
            "buying_power": [1000.0] * len(equities),
            "total_dividends": [0.0] * len(equities),
        }
    )


class TestKnownDrawdownAndSharpe:
    def test_known_drawdown_max_dd_duration_and_cagr(self) -> None:
        n = MIN_SNAPSHOTS_FOR_STATS + 10
        # Rise from 100 to 110 (day 0-9), fall to 93.5 (day 9-14, a 15% drawdown
        # from the 110 peak), then partially recover and stay flat.
        equities = [100.0 + i for i in range(10)]  # 100..109
        equities += list(np.linspace(109.0, 93.5, 6))[1:]  # -> 93.5 by day 14
        equities += [93.5 + i * 0.5 for i in range(1, n - len(equities) + 1)]
        equities = equities[:n]

        df = _mk_df(equities)
        result = calculate_equity_curve_metrics(df)

        assert result["n_snapshots"] == n

        # Hand-computed max drawdown: peak=109 at day 9, trough=93.5 at day 14.
        expected_dd = (93.5 - 109.0) / 109.0
        assert result["max_drawdown"] == pytest.approx(expected_dd, abs=1e-6)

        # Max drawdown duration: from day 9 (peak) to day 14 (trough) is the
        # underwater window before a new high is made -- since the curve
        # never exceeds 109 again in this construction, the underwater run
        # spans from day 10 (first day below 109) through the last day.
        assert result["max_drawdown_duration_days"] > 0

        # CAGR computed independently via the closed-form formula.
        start_val = equities[0]
        end_val = equities[-1]
        days_elapsed = n - 1
        expected_cagr = (end_val / start_val) ** (365.25 / days_elapsed) - 1.0
        assert result["cagr"] == pytest.approx(expected_cagr, abs=1e-6)

        # Sharpe computed independently via numpy from the same equity array.
        eq_arr = np.array(equities, dtype=float)
        rets = eq_arr[1:] / eq_arr[:-1] - 1.0
        expected_sharpe = (rets.mean() - 0.0) / rets.std(ddof=1) * math.sqrt(252)
        assert result["sharpe_ratio"] == pytest.approx(expected_sharpe, abs=1e-6)

        # Calmar = cagr / abs(max_drawdown), computed independently.
        expected_calmar = expected_cagr / abs(expected_dd)
        assert result["calmar_ratio"] == pytest.approx(expected_calmar, abs=1e-6)


class TestEmptyAndInsufficientData:
    def test_empty_dataframe_returns_all_nan(self) -> None:
        result = calculate_equity_curve_metrics(pd.DataFrame())
        assert math.isnan(result["sharpe_ratio"])
        assert math.isnan(result["calmar_ratio"])
        assert math.isnan(result["max_drawdown"])
        assert math.isnan(result["max_drawdown_duration_days"])
        assert math.isnan(result["cagr"])
        assert result["n_snapshots"] == 0

    def test_none_returns_all_nan(self) -> None:
        result = calculate_equity_curve_metrics(None)
        assert math.isnan(result["sharpe_ratio"])
        assert result["n_snapshots"] == 0

    def test_missing_columns_returns_all_nan(self) -> None:
        df = pd.DataFrame({"foo": [1, 2, 3]})
        result = calculate_equity_curve_metrics(df)
        assert math.isnan(result["sharpe_ratio"])
        assert result["n_snapshots"] == 0

    def test_fewer_than_min_snapshots_returns_actual_count(self) -> None:
        n = max(1, MIN_SNAPSHOTS_FOR_STATS - 5)
        df = _mk_df([10000.0 + i * 10 for i in range(n)])
        result = calculate_equity_curve_metrics(df)
        assert math.isnan(result["sharpe_ratio"])
        assert math.isnan(result["calmar_ratio"])
        assert result["n_snapshots"] == n


class TestFlatEquitySeries:
    def test_flat_series_nans_sharpe_calmar_but_not_drawdown_or_cagr(self) -> None:
        n = MIN_SNAPSHOTS_FOR_STATS + 5
        df = _mk_df([10000.0] * n)
        result = calculate_equity_curve_metrics(df)

        assert math.isnan(result["sharpe_ratio"])
        assert math.isnan(result["calmar_ratio"])
        # A flat curve never dips below its own peak -- a true 0.0, not fabricated.
        assert result["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
        # A flat curve has genuinely 0% growth -- a true 0.0, not fabricated.
        assert result["cagr"] == pytest.approx(0.0, abs=1e-6)
        assert result["n_snapshots"] == n


class TestMultiSnapshotPerDayDedup:
    def test_intraday_duplicates_collapse_to_last_value_per_day(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        n_days = MIN_SNAPSHOTS_FOR_STATS + 10

        # Pre-deduped "canonical" one-row-per-day series.
        canonical_equities = [10000.0 + i * 15 - (i % 7) * 20 for i in range(n_days)]
        canonical_df = _mk_df(canonical_equities, start=start)

        # Same series, but day 0 has 3 intraday snapshots -- the LAST one
        # (at a later timestamp) matches the canonical day-0 value; the
        # earlier two are distinguishable decoys.
        rows = []
        rows.append({"fetched_at": start, "total_equity": 1.0, "buying_power": 1000.0, "total_dividends": 0.0})
        rows.append({"fetched_at": start + timedelta(hours=6), "total_equity": 2.0, "buying_power": 1000.0, "total_dividends": 0.0})
        rows.append({
            "fetched_at": start + timedelta(hours=12),
            "total_equity": canonical_equities[0],
            "buying_power": 1000.0,
            "total_dividends": 0.0,
        })
        for i in range(1, n_days):
            rows.append({
                "fetched_at": start + timedelta(days=i),
                "total_equity": canonical_equities[i],
                "buying_power": 1000.0,
                "total_dividends": 0.0,
            })
        dup_df = pd.DataFrame(rows)

        canonical_result = calculate_equity_curve_metrics(canonical_df)
        dup_result = calculate_equity_curve_metrics(dup_df)

        # Dedup correctly collapses to the same VALUE sequence -- sharpe and
        # max_drawdown depend only on the equity values, not on the exact
        # intra-day timestamp of the surviving row, so these match exactly.
        assert dup_result["n_snapshots"] == canonical_result["n_snapshots"]
        assert dup_result["sharpe_ratio"] == pytest.approx(canonical_result["sharpe_ratio"], abs=1e-9)
        assert dup_result["max_drawdown"] == pytest.approx(canonical_result["max_drawdown"], abs=1e-9)

        # CAGR/Calmar depend on the elapsed TIME span, and the deduped day-0
        # row here deliberately keeps its real (later, hour-12) timestamp
        # rather than the canonical series' midnight timestamp -- a genuine,
        # correct half-day of jitter, not a bug. A loose relative tolerance
        # confirms they're in the same ballpark without asserting an exact
        # match that would require unrealistic identical intra-day timing.
        assert dup_result["cagr"] == pytest.approx(canonical_result["cagr"], rel=0.05)
        assert not math.isnan(canonical_result["calmar_ratio"])
        assert not math.isnan(dup_result["calmar_ratio"])
        assert dup_result["calmar_ratio"] == pytest.approx(canonical_result["calmar_ratio"], rel=0.05)
