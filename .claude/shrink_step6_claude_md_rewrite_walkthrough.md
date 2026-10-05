# Shrink step 6 walkthrough: rewrite `CLAUDE.md` for the post-shrink platform

Branch: `shrink-step6-claude-md-rewrite`, cut from `origin/main` @ `55e34161` (step 5.3, #1085).
Docs only; no code changed.

## Size

| File | Before | After |
|------|--------|-------|
| `CLAUDE.md` | 392,829 bytes, 427 lines | 35,994 bytes, 436 lines (hard-wrapped; the old file had lines up to 16 KB) |
| `AGENTS.md` | identical mirror | identical mirror (`cmp` clean) |
| `docs/FEATURE_TIER_HISTORY.md` | 253,011 bytes | 516,167 bytes (new relocated-entries section) |

## Kept (tightened)

- **Branch Workflow**: the no-domain-split note and all 7 start-of-session rules (low-risk direct to
  main vs PR, no rewriting published history, own-PR rebase allowed, merge-when-verified, unique
  artifact naming for every agent, post-merge main sync). Added the "never `git stash`" rule
  (shared `refs/stash` across worktrees), which was operator memory, not yet in the file.
- **Agent Workflow: Verification & Planning**: mandatory verification, the four Claude Code hooks,
  the Antigravity gap and its advisory ports, plan-first, doc-update step, subagent guidance,
  CLAUDE.md/AGENTS.md mirroring (now also naming `tests/test_agent_docs_sync.py`).
- **Project** summary, plus a new **Runtime today** subsection: the daemon as the one runtime,
  `main.py` being retired (5.4–5.6 pending), the pipeline step order (read from
  `main_orchestrator.py`), `DAEMON_AGENTIC_QUEUE_MODE` with the live `shadow` state and the review
  ending 2026-10-05, and the single universe builder.
- **Key documentation files** table (every row checked; dropped stale counts, added
  `FMP_INTEGRATION.md` and `JULES_INTEGRATION.md`).
- **Frontend strategy** (webapp only, `legacy/` frozen, `shared/` live).
- **Commands**: every script/module path checked to exist; grouped into web app, runtime, tests &
  gates, validation & data. `main.py` and `launch.command` marked as being retired in favor of
  `POST /run` / `shared.daemon_client.trigger_run()`.
- **Architecture** index table (all 11 `docs/architecture/*.md` exist) and a short Gravity note.
- **Conventions**, rewritten as short rules with pointers: CONSTRAINT #4/#6, dead-letter per-ticker
  loops, the `1e-12` std guard, graduated degrade, DTOs, `IDataProvider`/`CompositeProvider`,
  vectorization, lookahead perturbation tests, `FMP_BARS_ADJUSTMENT`, the FMP/Yahoo-only data-source
  policy, `settings.X` not `os.environ` (+ census test), timeouts (+ AST guard), shared per-host rate
  limiters, `ENV_PATH`/`LOCAL_DATA_ROOT`/no bare `db_path`, the autouse DB-isolation fixture
  pattern, `shared/env_io` allowlist/secret rules, `DANGEROUS_KEYS`/`BOOTSTRAP_KEYS`,
  `runtime_flags` read/write paths and precedence, the default-on-admin vs default-off-trading
  policy, the signal-module contract, the deployability gate and options-selling stress gate (both
  still in `validation/harness.py`/`validation/thresholds.py`), sizing single source of truth,
  snapshot parity, OrderManager/idempotency/dry-run/best-effort broker rules, kill switch + risk
  gate + fail-closed macro, the queue-driven Robinhood flow, the device-approval login rules, the
  daemon property-vs-method rule, shutdown budget, extended-hours gate, API auth tiers, mock/live
  parity, and help content.
- **Archived subsystems** pointer list. Each entry was confirmed by `ls legacy/...` (options desk and
  0DTE, FIX/multi-broker, SEC 606, dynamic circuit breaker, Almgren-Chriss, HRP/CVaR, diffusion
  engine, DRL/transformer models, research copilot, ETF transmission, Follow-a-Pilot, Sheets
  publisher); the Streamlit app is noted as deleted.

## Removed or relocated

All 163 bullets from the old "Conventions" and "Recent Architecture Updates" sections were processed
by a script over the saved old file:

- **1 exact duplicate dropped**: the second copy of "Recurring os.environ-bypass/missing-timeout bug
  classes…". (The two differently-worded "Options signal modules retired… step 3d'" bullets were both
  relocated.)
