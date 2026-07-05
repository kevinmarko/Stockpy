"""
validation/thresholds.py
========================
Single source of truth for all deployability gate thresholds used by:
  - validation/harness.py  (``ValidationReport.deployable``)
  - gui/strategy_health.py (``DeployabilityGate`` evaluations)

**Never hard-code these numbers elsewhere.** Any caller that needs to check or
display a threshold must import from this module so the GUI and the validation
harness can never drift apart.

Threshold semantics
-------------------
``PBO_MAX``            — Probability of Backtest Overfitting must be BELOW this.
``DSR_MIN``            — Deflated Sharpe Ratio must be ABOVE this.
``NET_SHARPE_MIN``     — Net-of-cost Sharpe must be ABOVE this.
``MAX_DRAWDOWN_MAX``   — Max Drawdown (fraction, e.g. 0.30 = 30 %) must be BELOW this.
``STRESS_MAX_DRAWDOWN`` — Options-selling tail-scenario max-drawdown limit (50 %).
                          Applied by ``passes_stress_gate()`` in
                          ``validation/stress_scenarios.py``.
``FAMILY_WISE_ALPHA``  — Target false discovery rate for the Benjamini-Hochberg
                          correction applied ACROSS the full family of signal
                          modules (not just one strategy's own trials). Applied
                          by ``validation/multiple_testing.py::benjamini_hochberg``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard deployability gates (all strategies)
# ---------------------------------------------------------------------------
PBO_MAX: float = 0.50
"""Probability of Backtest Overfitting must be strictly less than this."""

DSR_MIN: float = 0.95
"""Deflated Sharpe Ratio must be strictly greater than this."""

NET_SHARPE_MIN: float = 0.50
"""Net-of-cost Sharpe Ratio must be strictly greater than this."""

MAX_DRAWDOWN_MAX: float = 0.30
"""Maximum Drawdown (fractional) must be strictly less than this."""

# ---------------------------------------------------------------------------
# Options-selling tail-scenario gate (options-selling strategies only)
# ---------------------------------------------------------------------------
STRESS_MAX_DRAWDOWN: float = 0.50
"""Max Drawdown limit for each dated shock window (Lehman/Volmageddon/COVID/Yen).
Strategies exceeding this in *any* window are not deployable."""

# ---------------------------------------------------------------------------
# Multiple-testing correction across the signal-module family
# (validation/multiple_testing.py)
# ---------------------------------------------------------------------------
FAMILY_WISE_ALPHA: float = 0.05
"""Target false discovery rate for the Benjamini-Hochberg correction applied
across the full family of signal modules (see signals/registry.py) and their
trials — distinct from any single strategy's own within-strategy DSR/PBO
gates above, which do not account for testing ~17 modules independently."""
