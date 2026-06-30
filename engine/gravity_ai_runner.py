"""
engine/gravity_ai_runner.py — AI-driven Gravity audit runner (Scope 2, Tier 9).
================================================================================

The 7 step prompts in :mod:`ai_verification_prompts.py` have always been
designed for an external LLM to consume — they were never wired to a
Python runner.  This module finally provides one, using the providers
already shipped by Tier 9 (:mod:`llm.providers`).

Architecture
------------
* **Claude is the primary auditor.**  Its verdict is the "official" PASS/FAIL.
* **Gemini is the independent cross-checker.**  Its verdict is recorded
  side-by-side and surfaces as ``disagreement: True`` whenever the
  ``status`` fields differ.  The runner DOES NOT pick a winner — the
  operator sees both verdicts and decides.
* Both providers force structured JSON output via
  :class:`llm.schemas.GravityAuditStepResult` (Anthropic ``tool_use`` for
  Claude, ``response_schema`` for Gemini).  A schema-mismatched response
  is a soft failure (``None``) — the runner records "skipped" for that
  model rather than fabricating a verdict (CONSTRAINT #4 + #6).

Safety contract (audited by Gravity step_75)
--------------------------------------------
1.  Opt-in master switch: ``settings.GRAVITY_AI_RUNNER_ENABLED=False`` by
    default.  When False, the runner never instantiates a provider and
    never makes a network call.
2.  No order code: this module contains zero broker-submission
    primitives.  The runner exists to summarise audits, never to act.
3.  Lazy provider construction: providers are built on first use, not at
    import time, so the module costs nothing to import when disabled.
4.  Soft-fail end-to-end: any provider, parse, or schema failure → that
    model's verdict is ``None`` and the step is marked partial; the
    runner never raises.
5.  No fabricated metrics: numeric pipeline scalars (score, conviction,
    forecasts, position sizes, ATR, …) are never assigned from a runner
    output.  The runner only writes audit verdicts to
    ``settings.GRAVITY_AI_RUNNER_OUTPUT_PATH`` (JSON).
6.  No SDK imports at module top — every LLM-adjacent import is lazy
    inside a function body so the audit surface is reachable for tests
    that mock SDKs without paying the import cost up-front.

CLI
---
``python -m engine.gravity_ai_runner [STEP]``

* No argument: runs all 7 steps.
* Integer 1-7: runs that single step.
* ``--json``: emits JSON only (suitable for piping); otherwise renders a
  human summary then the JSON path.

Both providers stay strictly advisory — the runner adds no order code,
no execution, no automation.  It is purely a static-analysis verdict
aggregator.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Repo root for module-style invocation.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step → file map.  Each step audits a different layer of the platform; this
# hand-curated mapping is the canonical "what does the auditor read" table.
# Kept here so a single source-of-truth refactor of the audit scope is one
# diff away from changing the runner's behavior.
#
# Files are concatenated with a `# === <path> ===` separator and included
# verbatim in the user prompt.  Each step is capped at ~32 KB of code to
# avoid hitting context-window limits; the slice is from the top of each
# file so the most-load-bearing imports + public API are always included.
# ---------------------------------------------------------------------------
_STEP_FILE_MAP: Dict[int, Tuple[str, ...]] = {
    1: ("config.py", "database_setup.py", "processing_engine.py"),
    2: ("strategy_engine.py", "processing_engine.py"),
    3: ("technical_options_engine.py",),
    4: ("forecasting_engine.py",),
    5: ("macro_engine.py",),
    6: ("evaluation_engine.py", "research_engine.py", "sizing/kelly.py", "sizing/vol_target.py"),
    7: (
        "execution/risk_gate.py",
        "execution/kill_switch.py",
        "execution/order_manager.py",
        "main_orchestrator.py",
    ),
}

# Cap each individual file's slice at this many bytes so a single huge file
# doesn't push the prompt past the model's window.  Empirically ~32 KB per
# file keeps the total prompt comfortably under 100 KB for the multi-file
# steps.
_PER_FILE_BYTE_CAP = 32_768


@dataclasses.dataclass(frozen=True)
class StepRunResult:
    """Per-step output of the runner — pure data, JSON-serialisable."""

    step_number: int
    step_title: str
    claude_verdict: Optional[Dict[str, Any]]   # GravityAuditStepResult.model_dump() or None
    gemini_verdict: Optional[Dict[str, Any]]
    disagreement: bool                          # True iff both present AND status differs
    notes: List[str]                            # operator-facing notes (e.g. why a side is None)
    timestamp: str                              # UTC ISO

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class RunReport:
    """Aggregate report — what the runner persists + emits."""

    generated_at: str
    enabled: bool
    steps: List[StepRunResult]
    summary: Dict[str, Any]   # roll-up counts: total / pass / fail / disagreements / soft_fails

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "enabled": self.enabled,
            "steps": [s.to_dict() for s in self.steps],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_file_slice(rel_path: str) -> str:
    """Read up to ``_PER_FILE_BYTE_CAP`` bytes of a repo-relative file."""
    try:
        path = Path(_REPO_ROOT) / rel_path
        data = path.read_bytes()[:_PER_FILE_BYTE_CAP]
        return data.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("gravity_ai_runner: failed to read %s: %s", rel_path, exc)
        return f"# <unable to read {rel_path}: {exc}>"


def _compose_target_code(step_number: int) -> str:
    """Concatenate the per-step files into a single code blob for the prompt."""
    files = _STEP_FILE_MAP.get(int(step_number), ())
    parts: List[str] = []
    for rel in files:
        parts.append(f"\n# === {rel} ===\n")
        parts.append(_read_file_slice(rel))
    return "".join(parts) if parts else "# <no target files mapped for this step>"


def _load_step_templates() -> Dict[int, Any]:
    """Return a {step_number: StepPromptTemplate} map from ai_verification_prompts.

    Done lazily so this module is importable even if the prompts file is
    being edited — the runner soft-fails to an empty map and the CLI prints
    a clear notice rather than crashing.
    """
    try:
        from ai_verification_prompts import ALL_PROMPTS  # noqa: PLC0415

        return {int(p.step_number): p for p in ALL_PROMPTS}
    except Exception as exc:
        logger.warning("gravity_ai_runner: failed to load prompts: %s", exc)
        return {}


def _system_prompt() -> str:
    """Resolve the Gravity system prompt (Prompt Registry override → baseline)."""
    try:
        from settings import settings as _s  # noqa: PLC0415

        if getattr(_s, "PROMPT_REGISTRY_ENABLED", False):
            try:
                from prompt_registry import get_registry  # noqa: PLC0415

                body = get_registry().get("gravity.system")
                if isinstance(body, str) and body.strip():
                    return body
            except Exception as exc:
                logger.debug("gravity_ai_runner: registry lookup failed: %s", exc)
    except Exception:
        pass
    # Baseline — the exact same SYSTEM_PROMPT shipped in ai_verification_prompts.
    try:
        from ai_verification_prompts import SYSTEM_PROMPT  # noqa: PLC0415

        return SYSTEM_PROMPT
    except Exception:
        return (
            "You are 'Gravity', an Expert Quantitative Python Auditor.  "
            "Respond strictly in the requested JSON shape — no prose."
        )


def _build_user_prompt(template: Any, target_code: str) -> str:
    """Compose the per-step user-turn prompt (step body + target code)."""
    body = getattr(template, "prompt_text", "").strip()
    title = getattr(template, "step_title", f"Step {getattr(template, 'step_number', '?')}")
    return (
        f"# Gravity audit: {title}\n\n"
        f"{body}\n\n"
        "--- TARGET PYTHON CODE TO ANALYZE ---\n"
        f"{target_code}\n"
        "--- END TARGET CODE ---\n"
    )


def _run_one_provider(
    provider: Any,
    *,
    system: str,
    user: str,
) -> Optional[Dict[str, Any]]:
    """Call a provider; return ``.model_dump()`` on success, ``None`` on soft-fail."""
    if provider is None:
        return None
    try:
        from llm.schemas import GravityAuditStepResult  # noqa: PLC0415

        result = provider.call_structured(
            system=system,
            user=user,
            schema_model=GravityAuditStepResult,
        )
        if result is None:
            return None
        return result.model_dump()
    except Exception as exc:
        logger.warning("gravity_ai_runner: provider %s soft-failed: %s",
                       getattr(provider, "name", "?"), exc)
        return None


def _claude_provider():
    """Construct Claude (auditor) lazily, returning ``None`` if disabled / no key."""
    try:
        from settings import settings as _s  # noqa: PLC0415

        if not getattr(_s, "GRAVITY_AI_RUNNER_ENABLED", False):
            return None
        key = getattr(_s, "ANTHROPIC_API_KEY", None)
        if not key:
            return None
        from llm.providers import ClaudeProvider  # noqa: PLC0415

        return ClaudeProvider(
            api_key=key,
            timeout_seconds=float(getattr(_s, "LLM_COMMENTARY_TIMEOUT_SECONDS", 8) or 8),
        )
    except Exception as exc:
        logger.warning("gravity_ai_runner: ClaudeProvider construction failed: %s", exc)
        return None


def _gemini_provider():
    """Construct Gemini (cross-checker) lazily; same soft-fail contract."""
    try:
        from settings import settings as _s  # noqa: PLC0415

        if not getattr(_s, "GRAVITY_AI_RUNNER_ENABLED", False):
            return None
        key = getattr(_s, "GEMINI_API_KEY", None)
        if not key:
            return None
        from llm.providers import GeminiProvider  # noqa: PLC0415

        return GeminiProvider(
            api_key=key,
            timeout_seconds=float(getattr(_s, "LLM_COMMENTARY_TIMEOUT_SECONDS", 8) or 8),
        )
    except Exception as exc:
        logger.warning("gravity_ai_runner: GeminiProvider construction failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_step(
    step_number: int,
    *,
    claude=None,
    gemini=None,
    target_code: Optional[str] = None,
) -> StepRunResult:
    """Run a single Gravity audit step.

    Parameters
    ----------
    step_number :
        Integer 1-7 (the prompts shipped in ``ai_verification_prompts.py``).
    claude / gemini :
        Optional pre-constructed providers (injected by tests).  When
        ``None`` the runner constructs them via the lazy factories,
        which return ``None`` when the master switch is off or a key is
        missing.
    target_code :
        Optional override for the code blob passed to the model.  When
        ``None`` the per-step file map is consulted.

    Returns
    -------
    StepRunResult
        Per-step record; either side may be ``None`` (soft-fail).  Disagreement
        is only computed when BOTH verdicts are present.
    """
    templates = _load_step_templates()
    template = templates.get(int(step_number))
    notes: List[str] = []

    if template is None:
        return StepRunResult(
            step_number=int(step_number),
            step_title=f"Step {step_number} (unknown)",
            claude_verdict=None,
            gemini_verdict=None,
            disagreement=False,
            notes=["no prompt template found in ai_verification_prompts.ALL_PROMPTS"],
            timestamp=_utc_iso(),
        )

    if target_code is None:
        target_code = _compose_target_code(int(step_number))

    system = _system_prompt()
    user = _build_user_prompt(template, target_code)

    if claude is None:
        claude = _claude_provider()
    if gemini is None:
        gemini = _gemini_provider()

    if claude is None:
        notes.append("claude provider unavailable (master switch off or ANTHROPIC_API_KEY unset)")
    if gemini is None:
        notes.append("gemini provider unavailable (master switch off or GEMINI_API_KEY unset)")

    claude_verdict = _run_one_provider(claude, system=system, user=user)
    gemini_verdict = _run_one_provider(gemini, system=system, user=user)

    if claude is not None and claude_verdict is None:
        notes.append("claude returned None (soft-fail — see logs for diagnostic)")
    if gemini is not None and gemini_verdict is None:
        notes.append("gemini returned None (soft-fail — see logs for diagnostic)")

    disagreement = (
        claude_verdict is not None
        and gemini_verdict is not None
        and claude_verdict.get("status") != gemini_verdict.get("status")
    )

    return StepRunResult(
        step_number=int(step_number),
        step_title=getattr(template, "step_title", f"Step {step_number}"),
        claude_verdict=claude_verdict,
        gemini_verdict=gemini_verdict,
        disagreement=bool(disagreement),
        notes=notes,
        timestamp=_utc_iso(),
    )


def run_all(
    *,
    claude=None,
    gemini=None,
) -> RunReport:
    """Run every step in :data:`_STEP_FILE_MAP` and aggregate the report."""
    try:
        from settings import settings as _s  # noqa: PLC0415

        enabled = bool(getattr(_s, "GRAVITY_AI_RUNNER_ENABLED", False))
    except Exception:
        enabled = False

    # If callers didn't inject providers, construct each ONCE here so we don't
    # repeatedly hit lazy factories per-step.
    if claude is None and enabled:
        claude = _claude_provider()
    if gemini is None and enabled:
        gemini = _gemini_provider()

    step_numbers = sorted(_STEP_FILE_MAP.keys())
    steps: List[StepRunResult] = []
    for n in step_numbers:
        try:
            steps.append(run_step(n, claude=claude, gemini=gemini))
        except Exception as exc:
            # Belt-and-suspenders.  run_step itself catches, but if we somehow
            # raise, record a skip rather than abort the whole run.
            logger.warning("gravity_ai_runner: step %d uncaught failure: %s", n, exc)
            steps.append(StepRunResult(
                step_number=n,
                step_title=f"Step {n}",
                claude_verdict=None,
                gemini_verdict=None,
                disagreement=False,
                notes=[f"uncaught error: {exc}"],
                timestamp=_utc_iso(),
            ))

    summary = _summarise(steps)
    return RunReport(
        generated_at=_utc_iso(),
        enabled=enabled,
        steps=steps,
        summary=summary,
    )


def _summarise(steps: List[StepRunResult]) -> Dict[str, Any]:
    """Roll up a list of StepRunResult into operator-facing counters."""
    total = len(steps)
    claude_pass = sum(
        1 for s in steps if s.claude_verdict and s.claude_verdict.get("status") == "PASSED"
    )
    claude_fail = sum(
        1 for s in steps if s.claude_verdict and s.claude_verdict.get("status") == "FAILED"
    )
    claude_skip = sum(1 for s in steps if s.claude_verdict is None)
    gemini_pass = sum(
        1 for s in steps if s.gemini_verdict and s.gemini_verdict.get("status") == "PASSED"
    )
    gemini_fail = sum(
        1 for s in steps if s.gemini_verdict and s.gemini_verdict.get("status") == "FAILED"
    )
    gemini_skip = sum(1 for s in steps if s.gemini_verdict is None)
    disagreements = sum(1 for s in steps if s.disagreement)
    return {
        "total_steps": total,
        "claude": {"passed": claude_pass, "failed": claude_fail, "skipped": claude_skip},
        "gemini": {"passed": gemini_pass, "failed": gemini_fail, "skipped": gemini_skip},
        "disagreements": disagreements,
    }


def write_report(report: RunReport, *, path: Optional[str] = None) -> Optional[Path]:
    """Persist ``report`` as JSON.  Returns the path on success, ``None`` on failure."""
    try:
        from settings import settings as _s  # noqa: PLC0415

        target = Path(path or _s.GRAVITY_AI_RUNNER_OUTPUT_PATH)
    except Exception:
        target = Path(path or "output/gravity_ai_audit.json")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".gravity_ai.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(report.to_dict(), fh, indent=2, sort_keys=True)
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return target
    except Exception as exc:
        logger.warning("gravity_ai_runner: failed to write report to %s: %s", target, exc)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report_human(report: RunReport) -> None:
    print(f"=== Gravity AI audit run @ {report.generated_at} ===")
    print(f"Runner enabled: {report.enabled}")
    s = report.summary
    print(f"Steps total: {s.get('total_steps', 0)}")
    print(
        "Claude — passed: {p} failed: {f} skipped: {k}".format(
            p=s.get("claude", {}).get("passed", 0),
            f=s.get("claude", {}).get("failed", 0),
            k=s.get("claude", {}).get("skipped", 0),
        )
    )
    print(
        "Gemini — passed: {p} failed: {f} skipped: {k}".format(
            p=s.get("gemini", {}).get("passed", 0),
            f=s.get("gemini", {}).get("failed", 0),
            k=s.get("gemini", {}).get("skipped", 0),
        )
    )
    print(f"Cross-checker disagreements: {s.get('disagreements', 0)}")
    print()
    for st in report.steps:
        c = st.claude_verdict.get("status") if st.claude_verdict else "—"
        g = st.gemini_verdict.get("status") if st.gemini_verdict else "—"
        flag = "⚠ DISAGREEMENT" if st.disagreement else ""
        print(f"  Step {st.step_number}: claude={c}  gemini={g}  {flag}".rstrip())
        for note in st.notes:
            print(f"    • {note}")


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(
        prog="python -m engine.gravity_ai_runner",
        description="Run the AI Gravity audit (Claude auditor + Gemini cross-checker).",
    )
    parser.add_argument("step", type=int, nargs="?", default=None,
                        help="Integer 1-7. Omit to run every step.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON only (suitable for piping).")
    parser.add_argument("--output", type=str, default=None,
                        help="Override the output path for the JSON report.")
    args = parser.parse_args(argv)

    if args.step is None:
        report = run_all()
    else:
        report = RunReport(
            generated_at=_utc_iso(),
            enabled=False,
            steps=[run_step(args.step)],
            summary={},
        )
        # Recompute summary post-hoc so the single-step shape matches all-steps.
        report = dataclasses.replace(report, summary=_summarise(report.steps))

    written = write_report(report, path=args.output)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _print_report_human(report)
        if written is not None:
            print(f"\nReport written to: {written}")
        else:
            print("\n(report file not written — see logs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
