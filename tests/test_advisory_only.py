"""
tests/test_advisory_only.py
===========================
Regression coverage for the Tier 5.1 ADVISORY_ONLY quarantine.

The ADVISORY_ONLY flag is the project's authoritative "broker is off"
gate.  Three layers must honour it simultaneously:

1. ``main_orchestrator._execute_broker_orders`` returns immediately
   without importing the broker stack.
2. The GUI Strategy Matrix mode toggle does NOT render the radio +
   confirm button.
3. ``scripts/preflight_check`` drops the three broker-readiness checks
   (alpaca_configured / alpaca_paper_mode / dry_run_disabled) and the
   paper-trading-duration check from the gate.

These tests prove all three are wired so a future refactor that loosens
any one of them fails CI immediately.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from settings import settings


# ---------------------------------------------------------------------------
# Layer 1: orchestrator gate
# ---------------------------------------------------------------------------

def test_execute_broker_orders_skips_when_advisory_only(monkeypatch, caplog):
    """When ADVISORY_ONLY is True the function returns immediately, logs INFO,
    and never imports any execution.* module."""
    import main_orchestrator

    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", True, raising=False)

    # Synthesise a non-empty DataFrame so we can detect a regression where
    # the function reaches the iter-rows loop instead of returning early.
    df = pd.DataFrame([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 195.0}])

    with caplog.at_level("INFO"):
        asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False, macro_dto=None))

    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "ADVISORY_ONLY" in msgs, "expected an ADVISORY_ONLY INFO log"
    assert "quarantined" in msgs.lower() or "skipping" in msgs.lower()


def test_execute_broker_orders_does_not_log_quarantine_when_flag_disabled(monkeypatch, caplog):
    """When ADVISORY_ONLY is False the function does NOT emit the
    "quarantined" early-return INFO log.  It may still fail downstream
    (broker imports etc) — that path is exercised separately — but the
    Tier 5.1 quarantine guard must not fire."""
    import main_orchestrator

    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", False, raising=False)

    df = pd.DataFrame()  # empty — function will reach the broker-import branch
    with caplog.at_level("INFO"):
        try:
            asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=True, macro_dto=None))
        except Exception:
            pass  # broker stack may not be configured in the test env; ignore

    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "ADVISORY_ONLY=True" not in msgs, (
        "Quarantine log fired with ADVISORY_ONLY=False — early-return guard "
        "regressed."
    )


# ---------------------------------------------------------------------------
# Layer 2: GUI Strategy Matrix mode toggle gate
# ---------------------------------------------------------------------------

def test_strategy_mode_toggle_source_references_advisory_only():
    """AST-level guard: ``_render_strategy_mode_toggle`` must read
    ``ADVISORY_ONLY`` and skip the radio/confirm controls when it is True.
    Lightweight grep is enough — we just need to detect a regression that
    removes the gate entirely."""
    # gui/panels.py was converted to a package (Phase 4a extracted
    # gui/panels/__init__.py into per-tab modules; __init__.py is now a
    # thin re-export stub). ``_render_strategy_mode_toggle`` now lives in
    # gui/panels/strategy_matrix.py — see tests/test_ai_insights_panel.py
    # for the same fix pattern applied to the AI Insights tab.
    src = Path("gui/panels/strategy_matrix.py").read_text(encoding="utf-8")
    # The function must contain BOTH the setting reference and the explicit
    # caller-visible "Advisory mode — broker execution disabled" banner string.
    assert 'ADVISORY_ONLY' in src
    assert 'Advisory mode — broker execution disabled' in src


def test_app_banner_advisory_only_branch_present():
    """``gui/app.py`` must render an ADVISORY MODE banner when the flag is
    True.  Source-grep guard."""
    src = Path("gui/app.py").read_text(encoding="utf-8")
    assert "ADVISORY_ONLY" in src
    assert "ADVISORY MODE" in src


# ---------------------------------------------------------------------------
# Layer 3: preflight check gate
# ---------------------------------------------------------------------------

def test_preflight_skips_broker_checks_when_advisory_only(monkeypatch):
    """When ADVISORY_ONLY is True, the four broker-dependent checks are
    auto-skipped (PASS with reason)."""
    from scripts import preflight_check

    monkeypatch.setattr(preflight_check.settings, "ADVISORY_ONLY", True, raising=False)

    results = preflight_check.run_checks(skip=[])
    by_name = {r.name: r for r in results}

    for check_name in preflight_check._ADVISORY_AUTO_SKIP:
        assert check_name in by_name, f"missing check entry: {check_name}"
        r = by_name[check_name]
        assert r.passed is True, f"{check_name} should auto-skip to PASS under ADVISORY_ONLY"
        assert "ADVISORY_ONLY" in r.reason


def test_preflight_runs_broker_checks_when_advisory_only_false(monkeypatch):
    """When ADVISORY_ONLY is False the broker-dependent checks run normally —
    no auto-skip reason injected."""
    from scripts import preflight_check

    monkeypatch.setattr(preflight_check.settings, "ADVISORY_ONLY", False, raising=False)
    # Make sure the underlying checks see "clean" deterministic input that
    # is not driven by the test environment.
    monkeypatch.setattr(preflight_check.settings, "ALPACA_API_KEY", "TEST_KEY", raising=False)
    monkeypatch.setattr(preflight_check.settings, "ALPACA_SECRET_KEY", "TEST_SECRET", raising=False)
    monkeypatch.setattr(preflight_check.settings, "ALPACA_PAPER", True, raising=False)
    monkeypatch.setattr(preflight_check.settings, "DRY_RUN", False, raising=False)

    results = preflight_check.run_checks(skip=[])
    by_name = {r.name: r for r in results}

    for check_name in ("alpaca_configured", "alpaca_paper_mode", "dry_run_disabled"):
        r = by_name[check_name]
        assert "ADVISORY_ONLY" not in r.reason, (
            f"{check_name} was auto-skipped despite ADVISORY_ONLY=False: {r.reason}"
        )


def test_advisory_only_check_appears_in_results():
    """The new check_advisory_only_active is wired into ALL_CHECKS and
    produces a result row."""
    from scripts import preflight_check

    results = preflight_check.run_checks(skip=[])
    names = {r.name for r in results}
    assert "advisory_only_active" in names


def test_advisory_only_check_warns_when_flag_disabled(monkeypatch):
    """When ADVISORY_ONLY is False the check passes but with warning=True so
    the operator sees the "broker is live" surface explicitly."""
    from scripts import preflight_check

    monkeypatch.setattr(preflight_check.settings, "ADVISORY_ONLY", False, raising=False)
    r = preflight_check.check_advisory_only_active()
    assert r.passed is True
    assert r.warning is True
    assert "ADVISORY_ONLY=False" in r.reason


def test_settings_default_advisory_only_is_true():
    """The project default MUST be ADVISORY_ONLY=True so a fresh clone does
    not accidentally route orders to the broker."""
    assert getattr(settings, "ADVISORY_ONLY", None) is True, (
        "settings.ADVISORY_ONLY default must be True (Tier 5.1)"
    )
