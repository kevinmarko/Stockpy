import asyncio
import json
import os
from pathlib import Path

from google.antigravity import Agent, LocalAgentConfig, types

async def run_auditor(agent_name: str, system_instruction: str, pr1_diff: str, pr2_diff: str) -> str:
    config = LocalAgentConfig(
        system_instruction=system_instruction,
        capabilities=types.CapabilitiesConfig(
            # ensure they can view files if they need to check context
        )
    )
    async with Agent(config) as agent:
        prompt = f"""
You are {agent_name}. 
Please audit the following PR diffs for the Stockpy repo based on your system instructions.
Return your findings clearly. If everything looks perfect, state so.

--- PR 1 DIFF ---
{pr1_diff}

--- PR 2 DIFF ---
{pr2_diff}
"""
        print(f"[{agent_name}] Starting audit...")
        response = await agent.chat(prompt)
        result = await response.text()
        print(f"[{agent_name}] Completed audit.")
        return result

async def run_fixer(agent_name: str, system_instruction: str, findings: str) -> str:
    # We enable run_command policy to allow them to test or edit if needed, 
    # but built-in edit_file is already allowed by default.
    def allow_all_commands(cmd: str) -> bool:
        return True

    config = LocalAgentConfig(
        system_instruction=system_instruction,
        capabilities=types.CapabilitiesConfig(
            confirm_run_command=allow_all_commands
        )
    )
    async with Agent(config) as agent:
        prompt = f"""
You are {agent_name}.
We have audited PR 1 and PR 2. Here are the combined audit findings:

{findings}

Please fix any issues relevant to your domain by using your edit_file or other available tools on the actual repository files.
When you are done, summarize the files you modified and the fixes you applied.
"""
        print(f"[{agent_name}] Starting fix...")
        response = await agent.chat(prompt)
        result = await response.text()
        print(f"[{agent_name}] Completed fix.")
        return result

async def main():
    with open('/tmp/pr1_clean.diff', 'r') as f:
        pr1_diff = f.read()
    with open('/tmp/pr2_clean.diff', 'r') as f:
        pr2_diff = f.read()

    # 1. Spawning 4 Auditing Agents Concurrently
    auditors = [
        ("Auditor-Safety", "You are the Safety & Validation Auditor. Focus on numeric bounds, leg prices, and validation gates in apply_multi_leg_fill and apply_fill. Do not let zero or negative prices slip through. Reject on missing prices."),
        ("Auditor-Attribution", "You are the Attribution & State Auditor. Focus on strategy_id propagation, correct updates to orders, and database migration idempotency in PaperAccountStore."),
        ("Auditor-ErrorHandling", "You are the Error Handling Auditor. Focus on fail-closed atomicity, transaction rollbacks, and rejected order states."),
        ("Auditor-Tests", "You are the Test Quality Auditor. Focus on test coverage, fixture reuse, and numeric drift tolerances as enforced by AGENTS.md. Ensure PR tests exist and are robust.")
    ]

    print("--- STARTING 4 AUDIT AGENTS ---")
    audit_tasks = []
    for name, instruction in auditors:
        audit_tasks.append(run_auditor(name, instruction, pr1_diff, pr2_diff))

    audit_results = await asyncio.gather(*audit_tasks)

    # Consolidate findings
    findings_dict = {}
    for (name, _), result in zip(auditors, audit_results):
        findings_dict[name] = result

    os.makedirs('output', exist_ok=True)
    with open('output/audit_findings.json', 'w') as f:
        json.dump(findings_dict, f, indent=2)

    print("\n--- AUDIT PHASE COMPLETE. Findings saved to output/audit_findings.json ---\n")

    combined_findings = json.dumps(findings_dict, indent=2)

    # 2. Spawning 4 Fixing Agents Concurrently
    fixers = [
        ("Fixer-Data", "You are the Data Layer Fixer. Fix any database migration, SQL schema, or PaperAccountStore logic bugs reported in the audit findings."),
        ("Fixer-Execution", "You are the Execution Fixer. Fix any logic bugs in fmp_paper_broker, options_paper_executor, or paper_broker reported in the audit findings."),
        ("Fixer-Pilots", "You are the Pilots Fixer. Fix any issues in pilots/dispersion_trading or other pilots reported in the audit findings."),
        ("Fixer-Tests", "You are the Test Suite Fixer. Fix any test gaps, ensure lookahead checks pass, and tests cover the new logic as reported in the audit findings.")
    ]

    print("--- STARTING 4 FIX AGENTS ---")
    fix_tasks = []
    for name, instruction in fixers:
        fix_tasks.append(run_fixer(name, instruction, combined_findings))

    fix_results = await asyncio.gather(*fix_tasks)

    for (name, _), result in zip(fixers, fix_results):
        print(f"\n=== {name} RESULT ===\n{result}")

if __name__ == "__main__":
    asyncio.run(main())
