"""main.py must hand the cycle's REAL macro context to the report/snapshot writer.

Regression: main()'s single-run cycle used to build a hand-made "neutral"
MacroEconomicDTO (VIX 18, OAS 3.5, ...) for ``_write_html_report``, which also
writes state_snapshot.json. Every advisory run therefore published a fake
RISK ON macro regime, overwriting the daemon's real macro fields (CONSTRAINT #4).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import main
from data.robinhood_portfolio import AccountSnapshot


def test_single_run_passes_result_macro_dto_to_report(monkeypatch):
    real_macro = object()  # sentinel: the exact object must reach the writer
    now = datetime.now(timezone.utc)
    result = main.RunResult(
        snapshot=AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=now,
        ),
        recommendations=[],
        errors=[],
        started_at=now,
        finished_at=now,
        duration_seconds=0.0,
        macro_dto=real_macro,
    )

    captured = {}

    def _fake_write(res, macro_dto=None):
        captured["macro_dto"] = macro_dto

    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(main, "run_once", lambda force_account=False: result)
    monkeypatch.setattr(main, "_write_html_report", _fake_write)
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "_load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(main, "send_alert", MagicMock(), raising=False)

    with pytest.raises(SystemExit):  # a single run exits via sys.exit
        main.main()

    assert captured["macro_dto"] is real_macro
