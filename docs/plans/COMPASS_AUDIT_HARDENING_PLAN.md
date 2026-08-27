# Hardening Plan: Confirmed Gaps from the Compass Audit Brief

## Context

The user supplied a third-party brainstorming brief (`compass_artifact_wf-cadb6382...md`) auditing
this repo against general quant-platform best practices: SR 11-7 model risk management, the
Knight Capital execution-boundary lesson, a 5-vector CNN-LSTM leakage taxonomy, DSR/PBO
backtest-overfitting statistics, property-based testing, ML observability/drift, PWA/service-worker
hardening, and multi-agent coding-workflow governance. The brief was written without visibility into
this repo's actual code or its extensive `CLAUDE.md` changelog of prior hardening work — its "Wave 1"
severity ranking (Robinhood MCP live orders, fabricated-data deployability, conflicting Kelly
implementations) reads as generic best-practice caution, not a fresh finding.

Rather than implement the brief's recommendations blindly, 4 agents (3 Explore + 1 Plan) verified
each claim directly against the live repo. Result: **most of the brief's top concerns are already
resolved** by prior work already documented in `CLAUDE.md`. But the process surfaced **two live,
confirmed, currently-shipping gaps** that land close to what the brief warned about (one is
essentially the exact "fabricated data → deployable:true" scenario, live in production API code
today), plus four smaller, real, scoped gaps. This plan hands those six confirmed items to
Antigravity/Gemini to implement — organized by `CLAUDE.md`'s own risk tiers — with Claude Code
auditing the finished work afterward against the acceptance criteria defined here.

**Everything not listed under "Confirmed gaps" below is already resolved and must NOT be
re-implemented** — re-doing already-shipped work would be wasted effort and risks introducing
regressions into working code.

---

## Already resolved — verified directly, do not re-implement