- **5 one-line "archived to `legacy/`" stubs dropped** (options ML/safety gates, Phases 31-36 ×2,
  fabricated SPY spot fix, automated options lifecycle): they carried no detail beyond the pointer
  the new archive list gives.
- **33 bullets → pointer lines** in `docs/FEATURE_TIER_HISTORY.md` ("Entries whose detail lives in a
  dedicated doc"): each names a dedicated walkthrough or `docs/known_issues/*.md` write-up that the
  bullet itself summarized (e.g. the shrink step walkthroughs, the FRED-timeout incident and its two
  follow-ups, the Robinhood login docs, the diffusion SDE sign error).
- **124 bullets relocated verbatim** to `docs/FEATURE_TIER_HISTORY.md` under
  "## 2026-09 CLAUDE.md slimming — relocated entries" → "Entries relocated verbatim", in their
  original order. I chose verbatim over condensed so no detail was lost to paraphrase; the section
  header warns that they are historical and may name archived paths or old defaults.

Nothing load-bearing was dropped: every current rule was restated in the new Conventions section, and
all history is either pointed to or copied.

## Stale references found and fixed (in the rewritten `CLAUDE.md`)

- `legacy/streamlit_command_center/panels.py` (help-content convention): the directory no longer
  exists (the Streamlit app was deleted). Rule restated against `shared/help_content.py` and
  `webapp/src/help/helpContent.ts`.
- The VRP premium-selling gate convention cited `technical_options_engine.py`, `options_ondemand.py`,
  `reporting/options_snapshot.py` and `tests/test_vrp_gate.py`; the first three are archived and the
  test file doesn't exist. Dropped from conventions (options desk is in the archive list).
- "Options matrix integrity" (`technical_options_engine.py` + Gravity STEP 38): archived / step
  removed in 4a. Relocated.
- The persistent-daemon bullet cited `desktop/engine_supervisor.py` and the Streamlit "Launcher tab";
  neither exists. Replaced by the Runtime-today description.
- "See `AGENTS.md`'s 'Safety posture' section": no such section (AGENTS.md mirrors CLAUDE.md). The
  default-value policy is now stated directly.
- The default-on convention bullet listed 16 flags as flipped to default `True`; the code disagrees
  for at least `COMMAND_EXECUTION_ENABLED` (default `False`, checked via `Settings.model_fields`).
  The enumerated list was dropped; only the policy is stated.
- Gravity table said "90 real audit methods (`step_1`–`step_94`)"; `Gravity AI Review Suite.py` now
  has 32 `step_*` methods. The count was dropped.
- Key-docs table counts ("~74 files", "28 known issues"): the real numbers are ~160 docs and 61
  known-issues files. Counts dropped.
- Pipeline step order: written from `main_orchestrator.py` (`AsyncDataFetchStep` → `RunPipelineStep`
  with inner Macro/TrendVolatility/Processing/Forecasting/StrategyEval → `AdvisoryOverlayStep` →
  `AgenticQueueStep` → `BrokerExecutionStep` → `StateSnapshotStep`).
- `NO_VENV_REEXEC`: honored by `scripts/_bootstrap.py` only, not by `main.py`/`main_orchestrator.py`;
  worded accordingly.

A script checked every backticked path in the new `CLAUDE.md`; all resolve (the remaining non-hits
are relative names inside tables, example filenames in the naming rule, and runtime files under
`OUTPUT_DIR`). Setting names were checked against `Settings.model_fields`.

## Not changed (follow-up)

Several skills still cite old CLAUDE.md bullets by name (e.g. "`CLAUDE.md`'s step 4b bullet",
"the `.env` resolution fix bullet", "the `output/daemon.json` staleness fix bullet") in
`.claude/skills/*` and their `.agents/skills/*` mirrors. Those bullets now live in
`docs/FEATURE_TIER_HISTORY.md`'s relocated section. The skills were left as is to keep this PR to the
two agent docs plus the history file; updating the references (in both mirrors) is a small follow-up.

## Verification

- `cmp CLAUDE.md AGENTS.md`: identical.
- `tests/test_agent_docs_sync.py` + `tests/test_investyo_mcp_server.py` (the tests that read these
  docs, incl. `get_doc("CLAUDE.md")` asserting `# CLAUDE.md`): 302 passed.
- Full offline suite (`-m "not network and not slow" -n auto --dist loadgroup`, scratch
  `LOCAL_DATA_ROOT`): see the PR description for the result.
