"""CLAUDE.md and AGENTS.md are documented (see CLAUDE.md's own "Branch Workflow"
section) as exact mirrors, auto-synced on save by
``.claude/hooks/sync_agent_docs.sh`` / ``.agents/hooks/sync_agent_docs.sh``.

A hook is not a guard: it only fires for an agent session that has hooks
enabled, and CLAUDE.md itself records that these two files "had already
drifted by one real bullet" before the hook existed. This test is the actual
guard -- CI runs it regardless of which agent (or human) last edited either
file, or whether hooks were even active for that edit.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_claude_md_and_agents_md_are_byte_identical():
    claude_md = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    agents_md = (_REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert claude_md == agents_md, (
        "CLAUDE.md and AGENTS.md have drifted out of sync. These two files are "
        "documented as exact mirrors -- copy whichever one was just edited onto "
        "the other (or run the sync_agent_docs.sh hook manually) and commit both."
    )
