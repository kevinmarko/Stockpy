# Task Tracker: `pipeline_fundamentals_deadline`

Fixing the unbounded per-ticker fundamentals-refresh loop that wedged the pipeline's
"processing" stage for ~1199s every cycle. See
`.claude/pipeline_fundamentals_deadline_implementation_plan.md` for the full plan and
`.claude/pipeline_fundamentals_deadline_walkthrough.md` for the reviewer walkthrough.

**Note on completion state below:** this tracker was written by the agent responsible
ONLY for the `CLAUDE.md` changelog bullet and these three `.claude/` PR-artifact files
(checked off below as it directly observed those). The code/settings/test/docs-under-
`docs/` items were being implemented concurrently by a separate agent against the same
spec, and this tracker cannot directly observe that agent's completion state — those
items are left as pending checkboxes reflecting PLAN INTENT, not confirmed completion.
**The PR description (and the actual diff/CI state at review time) is the source of
truth for whether each pending item actually landed** — do not treat an unchecked box
here as "not done," and do not treat this file as authoritative once the PR exists.

## Checklist

- [ ] `settings.py`: add `PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE: float`
      (default `60.0`) field with a `Field(description=...)` matching this repo's
      existing timeout-setting documentation voice.
- [ ] `processing_engine.py`: `calculate_fundamental_metrics()` computes one wall-clock
      deadline before its per-ticker loop; sticky skip-and-DTO-fallback of the
      `HistoricalStore.get_fundamentals()` call once the deadline passes; single
      WARNING log on first trip (not one per skipped ticker).
- [ ] `tests/test_processing_engine.py`: new `TestCalculateFundamentalMetrics` class
      covering not-yet-passed / already-passed / passes-mid-loop deadline states.
- [ ] `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`: new dated
      2026-08-28 follow-up section documenting this second, independent stall
      location, its live-reproduction evidence, the fix, and the disclosed
      inner-substep-timeout gap left open.
- [ ] `docs/architecture/signal-engines.md`: extend the existing `processing_engine.py`
      bullet with the new deadline mechanism.
- [ ] `docs/architecture/orchestration-entrypoints.md`: short cross-reference addition
      near the existing FRED-timeout narrative in the `main_orchestrator.py` bullet.
- [x] `CLAUDE.md`: new dated changelog bullet added (this agent's own task — see the
      bullet immediately following "Comprehensive unbounded-timeout sweep" in the
      "## Project" section).
- [x] `.claude/pipeline_fundamentals_deadline_implementation_plan.md` written.
- [x] `.claude/pipeline_fundamentals_deadline_task.md` written (this file).
- [x] `.claude/pipeline_fundamentals_deadline_walkthrough.md` written.
- [ ] Verification run: `pytest tests/test_processing_engine.py -k TestCalculateFundamentalMetrics -v`
      shown to pass (zero failures) before considering the code-side task done — per
      `CLAUDE.md`'s "Verification is mandatory, not advisory" rule.
- [ ] Feature branch created (`lowercase-kebab`, e.g. `fix-fundamentals-cycle-deadline`)
      and PR opened — this is an "everything else" (engine/runtime) change per
      `CLAUDE.md`'s Start-of-session checklist, so it must not be committed directly to
      `main`.
