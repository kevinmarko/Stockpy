"""Overnight Position & Gap-Risk Guardrails (Phase 5).

NOT YET WIRED INTO THE LIVE ORDER PATH. This class computes a real,
correct check (given a weight and an earnings flag), but nothing in
execution/risk_gate.py::PreTradeRiskGate or execution/order_manager.py
calls it -- it currently enforces nothing in a real trading cycle despite
its execution-facing name. Wiring a new check into the pre-trade/pre-close
gate is a deliberate execution-path change (see AGENTS.md's "Do not bypass,
weaken, or simplify any of these gates unless explicitly instructed" --
adding a new gate is lower-risk than weakening one, but it still changes
what orders get accepted/rejected in live trading) and needs an explicit
operator decision on where in the pipeline it should run (pre-trade? a
separate end-of-day sweep?), not a silent wire-up as part of an unrelated
bug-fix pass.
"""

from typing import Dict, Any, List


class OvernightGuardrails:
    """Computes an overnight-hold pass/fail check. NOT currently invoked by
    any pre-trade/pre-close gate -- see module docstring."""

    def __init__(self, max_overnight_weight: float = 0.15):
        self.max_overnight_weight = max_overnight_weight

    def check_overnight_intent(
        self, symbol: str, current_weight: float, has_earnings_tonight: bool = False
    ) -> Dict[str, Any]:
        """Check if position is safe to hold overnight."""
        reasons: List[str] = []
        passed = True

        if current_weight > self.max_overnight_weight:
            passed = False
            reasons.append(f"Weight {current_weight:.2f} exceeds overnight cap {self.max_overnight_weight:.2f}")

        if has_earnings_tonight:
            passed = False
            reasons.append("Earnings release scheduled tonight — gap risk high")

        return {
            "symbol": symbol,
            "passed": passed,
            "reasons": reasons,
            "max_weight": self.max_overnight_weight,
        }


if __name__ == "__main__":
    pass
