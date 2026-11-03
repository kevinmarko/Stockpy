"""Least-privilege guard for the four-agent Robinhood system (Phase 1).

Only the ``rh-trader`` agent definition may list a Robinhood tool that can
submit, cancel or decline anything. Every other subagent definition under
``.claude/agents/`` and ``.agents/agents/`` must stay free of them, so a new
research/analysis agent can't quietly gain order powers.

Honest scope: a tool allowlist protects against an agent doing the wrong thing
by accident. It is not a security boundary (an agent with Bash can reach the
filesystem); the operator's tap in the Robinhood app is the real gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
AGENT_DIRS = [REPO / ".claude" / "agents", REPO / ".agents" / "agents"]
TRADER = REPO / ".claude" / "agents" / "rh-trader.md"

# Any Robinhood tool that moves money, cancels, exercises or declines.
ORDER_POWER_TOOL = re.compile(r"\b(?:mcp__[\w-]+__)?((?:place|cancel|exercise|decline)_\w+)")


@pytest.mark.parametrize("tool,hit", [
    ("place_equity_order", True), ("decline_trade_approval", True),
    ("cancel_option_exercise", True), ("cancel_equity_order", True),
    ("place_crypto_order", True), ("exercise_option", True),
    ("mcp__Robinhood__place_option_order", True),
    ("mcp__robinhood-trading__decline_trade_approval", True),
    ("review_equity_order", False), ("get_trade_approvals", False),
    ("get_trade_approval_setting", False), ("preview_crypto_order", False),
])
def test_order_power_pattern(tool, hit):
    assert bool(ORDER_POWER_TOOL.search(tool)) is hit


def _frontmatter_tools(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path} has no YAML frontmatter"
    for line in m.group(1).splitlines():
        if line.startswith("tools:"):
            return line
    return ""


def _agent_files():
    return sorted(p for d in AGENT_DIRS if d.exists() for p in d.glob("*.md"))


def test_trader_definition_exists():
    assert TRADER.exists()


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_only_rh_trader_has_order_power_tools(path: Path):
    tools = _frontmatter_tools(path)
    if path.resolve() == TRADER.resolve():
        return
    found = ORDER_POWER_TOOL.findall(tools)
    assert not found, f"{path} lists order-power tools {found}; only rh-trader.md may"


def test_rh_trader_tools_are_equity_only_and_minimal():
    tools = _frontmatter_tools(TRADER)
    assert "place_equity_order" in tools
    assert "decline_trade_approval" in tools
    assert "review_equity_order" in tools
    assert "get_trade_approvals" in tools
    for forbidden in ("place_option_order", "place_crypto_order", "cancel_",
                      "exercise_option", "Write", "Edit"):
        assert forbidden not in tools, f"rh-trader must not list {forbidden}"


def test_rh_trader_defers_to_the_skill_and_states_it_cannot_approve():
    body = TRADER.read_text(encoding="utf-8")
    assert ".claude/skills/robinhood-execution/SKILL.md" in body
    assert "You can never approve a trade." in body
    assert "the skill wins" in body


RISK_MANAGER = REPO / ".claude" / "agents" / "rh-risk-manager.md"
_RISK_READ_TOOLS = ("get_accounts", "get_portfolio", "get_equity_positions",
                    "get_equity_quotes", "get_trade_approvals", "get_earnings_calendar")


def test_rh_risk_manager_is_read_only_and_complete():
    """Agent 3 needs exactly the live-state read tools, under both server
    spellings, and nothing that can submit, decline, cancel or edit."""
    tools = _frontmatter_tools(RISK_MANAGER)
    assert not ORDER_POWER_TOOL.findall(tools)
    for server in ("robinhood-trading", "Robinhood"):
        for t in _RISK_READ_TOOLS:
            assert f"mcp__{server}__{t}" in tools, f"rh-risk-manager missing {server}/{t}"
    listed = {t.strip() for t in tools.split(":", 1)[1].split(",")}
    mcp_tools = {t for t in listed if t.startswith("mcp__")}
    assert all(t.rsplit("__", 1)[1] in _RISK_READ_TOOLS for t in mcp_tools), mcp_tools
    for forbidden in ("Write", "Edit", "review_equity_order"):
        assert forbidden not in listed


def test_rh_risk_manager_defers_to_the_preflight_code():
    body = RISK_MANAGER.read_text(encoding="utf-8")
    assert "python3 -m execution.agentic_preflight" in body
    assert "never override it" in body