| Brief concern | Verified status |
|---|---|
| Robinhood MCP live-order risk | No code path in this repo can place a live order without gating. Alpaca/FMP live path has a real server-side `pending_approval` durable state + atomic claim (`broker_live_execution_mcp.py`, `execution/live_trade_proposals_store.py`). Robinhood MCP is a third-party connector with its own `.claude/skills/robinhood-execution/SKILL.md` procedure — see Gap 2 below for the one narrow residual risk here. |
| Conflicting Kelly implementations / uncapped sizing | One source of truth: `sizing/kelly.py` + `sizing/vol_target.py`, composed via `sizing/position_sizer.py::size_position()`. Every caller applies a cap (`KELLY_CAP`, `MAX_LEVERAGE`, `MAX_POSITION_WEIGHT`, `MAX_PORTFOLIO_GROSS`). `pilots/mirror.py`'s Follow-sizing path was investigated specifically (does it skip the portfolio gross cap?) — confirmed **by design**, not a bug: it never routes through the systematic Kelly pipeline at all, it sizes a human-typed dollar commitment to one specific Pilot. See "Disclosed, not fixed" section below for the one adjacent gap this surfaced. |
| CNN-LSTM data leakage (all 5 vectors: scaler leakage, window-overlap, shuffling, non-purged CV, future-information features) | Fully fixed with real perturbation/purge tests (`forecasting_engine.py::fit_scalers_on_train`/`purged_train_val_split`, `cnn_lstm_worker.py`, `tests/test_forecasting_lookahead.py`, `tests/test_cnn_lstm_worker.py`). |
| Deflated Sharpe Ratio / PBO | Real, computed, hard-gated (`validation/metrics.py`, `validation/harness.py::ValidationReport.deployable`, thresholds in `validation/thresholds.py`). Two disclosed, deliberate limitations — not bugs — noted below. |
| Blameless postmortem practice | Already functions as designed: `docs/incident_log.md`'s template + the `docs/known_issues/*.md` convention (43 entries), explicitly framed as "kept even after a fix lands... exactly the context a future regression needs." |
| Multi-agent AI coding workflow governance (mechanical verification gates, habituation/diff-size risk, plan/code divergence, hard-wired human gates, AI tech-debt) | **Already fully shipped** — see `docs/plans/AGENT_WORKFLOW_HARDENING_PLAN.md` and `CLAUDE.md`'s "Agent Workflow: Verification & Planning" section: real blocking `Stop` hooks, targeted-test `PostToolUse` hooks, `/verify`/`/verify-webapp` slash commands, a `test-writer` subagent, mandatory Implementation Plans for engine/execution/sizing/validation work, and a documentation-update step required in every plan. |
| FMP_API_KEY / backend secrets baked into the PWA via `VITE_` prefix | Does not occur anywhere — confirmed by exhaustive grep. (`VITE_API_TOKEN` is a different, already-disclosed, already-mitigated tradeoff — see below, no new work.) |
| FastAPI security basics (CORS+credentials, SQL injection, auth) | Not separately re-audited in this pass (out of scope — the brief's angle here was generic OWASP hygiene, not a specific finding); no evidence found in the exploration that contradicts this being handled per the extensive existing auth-tiering conventions already documented across `CLAUDE.md`'s API bullets. Not part of this plan. |

---

## Confirmed gaps — implementation spec

Each gap below states its `CLAUDE.md` Start-of-session-checklist risk classification. **"Everything
else" gaps require Antigravity to produce its own Implementation Plan and open a feature branch +
PR before writing code** — do not skip straight to code for those three.

### Gap 1 (CRITICAL — Wave 1) — Fabricated-OHLCV backtest can report `is_deployable: true`

**Classification: Everything else — feature branch + PR + its own Implementation Plan.**
Touches `validation/` runtime logic and an endpoint driving a real "Deploy to Paper Broker" UI
affordance.

**The bug, confirmed live:** `api/pilots_api.py`'s `post_pilots_ai_research_backtest()` (backs both
`POST /pilots/ai/research/backtest` and `POST /pilots/ai/backtest/autonomous`, ~lines 7981-8021)
silently falls back to `validation/autonomous_backtest_runner.py::AutonomousBacktestRunner.generate_synthetic_ohlcv(500, regime="bull", seed=42)`
— a GBM random-walk generator — whenever `HistoricalStore.get_bars(sym)` returns `None` or fewer
than 50 rows. `AutonomousBacktestResult.to_dict()` has no provenance field, so the response is
indistinguishable from a real-data run and can carry `is_deployable: true`. The only consumer is
`webapp/src/components/ai/ResearchCopilotView.tsx`, which renders a "🚀 DEPLOYABLE (ALL GATES
PASSED)" badge and a one-click "Deploy to Paper Broker" button gated solely on this field
(`webapp/src/screens/PaperBroker.tsx`). This is exactly the CONSTRAINT #4 pattern this repo already
fixed once for `docs/known_issues/options_vpin_fabricated_live_data.md` — mirror that fix's spirit
(never let a fabricated value be indistinguishable from real), adapted to this module's existing
"ran, but explicitly gated closed" idiom (see the AST-unsafe-code path in
`tests/test_pilots_paper_broker.py:2966`, which already returns `is_deployable: False` +
`failure_reasons` rather than nulling the whole response).

**Fix:**
1. `validation/autonomous_backtest_runner.py`: add `data_source: str = "unknown"` and
   `is_synthetic_data: bool = False` to `AutonomousBacktestResult` and its `to_dict()`. Add
   `data_source: str = "unknown"` to `run()`'s signature. Immediately before constructing the final
   result, force-fail-closed:
   ```python
   is_synthetic = data_source != "real_historical_bars"
   if is_synthetic and is_deployable:
       is_deployable = False
       failure_reasons.append(
           f"NOT DEPLOYABLE: backtest ran on data_source={data_source!r} "
           "-- a synthetic-data run can never certify real-market deployability."
       )
   ```
   This is an allowlist rule: `is_deployable` can only ever be `True` when
   `data_source == "real_historical_bars"` — the default `"unknown"` (a caller that forgets to pass
   it) also fails closed. Also thread `data_source` through the early AST-compile-failure return path
   for consistency.
2. `api/pilots_api.py`: set `data_source = "real_historical_bars"` on the successful-fetch branch,
   `data_source = "synthetic_demo_data"` on the fallback branch, pass it into `runner.run(...)`.
