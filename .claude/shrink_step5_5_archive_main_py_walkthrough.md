# Step 5.5 walkthrough: archive `main.py` to `legacy/`

Plan: `.claude/shrink_step5_5_archive_main_py_implementation_plan.md` (operator-approved 2026-10-12).
Parent: `.claude/shrink_step5_retire_main_py_implementation_plan.md` §4 (5.5).
**Merge held (operator): not before the daemon has run `primary` cleanly through 2026-10-16.** This PR
removes the RUNBOOK §3.15 rollback; after it, rolling back the cutover = reverting this PR.

## What moved to `legacy/`
`main.py`, `pipeline/steps.py`, `reporting/html_publisher.py` (`daily_report.html`, decision 5),
`engine/trade_signals.py` and `engine/advisory_agent.py` (the `--agent` loop, decision 4),
`reporting/state_snapshot.py`'s advisory writer (`write_state_snapshot`, `load_snapshot_diff`; only caller
was `html_publisher`), and `scripts/com.investyo.daily-advisory.plist`. Tests whose subject was archived
went to `legacy/tests/` (not collected: `pytest.ini testpaths = tests`).

## What stayed, and why
- **`shared/market_hours.py` (new):** `is_us_market_open`, `is_us_market_open_now` (+ its 60 s cache and
  `reset_market_open_cache`), `is_extended_hours`, `is_automatic_run_gated`, moved verbatim from
  `engine/advisory_agent.py`; only `is_automatic_run_gated`'s docstring lost its `main.py` mentions.
  Importers repointed: `desktop/daemon_runtime.py`, `main_orchestrator.py`, `pipeline/production_steps.py`,
  `shared/engine_status.py`, `scripts/preflight_check.py`.
- **`reporting/state_snapshot.py`** keeps only `_safe_float_or_none`, `_rating_consecutive_cycles`,
  `_rating_is_excluded` (imported by `main_orchestrator.py`, now the only snapshot writer).

## Other changes
- `DAEMON_AGENTIC_QUEUE_MODE` description (`settings.py`, `.env.example`): `off` now means nothing
  writes the queue. **Default unchanged (`off`)** per the default-value policy; live is `primary`.
- `install_schedule.command`: `--with-daily-advisory` removed (its plist is archived); any argument → exit 2.
- `pilots/agentic.py`: the "no agent_state.json" message now says the loop was archived (nothing writes
  that file; it already showed "no state" live).
- Gravity: step 28 retargeted to `AdvisoryOverlayStep` / `pipeline.advisory_inputs`; steps 31/51/79 and the
  `--agent` part of 78 retired as explicit `skipped` entries; 55/56/60/81/82 retargeted to the daemon
  files; step 30 now checks `TestAdvisoryOverlayPipeline`. Baseline (`origin/main`): 20 failing steps /
  16 checks; branch before step-30 fix: 19 / 11, the only new failure (`tests/test_shared_market_hours.py`
  missing) resolved since that file now exists.
- Docs: `legacy/README.md`, RUNBOOK (every `main.py` instruction → daemon; §3.15 rollback = revert this
  PR), HOW_TO_GUIDE (help anchors kept), README, `docs/architecture/*`, CLAUDE.md/AGENTS.md,
  FEATURE_TIER_HISTORY, `.agents/skills/{robinhood-execution,alert-rule-authoring}` (the former was a stale
  copy that still said to run `python3 main.py` — missed in 5.4b-ii), CODEOWNERS, script docstrings,
  `alerting.py` docstring.

## Verification
- New gate `tests/test_main_py_archive_import_smoke.py`: blocks the 5 archived module paths and imports 19
  runtime entry points; 5 passed. Mutation: re-adding `import engine.advisory_agent` to
  `shared/engine_status.py` made it fail; restored.
- Whole suite collects with no import errors (12,023 tests).
- Every touched/new test file + `test_daemon_runtime`, `test_agent_docs_sync`, census/liveness,
  `test_help_content`: 945 passed, 3 failed (only the census/liveness freshness gates); after
  regenerating: census/liveness 57 passed.
- Census: 379 → 375 files scanned (−5 archived, +1 `shared/market_hours.py`); distinct keys unchanged
  (251 / 180), so no setting lost its last reader.
- `make ci`: see the PR comment for the run on the final tree.

## Not verified / disclosed
- No live daemon run on this branch (no runtime behavior change: only import paths of the three
  market-hours helpers moved). The soak check before merge covers the live side.
- HOW_TO_GUIDE §6's "Δ Since Last Run" band text may describe the archived `load_snapshot_diff`; not
  re-checked against the daemon.
