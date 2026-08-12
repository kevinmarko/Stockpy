"""
tests/test_production_steps_fund_dtos_dead_letter.py
=======================================================
Regression coverage for pipeline/production_steps.py's Finding 6 fix:
ProcessingStep's fund_dtos construction loop previously had no per-ticker
try/except, so a single malformed ``ctx.fund_raw[ticker]`` entry (e.g.
``{'info': None}``, which raises AttributeError inside
FundamentalDataDTO.from_raw_dict()'s ``info.get(...)`` calls) would abort
the loop for every remaining ticker -- breaking this codebase's "one bad
symbol must never abort a whole cycle" dead-letter convention already used
elsewhere in this same file's options/forecasting/strategy-eval loops.

Targets ProcessingStep.run() directly with a hand-built RunContext and a
real (not mocked) ProcessingEngine -- processing_engine.py is pure
pandas/numpy, no heavy engine imports, so this is cheap (mirrors
tests/test_processing_engine.py's own direct-construction approach).
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from dto_models import MacroEconomicDTO
from pipeline.context import RunContext
from pipeline.production_steps import ProcessingStep


def _ohlcv(n: int, seed: int, start: float = 100.0) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    close = start + np.cumsum(rng.normal(0, 1.0, n))
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0, 1.0, n)
    low = close - rng.uniform(0, 1.0, n)
    open_p = close + rng.normal(0, 0.3, n)
    volume = rng.randint(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


VALID_INFO = {
    "shortName": "Acme Corp", "sector": "Technology", "trailingPE": 20.0,
    "priceToBook": 3.0, "bookValue": 10.0, "trailingEps": 5.0,
    "dividendYield": 0.01, "payoutRatio": 0.2, "marketCap": 1_000_000_000.0,
    "currentPrice": 150.0, "beta": 1.1,
}


def _make_ctx(fund_raw: dict, tech_raw: dict) -> RunContext:
    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda *a, **k: None,
        get_provider_fn=lambda *a, **k: None,
        fetch_bars_fn=lambda *a, **k: {},
        build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
    )
    ctx.fund_raw = fund_raw
    ctx.tech_raw = tech_raw
    ctx.macro_dto = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.5, inflation_rate=2.0,
    )
    return ctx


class TestFundDtosDeadLetter:
    def test_malformed_entry_does_not_abort_the_cycle(self):
        """One {'info': None} entry among otherwise-valid tickers: the
        cycle must complete and only that ticker's fundamentals degrade."""
        fund_raw = {
            "GOOD1": {"info": VALID_INFO},
            "BAD": {"info": None},
            "GOOD2": {"info": VALID_INFO},
        }
        tech_raw = {t: _ohlcv(60, seed=i) for i, t in enumerate(fund_raw)}
        ctx = _make_ctx(fund_raw, tech_raw)

        step = ProcessingStep()
        step.run(ctx)  # must not raise

        fund_dtos = ctx.context_extras["fund_dtos"]
        assert "GOOD1" in fund_dtos
        assert "GOOD2" in fund_dtos
        assert "BAD" not in fund_dtos, "malformed entry must be skipped, not fabricated"

        # The rest of the cycle still ran to completion.
        assert ctx.dashboard_df is not None
        assert set(ctx.dashboard_df["Symbol"]) == {"GOOD1", "BAD", "GOOD2"}

    def test_all_valid_entries_all_present(self):
        """No malformed entries: behavior is unchanged from before the fix."""
        fund_raw = {
            "GOOD1": {"info": VALID_INFO},
            "GOOD2": {"info": VALID_INFO},
        }
        tech_raw = {t: _ohlcv(60, seed=i) for i, t in enumerate(fund_raw)}
        ctx = _make_ctx(fund_raw, tech_raw)

        step = ProcessingStep()
        step.run(ctx)

        fund_dtos = ctx.context_extras["fund_dtos"]
        assert set(fund_dtos.keys()) == {"GOOD1", "GOOD2"}

    def test_multiple_malformed_entries_only_skip_themselves(self):
        """Several independently-malformed tickers must each degrade on
        their own without taking any other ticker (good or bad) down with
        them."""
        fund_raw = {
            "GOOD1": {"info": VALID_INFO},
            "BAD1": {"info": None},
            "GOOD2": {"info": VALID_INFO},
            "BAD2": {"info": None},
            "GOOD3": {"info": VALID_INFO},
        }
        tech_raw = {t: _ohlcv(60, seed=i) for i, t in enumerate(fund_raw)}
        ctx = _make_ctx(fund_raw, tech_raw)

        step = ProcessingStep()
        step.run(ctx)  # must not raise

        fund_dtos = ctx.context_extras["fund_dtos"]
        assert set(fund_dtos.keys()) == {"GOOD1", "GOOD2", "GOOD3"}
