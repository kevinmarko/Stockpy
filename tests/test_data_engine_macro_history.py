"""
tests/test_data_engine_macro_history.py
========================================
Regression coverage for the T10YIE (10-Year Breakeven Inflation Rate) addition
to ``data_engine.DataEngine.fetch_macro_history()`` -- the "stagflation regime"
fix (see CLAUDE.md's "Gaussian HMM Regime Detector tuning & diagnostics" and
the ``settings.HMM_INFLATION_FEATURE_ENABLED`` field). Prior to this fix,
``fetch_macro_history()`` never fetched the ``T10YIE`` FRED series at all,
which left ``settings.HMM_INFLATION_FEATURE_ENABLED`` and
``macro_engine.py``'s ``HistoricalStore().get_macro("T10YIE", ...)`` call
silently no-op'd (no cached rows to ever top up from) even when the operator
turned the feature flag on.

Uses a fake stand-in for ``fredapi.Fred`` (monkeypatched onto
``data_engine.Fred``) rather than mocking the whole ``DataEngine`` instance,
so the assertions exercise the REAL ``fetch_macro_history()`` implementation,
not a test double of it.
"""

from __future__ import annotations

import socket
import threading
import time

import pandas as pd
import pytest

import data_engine
from data_engine import DataEngine, _bounded_fred_timeout
from settings import settings


class _FakeFred:
    """Minimal stand-in for ``fredapi.Fred`` with a controllable, per-series
    ``get_series()``. Mirrors ``tests/test_macro_engine.py``'s ``_FakeFred``
    convention."""

    def __init__(self, series_map: dict, *, raise_on: frozenset = frozenset(), api_key: str = ""):
        self._series_map = series_map
        self._raise_on = raise_on

    def get_series(self, series_id: str) -> pd.Series:
        if series_id in self._raise_on:
            raise RuntimeError(f"FRED unavailable for {series_id}")
        series = self._series_map.get(series_id)
        if series is None:
            raise KeyError(f"_FakeFred has no series configured for {series_id!r}")
        return series


def _make_engine(monkeypatch, series_map: dict, *, raise_on: frozenset = frozenset()) -> DataEngine:
    """Construct a real DataEngine whose ``self.fred`` is a ``_FakeFred``
    (bypasses the real network-touching ``fredapi.Fred(api_key=...)`` call)."""
    monkeypatch.setattr(
        data_engine, "Fred",
        lambda api_key: _FakeFred(series_map, raise_on=raise_on),
    )
    return DataEngine(fred_api_key="fake-test-key")


def _daily_series(start_value: float, n: int = 30) -> pd.Series:
    dates = pd.bdate_range(end="2026-08-01", periods=n)
    return pd.Series([start_value + i * 0.01 for i in range(n)], index=dates)


