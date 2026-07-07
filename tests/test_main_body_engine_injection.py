"""
tests/test_main_body_engine_injection.py
=========================================
PR3 groundwork: main_orchestrator._main_body() now accepts optional
`engines: EngineContext` and `data_engine: IDataProvider` keyword-only
parameters, mirroring the same warm-injection pattern PR2 added to
run_pipeline(). This lets a persistent caller (the orchestrator daemon)
supply a pre-built DataEngine + EngineContext so a cycle reuses them instead
of re-checking credentials.json / re-constructing every engine from scratch.

Verifies:
  - the default (engines=None, data_engine=None) path is unaffected --
    credentials.json is still checked and DataEngine/MockDataEngine still
    constructed exactly as before.
  - supplying data_engine bypasses the credentials.json check entirely and
    uses settings.DEFAULT_TICKERS directly.
  - engines is threaded straight through to run_pipeline(engines=...).
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pandas as pd
import pytest

import main_orchestrator as mo


class _NoLoginClient:
    def login(self):
        return False


class _FakeDataEngine:
    """A trivial stand-in for a pre-built, warm data provider."""

    def __init__(self):
        self.fetch_calls = 0


def _ok_fetch_factory():
    async def _ok_fetch(de, tickers):
        _ = de, tickers
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
        return {}, {}, {"AAPL": df}
    return _ok_fetch


def _inactive_kill_switch():
    return type("K", (), {"is_active": lambda self: False})()


class TestDataEngineInjection:
    def test_injected_data_engine_bypasses_credentials_check(self, monkeypatch) -> None:
        fake_de = _FakeDataEngine()
        monkeypatch.setattr(mo, "RobinhoodClient", lambda *a, **k: _NoLoginClient())
        monkeypatch.setattr(mo, "fetch_all_data_async", _ok_fetch_factory())
        monkeypatch.setattr(mo, "GlobalKillSwitch", lambda *a, **k: _inactive_kill_switch())
        monkeypatch.setattr(mo.settings, "DEFAULT_TICKERS", ["AAPL"], raising=False)

        captured = {}

        def _fake_run_pipeline(tickers, macro_raw, fund_raw, tech_raw, **kwargs):
            captured["data_engine"] = kwargs.get("data_engine")
            captured["engines"] = kwargs.get("engines")
            captured["tickers"] = tickers
            return pd.DataFrame(), mock.MagicMock(), mock.MagicMock(
                xsec_percentile_ranks={}, multifactor_scores={},
            )

        monkeypatch.setattr(mo, "run_pipeline", _fake_run_pipeline)
        # os.path.exists must NOT even be consulted for credentials.json when
        # data_engine is injected -- assert by making it raise if called with
        # that specific path.
        real_exists = mo.os.path.exists

        def _guard_exists(path):
            if path == "credentials.json":
                raise AssertionError(
                    "credentials.json check must be skipped when data_engine is injected"
                )
            return real_exists(path)

        monkeypatch.setattr(mo.os.path, "exists", _guard_exists)

        asyncio.run(mo._main_body(False, strict=False, data_engine=fake_de))

        assert captured["data_engine"] is fake_de
        assert captured["tickers"] == ["AAPL"]

    def test_no_injection_preserves_credentials_check(self, monkeypatch) -> None:
        # Force the credentials-absent branch; MockDataEngine + ["AAPL"] path.
        monkeypatch.setattr(mo.os.path, "exists", lambda p: False)
        monkeypatch.setattr(mo, "RobinhoodClient", lambda *a, **k: _NoLoginClient())
        monkeypatch.setattr(mo, "fetch_all_data_async", _ok_fetch_factory())
        monkeypatch.setattr(mo, "GlobalKillSwitch", lambda *a, **k: _inactive_kill_switch())

        captured = {}

        def _fake_run_pipeline(tickers, macro_raw, fund_raw, tech_raw, **kwargs):
            captured["data_engine"] = kwargs.get("data_engine")
            captured["tickers"] = tickers
            return pd.DataFrame(), mock.MagicMock(), mock.MagicMock(
                xsec_percentile_ranks={}, multifactor_scores={},
            )

        monkeypatch.setattr(mo, "run_pipeline", _fake_run_pipeline)

        asyncio.run(mo._main_body(False, strict=False))

        assert isinstance(captured["data_engine"], mo.MockDataEngine)
        assert captured["tickers"] == ["AAPL"]


class TestEngineContextThreading:
    def test_engines_arg_passed_through_to_run_pipeline(self, monkeypatch) -> None:
        ctx = mo.EngineContext()  # empty context is enough to prove identity threading
        monkeypatch.setattr(mo.os.path, "exists", lambda p: False)
        monkeypatch.setattr(mo, "RobinhoodClient", lambda *a, **k: _NoLoginClient())
        monkeypatch.setattr(mo, "fetch_all_data_async", _ok_fetch_factory())
        monkeypatch.setattr(mo, "GlobalKillSwitch", lambda *a, **k: _inactive_kill_switch())

        captured = {}

        def _fake_run_pipeline(tickers, macro_raw, fund_raw, tech_raw, **kwargs):
            captured["engines"] = kwargs.get("engines")
            return pd.DataFrame(), mock.MagicMock(), mock.MagicMock(
                xsec_percentile_ranks={}, multifactor_scores={},
            )

        monkeypatch.setattr(mo, "run_pipeline", _fake_run_pipeline)

        asyncio.run(mo._main_body(False, strict=False, engines=ctx))

        assert captured["engines"] is ctx
