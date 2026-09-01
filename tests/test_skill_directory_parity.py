"""
Parity guard for skills intentionally duplicated across `.agents/skills/`
(Antigravity) and `.claude/skills/` (Claude Code).

Why this exists: PR #970 added a new `.agents/skills/stockpy-quant-integrity/
SKILL.md` explicitly described in its own implementation plan as "Mirrored
from `.claude/skills/stockpy-quant-integrity/SKILL.md`", but the copy was
stale -- it reproduced a superseded claim about `STRATEGY_REGISTRY` status
that the `.claude` copy had already corrected. The sibling `stockpy-master-
prompt` skill added in the SAME commit then contradicted that stale copy on
the exact same fact (whether `zero_dte_engine`'s 15:45 ET exit gate is wired
into the daemon -- it is). Nothing caught either divergence before merge; the
PR's own "verification" ran two unrelated test files and checked that the
new SKILL.md files parsed, not that they said the same, correct thing.

See docs/known_issues/skill_directory_manual_copy_drift.md for the full
incident write-up.

This repo has no mechanism (unlike `.claude/hooks/sync_agent_docs.sh` for
CLAUDE.md/AGENTS.md) that keeps `.agents/skills/<name>/SKILL.md` and
`.claude/skills/<name>/SKILL.md` in sync for a skill that exists in both
trees under the same name.

Scope note -- this does NOT enforce parity for every skill present in both
trees. A repo-wide sweep while writing this test found 8 other skills
(`agentic-discovery`, `jules-delegation`, `mcp-widget-builder`,
`new-pwa-screen`, `new-signal-module`, `pilots-endpoint`,
`robinhood-execution`, `strategy-validation`) that already differ between
the two trees by a consistent 8-line HTML-comment preamble in the `.agents/`
copy (e.g. "Ported from this repo's Claude Code sibling skill... to
Antigravity's skill format... no restructuring was required for this port
beyond this note."). That looks like a deliberate porting convention, not
drift, but it was never verified content-for-content beyond the preamble
itself, and auditing all 8 is a separate, larger task outside the scope of
the PR #970 fix this test guards -- see
docs/known_issues/skill_directory_manual_copy_drift.md's "What's not fixed"
section. Only skills confirmed to be intended as true exact mirrors (no
porting-note preamble, explicitly described as "mirrored"/"exact mirror" in
their introducing PR) are enforced here.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

# Skills confirmed to be intended as exact, byte-identical mirrors across
# `.agents/skills/<name>/SKILL.md` and `.claude/skills/<name>/SKILL.md` --
# add a new name here only after confirming (like these two) that neither
# copy carries a deliberate per-platform porting preamble or other
# intentional divergence; otherwise this test will fail on a legitimate
# difference instead of a real bug.
EXACT_MIRROR_SKILLS: tuple[str, ...] = (
    "stockpy-master-prompt",
    "stockpy-quant-integrity",
)


def _skill_names(skills_dir: Path) -> set[str]:
    if not skills_dir.is_dir():
        return set()
    return {
        entry.name
        for entry in skills_dir.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    }


def _mirrored_skill_names() -> list[str]:
    present_in_both = _skill_names(AGENTS_SKILLS_DIR) & _skill_names(CLAUDE_SKILLS_DIR)
    return sorted(present_in_both & set(EXACT_MIRROR_SKILLS))


class TestSkillDirectoryParity:
    """Every skill in `EXACT_MIRROR_SKILLS` must be a byte-identical copy in
    both `.agents/skills/` and `.claude/skills/`."""

    @pytest.mark.parametrize("skill_name", _mirrored_skill_names())
    def test_skill_md_is_byte_identical_across_trees(self, skill_name: str) -> None:
        agents_path = AGENTS_SKILLS_DIR / skill_name / "SKILL.md"
        claude_path = CLAUDE_SKILLS_DIR / skill_name / "SKILL.md"

        agents_content = agents_path.read_text(encoding="utf-8")
        claude_content = claude_path.read_text(encoding="utf-8")

        if agents_content == claude_content:
            return

        agents_lines = agents_content.splitlines()
        claude_lines = claude_content.splitlines()
        diff_lines = list(
            difflib.unified_diff(
                agents_lines,
                claude_lines,
                fromfile=f".agents/skills/{skill_name}/SKILL.md",
                tofile=f".claude/skills/{skill_name}/SKILL.md",
                lineterm="",
            )
        )

        pytest.fail(
            f"'{skill_name}' has drifted between .agents/skills/ and "
            f".claude/skills/ -- these are meant to be exact mirrors.\n"
            + "\n".join(diff_lines)
            + "\n"
            f"Either sync the two copies to match, or -- if the divergence is "
            f"deliberate -- remove '{skill_name}' from EXACT_MIRROR_SKILLS in "
            f"tests/test_skill_directory_parity.py with a comment explaining why."
        )

    def test_mirrored_skill_set_is_non_empty(self) -> None:
        """Sanity check that this test is actually exercising every skill in
        EXACT_MIRROR_SKILLS -- checking only that _mirrored_skill_names() is
        non-empty would let one mirrored skill silently drop out of coverage
        (e.g. its directory renamed/removed from one tree) while the other
        keeps the parametrize list non-empty; the parity test above would
        then simply stop generating a case for the missing one instead of
        failing or even being visibly reported as skipped."""
        missing = set(EXACT_MIRROR_SKILLS) - set(_mirrored_skill_names())
        assert not missing, (
            "Expected every name in EXACT_MIRROR_SKILLS to be present in "
            f"both .agents/skills/ and .claude/skills/; missing from one or "
            f"both trees: {sorted(missing)}. Did the skills directories "
            "move, or did one of the mirrored skills get renamed or "
            "removed from one tree?"
        )