class TestFetchMacroHistoryIncludesT10YIE:
    def test_output_includes_a_real_t10yie_column(self, monkeypatch):
        series_map = {
            "VIXCLS": _daily_series(15.0),
            "T10Y2Y": _daily_series(0.5),
            "BAMLH0A0HYM2": _daily_series(2.5),
            "BAA10Y": _daily_series(2.0),
            "UNRATE": _daily_series(4.0),
            "T10YIE": _daily_series(2.3),
            "BAMLC0A0CM": _daily_series(3.5),
            "FEDFUNDS": _daily_series(5.0),
        }
        engine = _make_engine(monkeypatch, series_map)

        history_df = engine.fetch_macro_history()

        assert "T10YIE" in history_df.columns
        assert not history_df["T10YIE"].dropna().empty
        # Real (mocked-but-realistic) values, not fabricated placeholders --
        # the values round-trip exactly from the fake FRED source.
        expected = series_map["T10YIE"]
        expected.index = pd.to_datetime(expected.index)
        pd.testing.assert_series_equal(
            history_df["T10YIE"].sort_index(), expected.sort_index(), check_names=False,
        )
        # All eight series present -- the pre-existing six plus BAMLC0A0CM/FEDFUNDS.
        for col in ("VIXCLS", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "UNRATE", "T10YIE", "BAMLC0A0CM", "FEDFUNDS"):
            assert col in history_df.columns

    def test_no_fred_client_failure_path_column_list_includes_t10yie(self):
        # self.fred is None -> the first (no-FRED-initialized) empty-DataFrame
        # literal must also list T10YIE for schema consistency with the
        # success path.
        engine = DataEngine.__new__(DataEngine)
        engine.fred = None
        engine.fred_key = ""

        history_df = engine.fetch_macro_history()

        assert history_df.empty
        assert list(history_df.columns) == [
            "VIXCLS", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "UNRATE", "T10YIE",
            "BAMLC0A0CM", "FEDFUNDS",
        ]

    def test_fetch_exception_failure_path_column_list_includes_t10yie(self, monkeypatch):
        # A mid-fetch exception (e.g. T10YIE itself unavailable) must degrade
        # to the SAME eight-column empty-DataFrame shape (CONSTRAINT #6 --
        # never a fabricated partial frame), not raise.
        series_map = {
            "VIXCLS": _daily_series(15.0),
            "T10Y2Y": _daily_series(0.5),
            "BAMLH0A0HYM2": _daily_series(2.5),
            "BAA10Y": _daily_series(2.0),
            "UNRATE": _daily_series(4.0),
            "BAMLC0A0CM": _daily_series(3.5),
            "FEDFUNDS": _daily_series(5.0),
        }
        engine = _make_engine(monkeypatch, series_map, raise_on=frozenset({"T10YIE"}))

        history_df = engine.fetch_macro_history()

        assert history_df.empty
        assert list(history_df.columns) == [
            "VIXCLS", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "UNRATE", "T10YIE",
            "BAMLC0A0CM", "FEDFUNDS",
        ]


class TestFetchMacroHistoryIncludesBamlc0a0cmAndFedfunds:
    """Regression coverage for the BAMLC0A0CM (investment-grade credit OAS)
    and FEDFUNDS (Federal Funds Effective Rate) addition to
    ``data_engine.DataEngine.fetch_macro_history()`` -- closes the gap where
    ``api/pilots_api.py``'s ``get_transformer_forecast`` endpoint requested
    these two series via ``HistoricalStore().get_macro(...)`` but
    ``fetch_macro_history()`` never fetched them, so they always came back
    as empty Series and the endpoint silently degraded to VIX/yield-curve-
    only macro conditioning."""

    def test_output_includes_real_bamlc0a0cm_and_fedfunds_columns(self, monkeypatch):
        series_map = {
            "VIXCLS": _daily_series(15.0),
            "T10Y2Y": _daily_series(0.5),
            "BAMLH0A0HYM2": _daily_series(2.5),
            "BAA10Y": _daily_series(2.0),
            "UNRATE": _daily_series(4.0),
            "T10YIE": _daily_series(2.3),
            "BAMLC0A0CM": _daily_series(3.5),
            "FEDFUNDS": _daily_series(5.0),
        }
        engine = _make_engine(monkeypatch, series_map)

        history_df = engine.fetch_macro_history()

        assert "BAMLC0A0CM" in history_df.columns
        assert "FEDFUNDS" in history_df.columns
        assert not history_df["BAMLC0A0CM"].dropna().empty
        assert not history_df["FEDFUNDS"].dropna().empty
        # Real (mocked-but-realistic) values, not fabricated placeholders --
        # the values round-trip exactly from the fake FRED source.
        expected_baml = series_map["BAMLC0A0CM"]
        expected_baml.index = pd.to_datetime(expected_baml.index)
        pd.testing.assert_series_equal(
            history_df["BAMLC0A0CM"].sort_index(), expected_baml.sort_index(), check_names=False,
        )
        expected_fedfunds = series_map["FEDFUNDS"]
        expected_fedfunds.index = pd.to_datetime(expected_fedfunds.index)
        pd.testing.assert_series_equal(
            history_df["FEDFUNDS"].sort_index(), expected_fedfunds.sort_index(), check_names=False,
        )

    def test_no_fred_client_failure_path_column_list_includes_baml_and_fedfunds(self):
        # self.fred is None -> the first (no-FRED-initialized) empty-DataFrame
        # literal must also list BAMLC0A0CM/FEDFUNDS for schema consistency
        # with the success path.
        engine = DataEngine.__new__(DataEngine)
        engine.fred = None
        engine.fred_key = ""

        history_df = engine.fetch_macro_history()

        assert history_df.empty
        assert "BAMLC0A0CM" in history_df.columns
        assert "FEDFUNDS" in history_df.columns

    def test_fetch_exception_failure_path_column_list_includes_baml_and_fedfunds(self, monkeypatch):
        # A mid-fetch exception (e.g. FEDFUNDS itself unavailable) must
        # degrade to the SAME eight-column empty-DataFrame shape
        # (CONSTRAINT #6 -- never a fabricated partial frame), not raise.
        series_map = {
            "VIXCLS": _daily_series(15.0),
            "T10Y2Y": _daily_series(0.5),
            "BAMLH0A0HYM2": _daily_series(2.5),
            "BAA10Y": _daily_series(2.0),
            "UNRATE": _daily_series(4.0),
            "T10YIE": _daily_series(2.3),
            "BAMLC0A0CM": _daily_series(3.5),
        }
        engine = _make_engine(monkeypatch, series_map, raise_on=frozenset({"FEDFUNDS"}))

        history_df = engine.fetch_macro_history()

        assert history_df.empty
        assert "BAMLC0A0CM" in history_df.columns
        assert "FEDFUNDS" in history_df.columns


