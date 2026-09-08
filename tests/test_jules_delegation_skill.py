"""
tests/test_jules_delegation_skill.py
======================================
Pinned-invariant test for `.claude/skills/jules-delegation/SKILL.md` (and its
`.agents/` mirror), in the spirit of
``tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned``.

The 2026-09 confirm=True hardening pass added prompt-hash-pinning
(``request_dispatch_approval``/``approval_token``) and a dispatch cooldown
(``settings.JULES_DISPATCH_COOLDOWN_SECONDS``) to ``data/jules_client.py``.
Neither is a bypass-proof gate against a deliberate single-agent-turn
action -- see ``.claude/jules_confirm_hard_gate_implementation_plan.md``
Sec 2's own finding and ``docs/JULES_INTEGRATION.md`` Sec 4. This test
asserts the skill's prose still says so plainly, in both copies, so a future
edit cannot silently oversell what the code actually delivers (the same
"pin the honest wording so it can't be hallucinated away" purpose the
Robinhood test already serves for a different gap of the same class).
"""
from __future__ import annotations

from pathlib import Path


class TestSkillMdInvariantsPinned:
    def test_skill_md_states_the_two_step_flow_and_its_honest_limits(self):
        # Check every copy of the skill that actually exists -- not just
        # .claude's with an .agents fallback used only when .claude's is
        # missing. .claude/skills/jules-delegation/SKILL.md always exists in
        # this repo, so a fallback-only check would never actually read the
        # .agents/ copy and could miss an honesty phrase silently dropped
        # from it while .claude's stayed intact.
        candidate_paths = [
            Path(".claude/skills/jules-delegation/SKILL.md"),
            Path(".agents/skills/jules-delegation/SKILL.md"),
        ]
        skill_md_paths = [p for p in candidate_paths if p.exists()]

        assert skill_md_paths, f"Could not find any of {candidate_paths}"

        required_phrases = [
            # The two-step flow itself must be described, not just confirm=True.
            "request_jules_dispatch_approval",
            "approval_token",
            "request-approval",
            "--approval-token",
            # The honest, bar-raising-not-bypass-proof framing -- this is the
            # actual pinned invariant. If a future edit deletes these
            # sentences, this test must fail.
            "does **not** prove a human reviewed that content",
            "does **not** stop\n  a single agent turn from calling `request_jules_dispatch_approval` and\n  `dispatch_jules_task` back-to-back with matching content",
            "raising\n  the bar against accidental drift, never as a substitute for actually\n  asking the operator",
            # confirm=True's own pre-existing honesty statement must survive
            # unchanged alongside the new approval_token one -- neither
            # mechanism should read as a stronger claim than it is.
            "`confirm=True` is a code-level boolean, not a substitute for actually\n  getting the operator's yes.",
            # The hard-stop against treating approval-requesting itself as a
            # lower-stakes action that skips asking the operator.
            "Requesting an\n  approval is not a lower-stakes action",
        ]

        for skill_md_path in skill_md_paths:
            content = skill_md_path.read_text(encoding="utf-8")

            assert "## Hard stops (refuse and explain — do not proceed)" in content, (
                f"Missing 'Hard stops' section header in {skill_md_path}."
            )

            for phrase in required_phrases:
                assert phrase in content, (
                    f"Missing pinned honesty phrase in {skill_md_path}: {phrase!r}"
                )

    def test_skill_md_does_not_claim_the_hardening_is_bypass_proof(self):
        """Negative check: the prose must never claim these mechanisms stop
        a deliberate single-turn bypass -- only accidental drift/loops. If a
        future edit adds an overclaiming sentence, this catches it by
        asserting the specific overclaim phrases are ABSENT."""
        candidate_paths = [
            Path(".claude/skills/jules-delegation/SKILL.md"),
            Path(".agents/skills/jules-delegation/SKILL.md"),
        ]
        skill_md_paths = [p for p in candidate_paths if p.exists()]
        assert skill_md_paths, f"Could not find any of {candidate_paths}"

        overclaim_phrases = [
            "cannot be bypassed",
            "guarantees a human",
            "proves a human",
        ]

        for skill_md_path in skill_md_paths:
            content = skill_md_path.read_text(encoding="utf-8")
            for phrase in overclaim_phrases:
                assert phrase not in content, (
                    f"{skill_md_path} appears to overclaim safety with the "
                    f"phrase {phrase!r} -- this hardening is bar-raising, not "
                    "bypass-proof; see docs/JULES_INTEGRATION.md Sec 4."
                )
