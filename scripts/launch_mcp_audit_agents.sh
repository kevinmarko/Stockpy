#!/bin/bash
# Launches 5 independent Claude Code instances in separate Terminal windows to perform the audits

echo "Launching 5 Claude Code Audit Agents in new Terminal windows..."

# Function to launch a new Terminal window with Claude Code
launch_claude_audit() {
    local worktree_dir=$1
    local branch_name=$2
    local extra_instructions=$3
    
    local prompt="Execute the Independent Audit Agent checklist (Section 6 of AGENTS.md) against your branch ($branch_name). 
1. Re-run tests via \`pytest\` or \`.claude/hooks/verify_targeted_tests.sh\`.
2. Grep the diff for duplicate Kelly/win-rate formulas, broker-call paths, or hardcoded numeric literals.
3. Confirm honest status reporting, PR artifact naming, and documentation updates.
$extra_instructions
When finished, explicitly state your audit sign-off (or rejection) covering all checklist items so the PR can be opened."

    # Use osascript to open a new terminal window for each agent
    osascript <<EOF
    tell application "Terminal"
        do script "cd '$worktree_dir' && claude -p '$prompt'"
    end tell
EOF
}

# Agent 1: Options Analytics
launch_claude_audit \
    "/Users/kevinlee/Stockpy-live-agent1" \
    "feat-mcp-options-analytics" \
    "Specific check: Confirm simulate_0dte_payoff cannot reach execute_0dte_trade/exits and honestly reports live_exit_gate_wired and strategy_registry_status."

# Agent 2: Overnight Liquidity
launch_claude_audit \
    "/Users/kevinlee/Stockpy-live-agent2" \
    "feat-mcp-overnight-liquidity" \
    "Specific check: Confirm check_overnight_liquidity data_source explicitly states it is an approximation and no claims of real Level-2 data exist. Confirm execution.risk_gate.py is NOT modified."

# Agent 3: Margin & Kelly Sizing
launch_claude_audit \
    "/Users/kevinlee/Stockpy-live-agent3" \
    "feat-mcp-margin-kelly-sizing" \
    "Specific check: Grep diff for ANY arithmetic resembling (p*b-(1-p))/b outside sizing/kelly.py. Reject outright if found. Confirm margin framing doesn't imply a live buying-power check."

# Agent 4: Pairs Arbitrage
launch_claude_audit \
    "/Users/kevinlee/Stockpy-live-agent4" \
    "feat-mcp-pairs-arbitrage" \
    "Specific check: Confirm z-score thresholds are read from settings/validation.thresholds, not hardcoded. Confirm a failed cointegration test returns NaN."

# Agent 5: OTC Credibility
launch_claude_audit \
    "/Users/kevinlee/Stockpy-live-agent5" \
    "feat-mcp-otc-credibility" \
    "Specific check: Confirm market-cap floor reuses MULTIFACTOR_MICROCAP_THRESHOLD. Confirm the velocity check has a synthetic spike fixture test. Confirm 'insufficient data' vs 'score=0' distinction."

echo "All 5 Claude Code audit instances have been launched in separate Terminal windows!"