# ---------------------------------------------------------------------------
# 2026-08 fix: fredapi.Fred.get_series() calls a bare urlopen() with no
# timeout parameter and no session-injection hook -- a stalled FRED
# connection used to block DataEngine's macro fetches forever, wedging the
# entire pipeline cycle. See docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md.
# ---------------------------------------------------------------------------

class _BlackHoleServer:
    """A real TCP server that accepts a connection and then never sends a
    byte -- the one way to prove a bound is genuinely enforced on a blocking
    socket read, rather than mocking the timeout away. Used (not a bare
    ``bind+listen`` with no ``accept()`` at all) so a client's ``connect()``
    succeeds immediately and the hang is isolated to the subsequent read,
    matching what a stalled-but-connected FRED server would look like."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self._sock.settimeout(0.05)
        conns = []
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
                conns.append(conn)  # accepted, held open, never written to
            except socket.timeout:
                continue
        for conn in conns:
            conn.close()

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=1.0)
        self._sock.close()


class TestBoundedFredTimeout:
    """Pure unit coverage for the ``_bounded_fred_timeout`` context manager
    itself -- no fredapi/DataEngine involved."""

    def test_sets_and_restores_default_timeout(self):
        previous = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(None)
            with _bounded_fred_timeout(2.5):
                assert socket.getdefaulttimeout() == 2.5
            assert socket.getdefaulttimeout() is None
        finally:
            socket.setdefaulttimeout(previous)

    def test_restores_default_timeout_even_on_exception(self):
        previous = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(None)
            with pytest.raises(RuntimeError):
                with _bounded_fred_timeout(1.0):
                    assert socket.getdefaulttimeout() == 1.0
                    raise RuntimeError("boom")
            assert socket.getdefaulttimeout() is None
        finally:
            socket.setdefaulttimeout(previous)

    def test_genuinely_bounds_a_real_blocking_socket_read(self):
        """Proof, not assumption: a real socket connected to a server that
        never responds is bounded by _bounded_fred_timeout, not left to
        block forever -- this is the exact failure mode urlopen() inside
        fredapi hits against a stalled FRED connection."""
        server = _BlackHoleServer()
        try:
            started = time.monotonic()
            with pytest.raises((socket.timeout, TimeoutError, OSError)):
                with _bounded_fred_timeout(0.1):
                    sock = socket.create_connection(("127.0.0.1", server.port))
                    try:
                        sock.recv(1024)  # blocks forever without the bound
                    finally:
                        sock.close()
            elapsed = time.monotonic() - started
            assert elapsed < 1.0
        finally:
            server.close()


class _RealSocketFakeFred:
    """Like ``_FakeFred``, but the series listed in ``hang_on`` perform a
    REAL blocking socket read against a black-hole server instead of
    returning instantly -- proving the bound is enforced at the actual
    DataEngine call site, not just on the context manager in isolation."""

    def __init__(self, series_map: dict, *, hang_on: frozenset, port: int):
        self._series_map = series_map
        self._hang_on = hang_on
        self._port = port

    def get_series(self, series_id: str, **kwargs) -> pd.Series:
        if series_id in self._hang_on:
            sock = socket.create_connection(("127.0.0.1", self._port))
            try:
                sock.recv(1024)  # never returns without the ambient timeout
            finally:
                sock.close()
        series = self._series_map.get(series_id)
        if series is None:
            raise KeyError(f"_RealSocketFakeFred has no series configured for {series_id!r}")
        return series


class TestFetchMacroCallsBoundedByRequestTimeout:
    """End-to-end (within DataEngine): a hung self.fred.get_series() call
    must bound out within settings.FRED_REQUEST_TIMEOUT_SECONDS and degrade
    via the EXISTING broad except-Exception handling -- no new fallback
    logic required, since socket.timeout/TimeoutError are OSError->Exception
    subclasses those blocks already catch."""

    def test_fetch_macro_raw_detailed_bounds_a_hung_fred_call(self, monkeypatch):
        monkeypatch.setattr(settings, "FRED_REQUEST_TIMEOUT_SECONDS", 0.1)
        server = _BlackHoleServer()
        try:
            monkeypatch.setattr(
                data_engine, "Fred",
                lambda api_key: _RealSocketFakeFred(
                    {}, hang_on=frozenset({"T10Y2Y"}), port=server.port
                ),
            )
            engine = DataEngine(fred_api_key="fake-test-key")

            started = time.monotonic()
            result, fabricated = engine.fetch_macro_raw_detailed()
            elapsed = time.monotonic() - started

            assert elapsed < 1.0
            # The hung call raises before ever reaching a real value -- the
            # whole snapshot degrades to the documented hardcoded fallback
            # (CONSTRAINT #4: an honest, always-fabricated sentinel, never a
            # value that LOOKS real), exactly like any other FRED exception.
            assert fabricated  # non-empty: every key is a placeholder
        finally:
            server.close()

    def test_fetch_macro_history_bounds_a_hung_fred_call(self, monkeypatch):
        monkeypatch.setattr(settings, "FRED_REQUEST_TIMEOUT_SECONDS", 0.1)
        server = _BlackHoleServer()
        try:
            series_map = {
                "VIXCLS": _daily_series(15.0),
                # T10Y2Y hangs -- everything else would succeed if reached.
            }
            monkeypatch.setattr(
                data_engine, "Fred",
                lambda api_key: _RealSocketFakeFred(
                    series_map, hang_on=frozenset({"T10Y2Y"}), port=server.port
                ),
            )
            engine = DataEngine(fred_api_key="fake-test-key")

            started = time.monotonic()
            history_df = engine.fetch_macro_history()
            elapsed = time.monotonic() - started

            assert elapsed < 1.0
            # Existing dead-letter contract: a mid-fetch exception degrades
            # to the empty 8-column frame, never a fabricated partial one.
            assert history_df.empty
            assert "T10Y2Y" in history_df.columns
        finally:
            server.close()
