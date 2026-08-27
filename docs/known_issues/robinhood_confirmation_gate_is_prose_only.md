# Robinhood Confirmation Gate is Prose-Only

The `robinhood-execution` skill is the only actor permitted to call the Robinhood Trading MCP write tools. However, the critical safety invariant requiring one explicit human confirmation per placed order is enforced entirely by prose instructions in the `.claude/skills/robinhood-execution/SKILL.md` (or `.agents/skills/robinhood-execution/SKILL.md`) file, not by code or system constraints.

This is a known gap: the platform relies on the LLM adhering to its instructions to present the order to the operator and await explicit confirmation rather than batching orders or proceeding autonomously.

**Mitigation:**
A test (`TestSkillMdInvariantsPinned` in `tests/test_robinhood_e2e.py`) has been added to pin the verbatim safety language in the `SKILL.md` file, ensuring that the required instructions cannot be accidentally removed or modified without failing the test suite.