3. `webapp/src/api/types.ts`: add **required** `data_source: string; is_synthetic_data: boolean;` to
   `AutonomousBacktestResponse` (required, not optional, so no response shape can silently omit
   them — this will force every mock literal in `webapp/src/api/mock.ts` to declare them, which is
   the point).
4. `webapp/src/components/ai/ResearchCopilotView.tsx`: add a visible banner near the existing
   deployability badge shown whenever `is_synthetic_data` is true, explaining plainly that no real
   historical data was available for the requested symbol.
5. Update `webapp/src/components/ai/ResearchCopilotView.test.tsx`'s existing mocked responses to
   include the two new fields, plus a new test asserting: `is_synthetic_data: true` → the synthetic
   banner renders and the "Deploy to Paper Broker" button does not appear.

**Acceptance criteria:**
- New test in `tests/test_pilots_paper_broker.py::TestPilotsAIResearchBacktest`: mock
  `HistoricalStore.get_bars` to return `<50` rows or raise → assert `data_source ==
  "synthetic_demo_data"`, `is_synthetic_data is True`, `is_deployable is False` regardless of gate
  math.
- A second test with a real ≥50-row fixture → assert `data_source == "real_historical_bars"` and
  `is_deployable` reflects genuine gate computation (not force-overridden).
- New test in `tests/test_autonomous_backtest_runner.py`: a strategy engineered to pass all 4 gates,
  called with `data_source="synthetic_demo_data"` → assert `is_deployable is False` and
  `failure_reasons` names the synthetic-data reason — proving the override actually fires.
- `npx vitest run src/components/ai/ResearchCopilotView.test.tsx` clean; `npm run --prefix webapp
  typecheck` clean.

