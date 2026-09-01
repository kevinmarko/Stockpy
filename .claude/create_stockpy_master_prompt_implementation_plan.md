# Stockpy Master Prompt Skill Implementation Plan

Package the Stockpy / InvestYo Master Session Prompt into a dedicated, reusable agent skill (`stockpy-master-prompt`) across `.agents/skills/` (Antigravity) and `.claude/skills/` (Claude Code) so operators can invoke or reference the skill instead of copying and pasting the full master prompt.

## §0 Dependency & Environment Check

- Confirmed active branch: `create_stockpy_master_prompt`
- Confirmed skill discovery conventions for Antigravity (`.agents/skills/`) and Claude Code (`.claude/skills/`)
- Confirmed no conflicting live runtime changes required; pure skill and documentation additions.

## Proposed Changes

### Agent Skills Layer

#### [NEW] `.agents/skills/stockpy-master-prompt/SKILL.md`
- Encodes the full master prompt:
  1. System context & audit-first philosophy
  2. 5 non-negotiable constraints (advisory-only, never fabricate a metric / CONSTRAINT #4, fail closed / CONSTRAINT #6, single source of quant truth, deployability gate)
  3. 4-step startup ritual (memory read, quant integrity skill check, live code inspection, MCP SHA verification)
  4. Output format requirements (scoped PR artifacts, executable code, parity tests)
  5. Completion checklist (test verification, literal greps, single source of truth checks, doc updates)
  6. Agent-specific enforcement notes (Claude Code blocking hook vs Antigravity advisory hook, untrusted self-reports)
  7. Known open gaps (0DTE exit gate wiring, unregistered options pilots, universe divergence, live order MCP status)
  8. First move instructions.

#### [NEW] `.claude/skills/stockpy-master-prompt/SKILL.md`
- Exact mirror of the skill for Claude Code environment.

#### [NEW] `.agents/skills/stockpy-quant-integrity/SKILL.md`
- Mirrored from `.claude/skills/stockpy-quant-integrity/SKILL.md` to ensure Antigravity has local access to the quant integrity reference cited in the startup ritual.

### Documentation Layer

#### [MODIFY] `docs/architecture/simulation-eval-reporting.md`
- Updated `.agents/skills/` and `.claude/skills/` section to document the full skill surface including `stockpy-master-prompt`.

## Verification Plan

- `pytest tests/test_robinhood_e2e.py` (passes with 15/15)
- `pytest tests/test_discovery_skill.py` (passes with 10/10)
- Verified YAML frontmatter syntax and markdown rendering.

## AGENT HANDOFF NOTES

- Documentation updated in `docs/architecture/simulation-eval-reporting.md`.
- Both Antigravity and Claude Code skill directories now have `stockpy-master-prompt` available for automatic discovery.
