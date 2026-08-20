"""
tests/test_audit_regime_model.py
=================================
Unit tests for ``scripts/audit_regime_model.py``.

Coverage
--------
* Regression guard for the exact bug class introduced by PR #791 and fixed
  by c49cc1ce/3ec2b433: a name used only in a ``from __future__ import
  annotations``-deferred annotation (``Tuple``/``Optional``) but never
  imported. ``typing.get_type_hints()`` forces those deferred string
  annotations to actually resolve at call time, which is exactly what
  ruff's static ``F821`` check (and, previously, nothing at runtime) also
  cares about -- a plain ``import scripts.audit_regime_model`` alone does
  NOT catch this, since the annotation string is never evaluated just by
  importing the module.
* ``load_historical_data()``'s real SQLite read path: happy path shape/dtype,
  the ``BAMLH0A0HYM2``-absent -> ``None`` (never a fabricated series)
  contract, and the three failure modes (missing DB file, empty
  ``price_bars``, empty ``macro_history``).
* A cheap argparse-only smoke test for ``main()``'s CLI surface -- flag
  parsing only, never invoking the heavy HMM fit/walk-forward body.

``main()`` itself is deliberately NOT exercised end-to-end here (per this
suite's own scope decision) -- it pulls in ``HMMRegimeDetector.fit``, causal
walk-forward evaluation, and a full model-comparison grid, all already
covered by ``tests/test_regime_diagnostics.py`` and ``tests/test_hmm_*``.
"""

from __future__ import annotations

import sqlite3
import typing
from pathlib import Path

import pandas as pd
import pytest

import scripts.audit_regime_model as audit_regime_model
from scripts.audit_regime_model import load_historical_data
from settings import settings

# ---------------------------------------------------------------------------
# DDL matching data/historical_store.py's real schema (Phase 1 price_bars,
# Phase 3 macro_history) -- see that module's own DDL constants. Only the
# columns load_historical_data() actually SELECTs are strictly required, but
# mirroring the full real schema keeps this fixture DB honest rather than a
# stripped-down stand-in that could mask a query mismatch.
# ---------------------------------------------------------------------------

_PRICE_BARS_DDL = """
CREATE TABLE IF NOT EXISTS price_bars (
    symbol     TEXT    NOT NULL,
    date       TEXT    NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    adj_close  REAL,
    volume     INTEGER,
    source     TEXT    NOT NULL,
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (symbol, date)
)
"""

_MACRO_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS macro_history (
    series_id   TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
)
"""


def _make_db(db_path: Path, *, with_spy: bool = True, macro_series: dict[str, list[float]] | None = None) -> None:
    """Builds a throwaway on-disk SQLite DB at ``db_path`` with the real
    ``price_bars``/``macro_history`` schema, matching
    ``data/historical_store.py``'s DDL (see tests/test_registry_load.py and
    tests/test_train_meta_labelers.py for this repo's LOCAL_DATA_ROOT
    monkeypatch idiom this file reuses below).

    ``macro_series`` maps series_id -> list of values (one row per day,
    aligned by index to a shared date range); omit a series_id entirely to
    simulate it never having been fetched.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_PRICE_BARS_DDL)
        conn.execute(_MACRO_HISTORY_DDL)

        dates = pd.date_range("2024-01-01", periods=10, freq="B")

        if with_spy:
            for i, d in enumerate(dates):
                conn.execute(
                    "INSERT INTO price_bars (symbol, date, open, high, low, close, "
                    "adj_close, volume, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "SPY",
                        d.strftime("%Y-%m-%d"),
                        450.0 + i,
                        451.0 + i,
                        449.0 + i,
                        450.5 + i,
                        450.5 + i,
                        1_000_000,
                        "test",
                        "2024-01-10T00:00:00",
                    ),
                )

        if macro_series:
            for series_id, values in macro_series.items():
                for d, v in zip(dates, values):
                    conn.execute(
                        "INSERT INTO macro_history (series_id, date, value, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (series_id, d.strftime("%Y-%m-%d"), v, "test", "2024-01-10T00:00:00"),
                    )

        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def local_data_root(tmp_path, monkeypatch) -> Path:
    """Points ``settings.LOCAL_DATA_ROOT`` at a private tmp_path directory,
    so ``load_historical_data()``'s first candidate path
    (``settings.LOCAL_DATA_ROOT / "quant_platform.db"``) resolves there
    instead of touching any real/shared database -- avoids ever writing to
    a real cwd-relative ``quant_platform.db`` from a test process (unsafe
    under parallel test runs).
    """
    fake_local = tmp_path / "stockpy_local"
    fake_local.mkdir()
    monkeypatch.setattr(settings, "LOCAL_DATA_ROOT", fake_local)
    return fake_local


# ===========================================================================
# 1. Import/annotation-resolution regression test
# ===========================================================================