**Docs:** new `docs/known_issues/autonomous_backtest_synthetic_data_undisclosed.md` (mirror the VPIN
doc's structure: what happened, why, the fix, verification, what remains out of scope). Update
`docs/architecture/ml-and-reports.md`'s existing `validation/autonomous_backtest_runner.py` bullet to
describe the new provenance field.

---

### Gap 2 (Wave 1) — Robinhood one-confirmation-per-order gate is prose-only, not code-pinned

**Classification: Low-risk — test-only + a new known_issues doc. Direct to main after self-review.**

**The gap:** the Robinhood MCP order-placement tools are a third-party connector (not this repo's
code), so nothing here can literally intercept a call to them. The "one human confirmation per
order, never batch, kill-switch recheck before every placement" safety contract lives entirely in
`.claude/skills/robinhood-execution/SKILL.md`'s "Invariants (never violate)" section — a natural-
language procedure an LLM agent is instructed to follow, not a code-enforced gate. `tests/test_robinhood_e2e.py`
already exists but tests a hand-written Python **reimplementation** of the described behavior; it
never reads the live `SKILL.md` file, so a future edit that silently weakened the prose (e.g. removed
"never batch") would not be caught by that suite.

**Fix:** add a new test class to `tests/test_robinhood_e2e.py` (e.g. `TestSkillMdInvariantsPinned`)
that reads `.claude/skills/robinhood-execution/SKILL.md` directly and asserts specific required
phrases from its "Invariants (never violate)" section are still present verbatim — e.g. the
preview-always requirement, the never-place-in-review-mode requirement, "one explicit human
confirmation per placed order / no batch", the "recheck immediately before each placement"
kill-switch language, the `execution_placed.jsonl` idempotency requirement, and the
live-quote-not-stale-snapshot-price requirement. Fail with a message naming exactly which clause
vanished, mirroring this repo's existing `tests/test_help_content.py::TestAnchorValidity` style of
pinning prose-as-contract via an automated check.

**Acceptance criteria:** the new test class passes today; verify manually (do not commit) that it
fails if one invariant line is temporarily deleted locally.

**Docs:** new `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md` disclosing this
residual, structural gap plainly — the confirmation gate is a behavioral contract because the
counterparty is a third-party MCP server this repo cannot intercept; the new pinning test is
defense-in-depth against silent prose drift, not a substitute for code enforcement. No settings
changes.

---

### Gap 3 (Wave 2) — No input/feature-distribution drift detection (PSI)

**Classification: Everything else — feature branch + PR + its own Implementation Plan.** Touches
`validation/` and `scripts/preflight_check.py` (a go-live gate script), even though the new flag
defaults off and the check is warning-only.

**The gap:** `validation/drift.py` only detects OUTPUT/outcome drift (CUSUM/Page-Hinkley on
calibration error vs. realized outcomes). There is zero PSI, Jensen-Shannon, or covariate-shift
detection anywhere on model INPUT features.

**Fix:** new module `validation/covariate_drift.py`, mirroring `validation/drift.py`'s existing
shape (pure detector → frozen result dataclass → adapter → alert-dispatching wrapper):
- `compute_psi(reference, current, n_buckets=10) -> float` — standard quantile-bucketed PSI.
- `PSIResult` frozen dataclass (`drift_detected`, `psi`, `feature`, `details`); never raises
  (CONSTRAINT #6) — degrades to `psi=None, drift_detected=False` on too-short/degenerate input.
- `PSI_ALERT_THRESHOLD = 0.25` (named constant, comment citing standard PSI bands: <0.1 no shift,
  0.1-0.25 moderate, >0.25 significant).
- `adapt_symbol_history_to_windows(df, column, reference_size=60, recent_size=20)` — slices an
  **already-computed** dashboard column (e.g. `RSI_2`, `Realized_Vol_Rank` from `config.COLUMN_SCHEMA`
  / `processing_engine.py`) into reference/recent windows. Must reuse existing computed columns —
  never invent a new data source.
- `check_and_alert_feature_drift(df, columns=(...), send_alert_fn=None)` — loops columns, calls
  `observability.alerts.send_alert("WARNING", ...)` on any `drift_detected`, mirroring
  `check_and_alert_recommendation_drift`'s structure.
- New setting `FEATURE_DRIFT_PSI_ENABLED: bool = Field(default=False, ...)`, matching the "new
  diagnostic instrumentation defaults off" convention already established by
  `MARKET_DATA_LATENCY_TRACKING_ENABLED` — copy its description tone.
- New `scripts/preflight_check.py::check_feature_drift()`, mirroring `check_calibration_drift()`'s
  shape exactly (gated on the new flag, WARNING-only, added to `ALL_CHECKS`).

**Acceptance criteria:** new `tests/test_covariate_drift.py` — PSI known-value sanity checks
(identical distributions → PSI≈0; a shifted distribution → PSI above threshold), degenerate/short-
input never-raises coverage, and an alert-dispatch test with a mocked `send_alert_fn` (mirror
`tests/test_drift.py`'s existing pattern). The new preflight check must be a no-op when the flag is
`False` (assert `ALL_CHECKS` behavior unaffected).

**Docs:** confirm the exact doc file describing `validation/drift.py` before writing (grep first —
it was not conclusively located during planning; `docs/plans/OBSERVABILITY_PLAN.md` references it,
but the canonical architecture doc location needs re-confirming). Add a bullet describing the new
module there. `CLAUDE.md`/`AGENTS.md` need a one-line mention of the new setting per the
"new setting → docs" rule.

---

### Gap 4 (Wave 2) — No property-based (Hypothesis) tests for sizing invariants

**Classification: Low-risk in spirit (test-only, no production code touched) — but flag the new
pinned dependency (`hypothesis` in `requirements.txt`) to the operator explicitly before merging,
since it is a new supply-chain addition even though it changes no runtime behavior.**

**The gap:** `hypothesis` is not installed and not in `requirements.txt`. All sizing-math tests are
hand-written examples, not generated ones.

**Fix:**
1. `requirements.txt`: add `hypothesis>=6.100,<7  # property-based testing for sizing invariants
   (tests/test_sizing_properties.py)`, matching the file's existing pinning/comment convention.
2. New `tests/test_sizing_properties.py` (one dedicated cross-cutting file, since the invariants
   span 3 modules) with `@given(...)` properties matching real input domains:
   - `fractional_kelly(p, b, fraction, cap)`: for `p∈[0,1]`, `b∈[0.01,20]`, `fraction∈[0.01,1.0]`,
     `cap∈[0.01,1.0]` → output is `NaN` OR `0.0 <= output <= cap`.
   - `volatility_target_weight(realized_vol, target_vol, max_leverage)`: for `realized_vol∈[1e-6,5.0]`
     and `max_leverage∈[0.1,10.0]` → output is `NaN` OR `0.0 <= output <= max_leverage`.
   - `size_position(...)`: across its regime/meta-label/escalation kwargs (including deliberately
     probing `escalation_factor > 1.0` as an edge case not currently bounded by a Pydantic field
     constraint — if this reveals `final_weight` can exceed `max_position_weight`, that is a genuine
     finding to report, not to silently work around) → `final_weight` is `NaN` OR
     `0.0 <= final_weight <= max_position_weight`.
   - `apply_portfolio_gross_cap(per_name_weights, max_gross)`: for 1-10 symbols with weights in
     `[-2,2]` and `max_gross∈[0.5,5.0]` → `sum(abs(w) for w in scaled_weights.values() if
     math.isfinite(w)) <= max_gross + epsilon`.
3. Confirm `.github/workflows/ci.yml` needs no change for Hypothesis to run under the existing
   `pytest -m "not network"` invocation (it shouldn't — Hypothesis is a pytest plugin with no special
   marker requirement — but verify directly before assuming).

**Acceptance criteria:** `pytest tests/test_sizing_properties.py -q` passes; each property function
has a one-line docstring naming the invariant it defends.

**Docs:** none required beyond the `requirements.txt` inline comment above.

---

### Gap 5 (Wave 3) — Model inventory (`ml/registry.yaml`) missing `owner`/`materiality_tier`

**Classification: Low-risk — YAML data + a doc bullet, no `.py` runtime logic changes. Direct to
main after self-review.**

**Fix:** `ml/registry.yaml` — add a comment block documenting two new optional fields (mirroring the
existing "Fields"/"Provenance fields" comment sections), then add `owner: <value>` and
`materiality_tier: <value>` to all 4 existing entries (`lgbm_ranker`,
`meta_labeler_timeseries_momentum`, `meta_labeler_cross_sectional_momentum`,
`options_meta_labeler`). Confirmed safe: `ml/registry_io.py::update_model_metrics()` mutates entries
by explicit key name only — any new key survives every automated retraining write untouched.
**`owner` should be left as a placeholder for the operator to fill in** (no way to know real
ownership); a reasonable default `materiality_tier` for all 4 today is `"experimental"` since all 4
currently show `deployable: false` in the registry — but flag this value for the operator to confirm
rather than asserting it silently.

**Acceptance criteria:** no existing test regresses; add one assertion that `load_registry()`
round-trips the two new keys if a registry-loading test file exists (none was found under
`tests/test_registry_io.py` during planning — confirm whether registry-loading has test coverage
elsewhere before assuming there's nothing to extend).

**Docs:** update `docs/architecture/ml-and-reports.md`'s `ml/registry.yaml` bullet to mention the two
new optional fields.

---

### Gap 6 (Wave 3) — PWA service worker has no `runtimeCaching` for API JSON requests

**Classification: Everything else — feature branch + PR + its own Implementation Plan.** Changes
runtime network behavior for every API call the app makes and carries a real regression risk
(breaking SSE streaming) if scoped incorrectly.

**The gap:** `webapp/vite.config.ts`'s `VitePWA({...})` `workbox` block has only `globPatterns` /
`navigateFallback` / `maximumFileSizeToCacheInBytes` — no `runtimeCaching` array. GET requests to the
4 API base URLs (`apiBaseUrl`, `dataApiBaseUrl`, `metricsApiBaseUrl`, `controlApiBaseUrl` from
`webapp/src/config/env.ts`) bypass the service worker entirely: no caching, no offline fallback.
**Already correctly handled, do not touch:** `registerType: "prompt"` is already set with a real,
already-wired update-available/reload-prompt flow (`webapp/src/hooks/usePwaStatus.ts` +
`webapp/src/components/PwaStatusSection.tsx`) — this answers the brief's "cache versioning +
update-notification flow" ask; leave it alone.

**A regression risk found during planning, not in the original brief, that MUST be handled:**
`webapp/src/api/client.ts` has a real Server-Sent-Events GET endpoint (`.../jobs/${jobId}/stream`,
via native `EventSource`). SSE GET requests **do** pass through the service worker's `fetch` event.
A broad caching rule matching by API origin would intercept and attempt to buffer this endless-stream
response, breaking the live job-status stream (and possibly AI chat streaming). **The runtime-caching
predicate must explicitly exclude any path ending in `/stream`** — this is a required acceptance-test
line item, not an implementation detail to discover incidentally.

**Fix:**
1. New pure, testable module `webapp/vite.pwa-runtime-caching.ts` exporting
   `buildApiRuntimeCaching(baseUrls): RuntimeCaching[]`.
2. Each entry: `urlPattern: ({url, request}) => request.method === "GET" && url.origin === <resolved
   base> && !url.pathname.endsWith("/stream")`, `handler: "NetworkFirst"` (not
   StaleWhileRevalidate — this app displays live quotes/positions/portfolio values, and SWR would
   show a stale cached financial figure first on every load, which risks looking like a fresh read;
   NetworkFirst with a short `networkTimeoutSeconds` prefers live data and only serves cache on
   genuine network failure/timeout), `options: { cacheName, networkTimeoutSeconds: 4, expiration: {
   maxEntries: 100, maxAgeSeconds: 120 }, cacheableResponse: { statuses: [0, 200] } }`.
3. `webapp/vite.config.ts`: convert to the `defineConfig(({mode}) => {...})` function form, call
   `loadEnv(mode, process.cwd(), "")` to resolve the 4 real base URLs the same way `env.ts` does
   (env var or matching `URL_DEFAULTS` fallback — do not hardcode dev-only ports), pass the result
   into `buildApiRuntimeCaching(...)` to populate `workbox.runtimeCaching`.

**Acceptance criteria:** new `webapp/vite.pwa-runtime-caching.test.ts` (vitest) asserting: the
predicate matches a GET to each of the 4 configured origins; does **not** match any `/jobs/x/stream`
path; does not match a non-GET request; uses `handler: "NetworkFirst"`. Manual check via
`/verify-webapp`: build the PWA, go offline in devtools, confirm a previously-fetched API GET still
resolves from cache while the job-stream endpoint is unaffected when online.

**Docs:** add a bullet to `docs/architecture/webapp-and-gui.md` describing the runtime-caching
addition, the NetworkFirst justification, and the SSE exclusion requirement (no existing
service-worker-behavior section was found there during planning — confirm before adding a
freestanding bullet vs. a new subsection).

---

## Disclosed, not fixed — document only, do not write code for these

- **`pilots/mirror.py` has no aggregate cap across multiple concurrent Follows.** Investigated
  specifically: per-Follow sizing is a deliberate, human-typed dollar commitment (not a bug that it
  skips `MAX_PORTFOLIO_GROSS`), but there is genuinely no ceiling on the *sum* across several
  simultaneous Follows against total account equity. Add a short note to `pilots/mirror.py`'s own
  module docstring (or a `docs/known_issues/*.md` entry) disclosing this as a known, accepted
  limitation — leave the actual design decision (whether to add an aggregate cap) to the operator.
- **`VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED` defaults `False`.** 5 currently-`deployable`
  strategies rely on a `return 1.0` DSR shortcut rather than the mathematically-correct single-trial
  path. This is already disclosed in the setting's own description as requiring live-market data
  access to re-verify before flipping — out of scope for an autonomous code change; note it exists,
  do not change the default.
- **Family-wise multiple-testing correction (Benjamini-Hochberg) is computed but deliberately
  advisory-only**, not wired into the hard `deployable` gate (per its own docstring — promoting it
  would decertify some currently-deployable strategies). This is a considered design choice, not a
  bug — flag for the operator's own decision, do not change it unilaterally.
- **`VITE_API_TOKEN` is baked into the bundle on loopback origins by design**, with an already-coded
  mitigation (sessionStorage) for non-loopback origins. Already disclosed in `apiToken.ts`'s own
  docstring. No new work.

---

## Sequencing instructions for Antigravity/Gemini

1. `git fetch origin && git rebase origin/main` before starting, per `CLAUDE.md`'s Start-of-session
   checklist.
2. Do Gaps 2 and 5 first (low-risk, direct-to-main after self-review) — they're small, independent,
   and unblock nothing else.
3. For Gaps 1, 3, and 6 (each "everything else"): open a feature branch per gap (don't combine them —
   keep diffs small and independently reviewable, per this repo's own agent-workflow-hardening
   guidance on diff size), produce an Implementation Plan for that gap specifically before writing
   code, and include the documentation-update step named above in that plan.
4. Gap 4 (Hypothesis tests) can be its own small branch+PR or bundled with Gap 1/3's PR if convenient
   — it's low-risk but touches `requirements.txt`, so use judgment; a standalone PR is cleaner.
5. Per `CLAUDE.md`'s PR Artifacts rule: any implementation plan/task-tracker/walkthrough committed
   under `.claude/` must use a unique, feature-scoped filename (e.g.
   `.claude/autonomous_backtest_provenance_implementation_plan.md`,
   `.claude/feature_drift_psi_implementation_plan.md`,
   `.claude/pwa_runtime_caching_implementation_plan.md`) — never a bare `plan.md`/`task.md`.
6. Run every acceptance-criteria test listed above for each gap before opening its PR.

---

## What Claude Code will audit afterward

Once Antigravity's PR(s) land, Claude Code will independently verify — not just re-read the diff, but
re-run — the following, per the repo's "verification is mandatory, not advisory" policy:

1. **Gap 1**: re-run the new `tests/test_pilots_paper_broker.py` and
   `tests/test_autonomous_backtest_runner.py` tests directly; manually construct a request that hits
   the `<50`-bars fallback and confirm the live response actually carries `is_synthetic_data: true`
   and `is_deployable: false` — not just that the tests pass, but that the endpoint behaves this way
   for real. Confirm the webapp banner renders and the deploy button is genuinely absent (browser
   check via `/verify-webapp`, not just a typecheck).
2. **Gap 2**: confirm the new pinning test actually fails when a required invariant clause is
   removed (spot-check by temporarily deleting one locally, not committing it).
3. **Gap 3**: confirm `FEATURE_DRIFT_PSI_ENABLED=False` (default) leaves `preflight_check.py`'s output
   byte-identical to before the change; confirm the PSI math against a hand-computed example, not just
   that the test suite is green.
4. **Gap 4**: run `pytest tests/test_sizing_properties.py -q` directly; check whether the deliberate
   `escalation_factor > 1.0` probe surfaced a real finding, and if so confirm it was reported rather
   than silently patched around.
5. **Gap 5**: confirm `ml/registry_io.py`'s existing behavior is unchanged (no code was touched
   unnecessarily) and the YAML still parses/round-trips correctly.
6. **Gap 6 (highest regression risk — audit this one most carefully)**: run
   `webapp/vite.pwa-runtime-caching.test.ts`; then actually build the PWA, go offline in devtools, and
   confirm (a) a previously-fetched API GET resolves from cache, (b) the job-stream SSE endpoint is
   never intercepted online, and (c) live quote/portfolio data is not shown stale-first on a normal
   (online) load.
7. **Cross-cutting**: confirm each PR's docs were actually updated per `CLAUDE.md`'s requirement (not
   just planned), confirm `.claude/` plan-artifact filenames are unique/scoped per the naming rule,
   and confirm no unrelated scope creep was bundled into any of the three feature-branch PRs.

## Verification commands (for both Antigravity and the Claude Code audit pass)

```bash
pytest tests/test_pilots_paper_broker.py -k TestPilotsAIResearchBacktest -q
pytest tests/test_autonomous_backtest_runner.py -q
pytest tests/test_robinhood_e2e.py -k TestSkillMdInvariantsPinned -q
pytest tests/test_covariate_drift.py -q
pytest tests/test_sizing_properties.py -q
npm run --prefix webapp typecheck
npx vitest run src/components/ai/ResearchCopilotView.test.tsx --prefix webapp
npx vitest run webapp/vite.pwa-runtime-caching.test.ts
```
