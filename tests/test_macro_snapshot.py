"""
tests/test_macro_snapshot.py
=============================
Offline tests for ``execution/macro_snapshot.py`` — the zero-network,
DB-cache-only ``MacroEconomicDTO`` loader used as the fallback for
``execution/compose.py::compose_and_emit`` and
``execution/flatten_proposal.py::emit_flatten_proposal`` when no explicit
macro DTO is passed in.

Proves:
  * An empty/never-written DB → ``load_cached_macro_dto()`` returns ``None``
    and logs a WARNING (explicit fail-open, not silent — see the module's
    own docstring).
  * A DB seeded with real cached macro rows → returns a real
    ``MacroEconomicDTO`` reflecting the seeded values, never a fabricated
    neutral default.

No network calls anywhere in this file — seeding goes through
``HistoricalStore.get_macro()`` with an injected mock ``DataEngine``, the
same pattern ``tests/test_historical_store.py`` already uses.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest
from unittest.mock import MagicMock

import settings as settings_mod
from data.historical_store import HistoricalStore
from execution.macro_snapshot import load_cached_macro_dto


def _point_database_url_at(monkeypatch, db_path: str) -> None:
    """Repoint ``db_config.resolve_database_url()`` (and therefore
    ``load_cached_macro_dto``'s internal ``HistoricalStore(readonly=True)``,
    which resolves its db_path the same way) at *db_path*."""
    monkeypatch.setattr(
        settings_mod.settings, "DATABASE_URL", f"sqlite:///{db_path}", raising=False,
    )


def _seed_macro_rows(db_path: str) -> None:
    """Write real VIXCLS/SAHMREALTIME/T10Y2Y/BAMLH0A0HYM2 rows via the
    store's real public write path (``get_macro`` + an injected mock
    DataEngine) — mirrors tests/test_historical_store.py's own seeding
    convention rather than hand-rolling SQL against the schema."""
    writer = HistoricalStore(db_path=db_path)
    dates = pd.bdate_range(end=pd.Timestamp.now(tz=None).normalize(), periods=5)
    macro_df = pd.DataFrame(
        {
            "VIXCLS": [15.0, 16.0, 17.0, 18.0, 35.0],
            "SAHMREALTIME": [0.0, 0.0, 0.0, 0.0, 0.6],
            "T10Y2Y": [0.5, 0.5, 0.4, -0.3, -0.5],
            "BAMLH0A0HYM2": [3.0, 3.1, 3.2, 6.5, 7.0],
        },
        index=dates,
    )
    de = MagicMock()
    de.fetch_macro_history.return_value = macro_df
    # One call seeds all four columns present in the DataFrame — get_macro's
    # own documented behaviour ("fetches ALL FRED series in one request").
    writer.get_macro("VIXCLS", data_engine=de)


class TestLoadCachedMacroDto:
    def test_returns_none_when_db_empty(self, tmp_path, monkeypatch, caplog):
        db_path = str(tmp_path / "empty.db")
        _point_database_url_at(monkeypatch, db_path)

        with caplog.at_level(logging.WARNING, logger="execution.macro_snapshot"):
            result = load_cached_macro_dto()

        assert result is None
        assert any("no cached macro data available" in r.message for r in caplog.records)

    def test_builds_real_dto_from_cached_rows(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "seeded.db")
        _seed_macro_rows(db_path)
        _point_database_url_at(monkeypatch, db_path)

        dto = load_cached_macro_dto()

        assert dto is not None
        # Latest cached row of each series (see _seed_macro_rows).
        assert dto.vix == pytest.approx(35.0)
        assert dto.sahm_rule_indicator == pytest.approx(0.6)
        assert dto.yield_curve == pytest.approx(-0.5)
        assert dto.credit_spread == pytest.approx(7.0)
        # The seeded values are genuinely stress-level — proves this is a
        # real DTO built from real cached data, not a fabricated neutral
        # "everything is fine" default.
        assert dto.killSwitch is True

    def test_partial_coverage_still_returns_none(self, tmp_path, monkeypatch, caplog):
        """Only SOME of the four required series cached (e.g. a partially
        completed backfill) must still fail open with a warning, not build a
        DTO from an incomplete picture."""
        db_path = str(tmp_path / "partial.db")
        writer = HistoricalStore(db_path=db_path)
        dates = pd.bdate_range(end=pd.Timestamp.now(tz=None).normalize(), periods=3)
        # Only VIXCLS present — SAHMREALTIME/T10Y2Y/BAMLH0A0HYM2 missing.
        macro_df = pd.DataFrame({"VIXCLS": [15.0, 16.0, 17.0]}, index=dates)
        de = MagicMock()
        de.fetch_macro_history.return_value = macro_df
        writer.get_macro("VIXCLS", data_engine=de)
        _point_database_url_at(monkeypatch, db_path)

        with caplog.at_level(logging.WARNING, logger="execution.macro_snapshot"):
            result = load_cached_macro_dto()

        assert result is None
        assert any("no cached macro data available" in r.message for r in caplog.records)
