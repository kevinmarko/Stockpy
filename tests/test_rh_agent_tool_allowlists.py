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

# The Robinhood MCP's tool-name prefixes, one per way it can be connected:
#   robinhood-trading   `claude mcp add robinhood-trading --transport http ...`
#   Robinhood           a server registered under that name
#   claude_ai_Robinhood the claude.ai connector, as the headless `claude -p`
#                       CLI names it. The launchd timers run that CLI, so
#                       without this spelling every agentic cycle and review
#                       was blocked from Robinhood (2026-11-13).
RH_SERVERS = ("robinhood-trading", "Robinhood", "claude_ai_Robinhood")
RH_AGENTS = ("rh-research-scout.md", "rh-reviewer.md", "rh-risk-manager.md", "rh-trader.md")

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
    for server in RH_SERVERS:
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


def _mcp_tools(path: Path) -> set:
    tools = _frontmatter_tools(path)
    return {t.strip() for t in tools.split(":", 1)[1].split(",") if t.strip().startswith("mcp__")}


@pytest.mark.parametrize("agent,servers,allowed", [
    ("rh-research-scout.md", RH_SERVERS,
     ("get_accounts", "get_scans", "run_scan", "get_scanner_filter_specs", "get_equity_quotes",
      "get_equity_fundamentals", "get_earnings_calendar", "get_earnings_results",
      "get_equity_analyst_ratings", "get_sec_filing_index")),
    ("rh-strategy-analyst.md", ("investyo", "investyo-platform"),
     ("get_signal_breakdown", "get_regime_status", "get_recommendation")),
])
def test_phase3_agents_have_exactly_their_read_tools(agent, servers, allowed):
    """Scout and Analyst are read-only researchers: their MCP tools are exactly
    the listed read set (under each server spelling) — no order, scan-edit,
    watchlist or paper-trade tools."""
    path = REPO / ".claude" / "agents" / agent
    assert not ORDER_POWER_TOOL.findall(_frontmatter_tools(path))
    expected = {f"mcp__{s}__{t}" for s in servers for t in allowed}
    assert _mcp_tools(path) == expected


def test_phase3_agents_defer_to_the_validator():
    for agent in ("rh-research-scout.md", "rh-strategy-analyst.md"):
        body = (REPO / ".claude" / "agents" / agent).read_text(encoding="utf-8")
        assert "python3 -m execution.agentic_research validate" in body


@pytest.mark.parametrize("agent", RH_AGENTS)
def test_robinhood_tools_identical_under_every_server_spelling(agent):
    """Each agent lists the same Robinhood tools under every prefix, so how the
    MCP is connected never changes what an agent may do (no spelling adds
    a tool, and none is missing one)."""
    tools = _mcp_tools(REPO / ".claude" / "agents" / agent)
    by_server = {s: {t.split("__", 2)[2] for t in tools if t.startswith(f"mcp__{s}__")}
                 for s in RH_SERVERS}
    base = by_server["robinhood-trading"]
    assert base, f"{agent} lists no Robinhood tools"
    for server, names in by_server.items():
        assert names == base, f"{agent}: {server} differs from robinhood-trading"


REVIEWER = REPO / ".claude" / "agents" / "rh-reviewer.md"
_REVIEWER_READ_TOOLS = ("get_accounts", "get_equity_orders", "get_equity_positions",
                        "get_equity_quotes", "get_trade_approvals", "get_trade_approval_setting")


def test_rh_reviewer_is_read_only_and_has_positions_read():
    """The Reviewer fetches the Agentic holdings (get_equity_positions) for the
    daemon's exit path; its MCP tools stay exactly this read set under every
    server spelling, with no order, decline or cancel power."""
    tools = _frontmatter_tools(REVIEWER)
    assert not ORDER_POWER_TOOL.findall(tools)
    expected = {f"mcp__{s}__{t}" for s in RH_SERVERS for t in _REVIEWER_READ_TOOLS}
    assert _mcp_tools(REVIEWER) == expected


@pytest.mark.parametrize("agent,model", [
    ("rh-research-scout.md", "sonnet"), ("rh-risk-manager.md", "sonnet"),
    ("rh-reviewer.md", "sonnet"), ("rh-strategy-analyst.md", "opus"),
    ("rh-trader.md", "opus"),
])
def test_agent_models_opus_only_for_judgment_and_money(agent, model):
    text = (REPO / ".claude" / "agents" / agent).read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
    assert f"\nmodel: {model}\n" in f"\n{front}\n"
