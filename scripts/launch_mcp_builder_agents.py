import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap
bootstrap()

from google.antigravity import Agent, LocalAgentConfig, types

async def launch_builder_agent(name, directory, prompt):
    print(f"Launching {name} for directory {directory}...")
    config = LocalAgentConfig(
        app_data_dir=f"/Users/kevinlee/.gemini/antigravity/brain/builder_{name.replace(' ', '_')}"
    )
    
    full_prompt = f"""You are {name}. You are assigned to work in the git worktree located at:
{directory}

CRITICAL PATH INSTRUCTIONS:
1. Always use absolute paths starting with `{directory}` when viewing, editing, or creating files.
2. When running commands, specify `Cwd` as `{directory}`.
3. You must load and follow the skill located at `{directory}/.claude/skills/stockpy-quant-integrity/SKILL.md`.
4. Read the detailed implementation plan in `{directory}/AGENTS.md` under the section '# InvestYo MCP — Financial Asset-Class Tools'. Focus on the instructions for {name}.

YOUR TASK:
{prompt}

Once you have completed your implementation, wrote the tests, and created the required PR artifacts (.claude/mcp_*_implementation_plan.md, _task.md, _walkthrough.md), end your turn.
"""
    async with Agent(config) as agent:
        response = await agent.chat(full_prompt)
        print(f"\n======================\n{name} FINISHED:\n{await response.text()}\n======================")

async def main():
    agent_configs = [
        {
            "name": "Agent 1",
            "directory": "/Users/kevinlee/Stockpy-live-agent1",
            "prompt": "Build `analyze_options_chain` and `simulate_0dte_payoff` tools. Branch: `feat-mcp-options-analytics`. PR Artifact slug: `mcp_options_analytics`. NOTE: simulate_0dte_payoff should ship in simulation-only mode."
        },
        {
            "name": "Agent 2",
            "directory": "/Users/kevinlee/Stockpy-live-agent2",
            "prompt": "Build `check_overnight_liquidity` tool. Branch: `feat-mcp-overnight-liquidity`. PR Artifact slug: `mcp_overnight_liquidity`. NOTE: Skip real Level-2 integration. Use an approximated NBBO/volume approach."
        },
        {
            "name": "Agent 3",
            "directory": "/Users/kevinlee/Stockpy-live-agent3",
            "prompt": "Build `calculate_margin_kelly_size` tool. Branch: `feat-mcp-margin-kelly-sizing`. PR Artifact slug: `mcp_margin_kelly_sizing`. NOTE: Only use StrategyEngine._calculate_kelly_sizing() and sizing.position_sizer.size_position(), NEVER re-derive win-rate or Kelly fraction."
        },
        {
            "name": "Agent 4",
            "directory": "/Users/kevinlee/Stockpy-live-agent4",
            "prompt": "Build `evaluate_pairs_arbitrage` tool. Branch: `feat-mcp-pairs-arbitrage`. PR Artifact slug: `mcp_pairs_arbitrage`. NOTE: Ensure the z-score thresholds are read from settings/validation.thresholds."
        },
        {
            "name": "Agent 5",
            "directory": "/Users/kevinlee/Stockpy-live-agent5",
            "prompt": "Build `score_otc_credibility` tool. Branch: `feat-mcp-otc-credibility`. PR Artifact slug: `mcp_otc_credibility`. NOTE: Implement a market-cap/ADV floor using MULTIFACTOR_MICROCAP_THRESHOLD from settings."
        }
    ]

    tasks = []
    for cfg in agent_configs:
        tasks.append(asyncio.create_task(launch_builder_agent(cfg["name"], cfg["directory"], cfg["prompt"])))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