class TestAnnotationsResolve:
    """Regression coverage for PR #791's ``Tuple``/``Optional`` used in
    ``load_historical_data``'s return annotation without the corresponding
    ``from typing import Optional, Tuple`` import. ``from __future__ import
    annotations`` defers annotations to plain strings at class/function
    definition time -- it does NOT mean they're never evaluated; ruff's
    ``F821`` parses them statically regardless of the deferral, and any
    real runtime consumer of ``__annotations__`` (e.g. ``get_type_hints``,
    used by some serialization/validation libraries) forces the exact same
    resolution and would raise ``NameError`` just as loudly. A bare
    ``import scripts.audit_regime_model`` alone does NOT exercise this --
    the string annotation is inert until something resolves it.
    """

    def test_get_type_hints_resolves_load_historical_data(self) -> None:
        hints = typing.get_type_hints(audit_regime_model.load_historical_data)

        # Sanity-check the resolved structure rather than just "didn't raise":
        # the return annotation must actually be a tuple[...] type, proving
        # `Tuple` (and by extension `Optional`, used inside the tuple's last
        # element) genuinely resolved to real typing objects, not silently
        # skipped.
        assert "return" in hints
        return_hint = hints["return"]
        assert typing.get_origin(return_hint) is tuple

        args = typing.get_args(return_hint)
        assert len(args) == 4
        # Last element is Optional[pd.Series] == Union[pd.Series, None]
        assert typing.get_origin(args[-1]) is typing.Union
        assert type(None) in typing.get_args(args[-1])

    def test_get_type_hints_resolves_main(self) -> None:
        # main() -> int; no Tuple/Optional here, but resolving it too is a
        # cheap, complete guard against the same bug class anywhere else in
        # this module's annotations.
        hints = typing.get_type_hints(audit_regime_model.main)
        assert hints.get("return") is int


# ===========================================================================
# 2. load_historical_data() behavior against a real on-disk SQLite DB
# ===========================================================================

class TestLoadHistoricalData:
    def test_happy_path_returns_expected_shapes_and_dtypes(self, local_data_root, monkeypatch):
        db_path = local_data_root / "quant_platform.db"
        _make_db(
            db_path,
            with_spy=True,
            macro_series={
                "VIXCLS": [15.0 + i for i in range(10)],
                "T10Y2Y": [0.5 + 0.01 * i for i in range(10)],
                "BAMLH0A0HYM2": [3.0 + 0.05 * i for i in range(10)],
            },
        )

        spy_df, vix_s, t10y2y_s, credit_s = load_historical_data()

        assert isinstance(spy_df, pd.DataFrame)
        assert "Close" in spy_df.columns
        assert len(spy_df) == 10
        assert isinstance(spy_df.index, pd.DatetimeIndex)

        assert isinstance(vix_s, pd.Series)
        assert isinstance(t10y2y_s, pd.Series)
        assert len(vix_s) == 10
        assert len(t10y2y_s) == 10

        assert credit_s is not None
        assert isinstance(credit_s, pd.Series)
        assert len(credit_s) == 10
        assert pd.api.types.is_float_dtype(credit_s.dtype)

    def test_missing_credit_spread_series_is_none_not_fabricated(self, local_data_root):
        """BAMLH0A0HYM2 absent from macro_history -> credit_series is None,
        never a fabricated empty/zero Series (CONSTRAINT #4 convention)."""
        db_path = local_data_root / "quant_platform.db"
        _make_db(
            db_path,
            with_spy=True,
            macro_series={
                "VIXCLS": [15.0] * 10,
                "T10Y2Y": [0.5] * 10,
                # BAMLH0A0HYM2 deliberately omitted.
            },
        )

        _, _, _, credit_s = load_historical_data()
        assert credit_s is None

    def test_missing_db_file_raises_file_not_found(self, local_data_root, tmp_path, monkeypatch):
        # local_data_root exists but no quant_platform.db was ever written
        # there. The function's second candidate is Path("quant_platform.db")
        # relative to the CWD -- and this checkout's own repo root happens to
        # have a real quant_platform.db, so the test must chdir into a bare
        # tmp_path directory (never the real repo root) to make that fallback
        # genuinely absent too, rather than accidentally reading a real
        # shared database from a test process.
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        with pytest.raises(FileNotFoundError):
            load_historical_data()

    def test_empty_price_bars_raises_value_error(self, local_data_root):
        db_path = local_data_root / "quant_platform.db"
        _make_db(
            db_path,
            with_spy=False,
            macro_series={
                "VIXCLS": [15.0] * 10,
                "T10Y2Y": [0.5] * 10,
                "BAMLH0A0HYM2": [3.0] * 10,
            },
        )

        with pytest.raises(ValueError, match="No SPY price bars"):
            load_historical_data()

    def test_empty_macro_history_raises_value_error(self, local_data_root):
        db_path = local_data_root / "quant_platform.db"
        _make_db(db_path, with_spy=True, macro_series=None)

        with pytest.raises(ValueError, match="No macro series"):
            load_historical_data()


# ===========================================================================
# 3. Cheap argparse-only smoke test for main()'s CLI surface
# ===========================================================================

class TestArgparseSmoke:
    """Builds the same ``argparse.ArgumentParser`` main() constructs and
    exercises only ``parse_args`` -- never touches settings, the database,
    or the HMM fit/walk-forward body. Kept in sync manually with main()'s
    own parser definition (scripts/audit_regime_model.py:84-89); if that
    drifts, this test drifting out of date is an acceptable, low-cost
    tradeoff for not having to refactor main() to expose a testable parser
    builder purely for this smoke test.
    """

    def _build_parser(self):
        import argparse

        parser = argparse.ArgumentParser(description="Audit Gaussian HMM Regime Detector.")
        parser.add_argument("--compare", action="store_true")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--states", type=int, default=None)
        parser.add_argument("--cov", type=str, default=None)
        parser.add_argument("--output", type=str, default=None)
        return parser

    def test_default_args_parse(self) -> None:
        args = self._build_parser().parse_args([])
        assert args.compare is False
        assert args.json is False
        assert args.states is None
        assert args.cov is None
        assert args.output is None

    def test_all_flags_parse(self, tmp_path) -> None:
        out_path = str(tmp_path / "audit.json")
        args = self._build_parser().parse_args(
            ["--compare", "--json", "--states", "4", "--cov", "full", "--output", out_path]
        )
        assert args.compare is True
        assert args.json is True
        assert args.states == 4
        assert args.cov == "full"
        assert args.output == out_path
