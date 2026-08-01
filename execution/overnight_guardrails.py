"""Overnight Position & Gap-Risk Guardrails (Phase 5).

Enforces overnight holding limits, gap-risk stress checks, and earnings event holds.
"""

from typing import Dict, Any, List


class OvernightGuardrails:
    """Pre-close risk gate for overnight position holding."""

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
