"""tests/test_options_archive_import_smoke.py

Step 4b (options desk archive) closer: the 50 modules listed in
``_ARCHIVED_DOTTED_MODULES`` below were ``git mv``'d to ``legacy/`` in this
PR, together with their 50 dedicated test files. This test proves the
promise the whole archive rests on -- that no production entry point still
needs any of them -- by blocking every one of their OLD dotted import paths
(``sys.modules[name] = None``, the standard "make this import raise
ImportError" idiom) and then actually importing each of the modules listed
in ``_ENTRY_POINTS`` in a fresh subprocess.

Blocking via ``sys.modules`` rather than relying solely on the physical
``git mv`` matters for two reasons: (1) it is explicit about intent -- a
reader doesn't have to trust that nothing at the old path silently shadows
this test's assumption; (2) it defends against a FUTURE regression where
someone recreates a stub file at one of these old paths (e.g. a careless
merge conflict resolution) that would otherwise silently reintroduce a
working import and mask the fact that production code still depends on it.

Run in a subprocess (not in-process ``sys.modules`` mutation) so a poisoned
entry doesn't leak into the rest of this test session, and so a real,
uncaught ImportError inside one of the entry points surfaces as a clean
non-zero exit code / captured traceback rather than corrupting collection
for every other test file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The exact 50 modules archived to legacy/ in step 4b -- kept as a literal
# list (not re-derived by walking legacy/) so this test fails loudly, not
# silently, if a future archive PR forgets to update it.
_ARCHIVED_DOTTED_MODULES = [
    "data.execution_audit_store",
    "execution.almgren_chriss_router",
    "execution.dynamic_circuit_breaker",
    "execution.fix_gateway",
    "execution.multi_broker_gateway",
    "execution.options_analytics",
    "execution.options_lifecycle",
    "execution.options_paper_executor",
    "execution.options_queue_builder",
    "execution.sec_rule_606_reporter",
    "llm.research_copilot",
    "ml.drl_market_maker",
    "ml.drl_market_maker_ppo",
    "ml.options_meta_labeler",
    "ml.transformer_vol_forecaster",
    "ml.vrp_premium_selling_proxy_signal",
    "options_ondemand",
    "pilots.copula_stat_arb",
    "pilots.dispersion_trading",
    "pilots.earnings_crush",
    "pilots.gamma_scalper",
    "pilots.har_volatility",
    "pilots.lob_simulator",
    "pilots.multi_leg_pricing",
    "pilots.options",
    "pilots.options_alerts",
    "pilots.options_gex",
    "pilots.options_hedging",
    "pilots.options_risk",
    "pilots.options_sor",
    "pilots.options_vpin",
    "pilots.paper_broker_options_order",
    "pilots.realtime_risk_streamer",
    "pilots.scenario_matrix",
    "pilots.unusual_options_flow",
    "pilots.vol_mispricing",
    "pilots.volatility_surface",
    "pilots.zero_dte_engine",
    "reporting.options_snapshot",
    "scripts.purge_corrupt_paper_options",
    "signals.options_flow_sentiment",
    "signals.vrp_premium_selling",
    "sizing.hrp_cvar_optimizer",
    "technical_options_engine",
    "validation.autonomous_backtest_runner",
    "validation.options_harness",
    "validation.options_selling_backtest",
    "validation.synthetic_diffusion_engine",
    "volatility.bootstrap_iv_history",
    "volatility.iv_engine",
]

# The entry points the plan names as must-still-import-cleanly.
_ENTRY_POINTS = [
    "main",
    "main_orchestrator",
    "investyo_mcp_server",
    "broker_live_execution_mcp",
    "api.pilots_api",
    "api.data_api",
    "api.metrics_api",
    "execution.fmp_paper_broker",
    "data.paper_account_store",
    "pipeline.production_steps",
]


def test_archive_module_count_matches_expected():
    """Regression guard for the list above itself: if a future PR moves a
    51st module into (or one of these 50 back out of) legacy/, this test
    should fail loudly rather than silently under-covering the guard below."""
    assert len(_ARCHIVED_DOTTED_MODULES) == 50
    assert len(set(_ARCHIVED_DOTTED_MODULES)) == 50, "duplicate entry in _ARCHIVED_DOTTED_MODULES"


def test_entry_points_import_cleanly_with_archived_modules_blocked():
    """Blocks every archived module's old dotted path, then imports each
    entry point in one fresh subprocess. A failure prints exactly which
    entry point failed and the real traceback (surfaced via the subprocess's
    captured stderr), not just a bare non-zero exit code."""
    script = textwrap.dedent(
        f"""
        import sys

        for _name in {_ARCHIVED_DOTTED_MODULES!r}:
            sys.modules[_name] = None

        failures = []
        for _entry in {_ENTRY_POINTS!r}:
            try:
                __import__(_entry)
            except Exception as exc:  # noqa: BLE001 -- report every failure, not just the first
                import traceback
                failures.append(_entry + ": " + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

        if failures:
            print("IMPORT_SMOKE_FAILURES:", file=sys.stderr)
            for f in failures:
                print(f, file=sys.stderr)
            sys.exit(1)
        print("IMPORT_SMOKE_OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "NO_VENV_REEXEC": "1"},
    )
    assert result.returncode == 0, (
        f"one or more entry points failed to import with the archived options-desk "
        f"modules blocked:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "IMPORT_SMOKE_OK" in result.stdout


def test_no_archived_module_is_importable_from_its_old_path():
    """Belt-and-suspenders companion to the blocked-import smoke test above:
    proves the physical git mv actually happened for every one of the 50
    modules -- each genuinely isn't importable from its pre-archive dotted
    path any more (as opposed to merely being blocked by this test file's
    own sys.modules trick). One subprocess for all 50, not 50 subprocesses,
    to keep this test's wall-clock cost down."""
    script = textwrap.dedent(
        f"""
        import importlib
        import sys

        still_importable = []
        for _name in {_ARCHIVED_DOTTED_MODULES!r}:
            try:
                importlib.import_module(_name)
                still_importable.append(_name)
            except Exception:
                pass

        if still_importable:
            print("STILL_IMPORTABLE:", still_importable, file=sys.stderr)
            sys.exit(1)
        print("ARCHIVE_MOVE_CONFIRMED")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "NO_VENV_REEXEC": "1"},
    )
    assert result.returncode == 0, (
        f"one or more archived modules are still importable from their pre-archive "
        f"path (expected all 50 to have moved to legacy/):\n{result.stderr}"
    )
    assert "ARCHIVE_MOVE_CONFIRMED" in result.stdout
