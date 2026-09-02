"""cli_introspect/targets.py — the curated list of entry points to introspect.

Deliberately curated, NOT "every module that imports argparse": the many
``if __name__ == "__main__"`` self-test/demo blocks in the codebase define no
arguments and would only add noise. This is the operator-facing command surface.

``kind`` is how the child harness executes the target:
  * ``"path"``   → ``runpy.run_path`` on a repo-relative .py file (scripts &
    root orchestrators, invoked in real life as ``python <file>``).
  * ``"module"`` → ``runpy.run_module`` on a dotted name (invoked in real life
    as ``python -m <module>``), needed so intra-package relative imports resolve.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    kind: str  # "path" | "module"
    target: str  # repo-relative file path, or dotted module name
    name: str  # manifest command name
    invocation: str  # display prefix shown to the operator


TARGETS: list[Target] = [
    Target("path", "main.py", "main.py", "python3 main.py"),
    Target("path", "main_orchestrator.py", "main_orchestrator.py", "python3 main_orchestrator.py"),
    Target("path", "universe_engine.py", "universe_engine.py", "python3 universe_engine.py"),
    # app_shell.py (the frozen, decommissioned Streamlit native-shell wrapper —
    # see CLAUDE.md's "Frontend strategy" section) is deliberately NOT offered
    # here: a decommissioned entry point shouldn't be runnable from the
    # webapp Commands screen.
    Target("module", "execution.kill_switch", "execution.kill_switch", "python -m execution.kill_switch"),
    Target("module", "validation.harness", "validation.harness", "python -m validation.harness"),
    Target("module", "prompt_registry", "prompt_registry", "python -m prompt_registry"),
    Target("path", "scripts/preflight_check.py", "preflight_check.py", "python scripts/preflight_check.py"),
    Target("module", "scripts.refresh_validations", "refresh_validations.py", "python -m scripts.refresh_validations"),
    Target("path", "scripts/daily_briefing.py", "daily_briefing.py", "python scripts/daily_briefing.py"),
    Target("path", "scripts/track_record_status.py", "track_record_status.py", "python scripts/track_record_status.py"),
    Target("path", "database_setup.py", "database_setup.py", "python3 database_setup.py"),
    Target(
        "path",
        "scripts/repair_price_bars_adjustment.py",
        "repair_price_bars_adjustment.py",
        "python scripts/repair_price_bars_adjustment.py",
    ),
    Target(
        "path",
        "scripts/backfill_edgar_fundamentals.py",
        "backfill_edgar_fundamentals.py",
        "python scripts/backfill_edgar_fundamentals.py",
    ),
    Target(
        "path",
        "scripts/backfill_news_history.py",
        "backfill_news_history.py",
        "python scripts/backfill_news_history.py",
    ),
    Target(
        "path",
        "scripts/backfill_news_history_from_audit.py",
        "backfill_news_history_from_audit.py",
        "python scripts/backfill_news_history_from_audit.py",
    ),
    Target(
        "path",
        "scripts/backfill_sentiment_history.py",
        "backfill_sentiment_history.py",
        "python scripts/backfill_sentiment_history.py",
    ),
    Target(
        "path",
        "scripts/export_notebooklm.py",
        "export_notebooklm.py",
        "python scripts/export_notebooklm.py",
    ),
]
