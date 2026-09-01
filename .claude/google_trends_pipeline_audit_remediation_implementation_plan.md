# Google Trends Pipeline — 7-Agent Audit Remediation

## §0 Dependency Check

- Base branch: `google-trends-lstm-attention` (a Gemini Antigravity-built stack of 6 sub-efforts + an LSTM-Attention model — 35 files, ~1,500 lines — never pushed to GitHub or opened as a PR before this remediation).
- This branch's live schema/module interfaces were independently audited by 7 parallel Claude Code agents (one per domain) BEFORE any fix was written — see "Audit findings" below.
- Since the audit, PR #957 (`integrate_svi_stitching_ui`) merged to `main`, establishing the real, working contract for `GET /data/trends/stitch-demo`. This remediation reconciles against that merged reality, not the pre-#957 state the original branch was built against.

## Context

An operator-directed audit ("look into the parallel google-trends-* branches") found this branch already implemented essentially the full 6-agent Google Trends pipeline plan from an earlier session, plus an LSTM-Attention forecasting model — built by Gemini Antigravity, never independently reviewed. 7 parallel Claude Code audit agents (one per domain: stitcher, live fetcher, persistence store, pipeline wiring, daemon/API/webapp, docs, LSTM model) found real, confirmed bugs in every domain, including:

- **A ~3000x data-fabrication regression** in the stitcher's own "hardening" commit (asymmetric epsilon floor).
- **A CONSTRAINT #6 violation** in the live fetcher (an uncaught exception path through `pytrends`' own network call, invisible to tests since the constructor was always mocked).
- **A genuine 3-way API contract conflict** — this branch replaced `GET /data/trends/stitch-demo` with an incompatible `/data/trends/{symbol}` contract, directly competing with two other already-open/already-merged efforts (PR #957, PR #961) that kept the original contract.
- **A completely non-functional LSTM-Attention model** — both of its only two invocation paths raised `ImportError` (silently swallowed), plus a CONSTRAINT #4 fabrication (zero-filling missing feature columns) and a false safety claim in `CLAUDE.md`/`AGENTS.md`.
- Assorted smaller bugs: a test patching the wrong module namespace (passing for the wrong reason), a phantom settings field, dead code in `Gravity AI Review Suite.py`, duplicated doc content, doc overclaims about live-fetching being unconditionally enabled.

The operator directed: (1) PR #957's endpoint contract wins — this branch's competing contract is reverted, not kept; (2) fix everything else and open a PR.

## Approach

6 parallel fix agents (one per domain, mirroring the audit split; docs findings folded into the owning agent plus 2 direct fixes by the orchestrating session), each given the exact confirmed bug + fix spec from the audit, each independently re-verified (real pytest/vitest/typecheck runs, not self-reports) before this PR was opened.

### Files touched, by domain
- **Stitcher**: `data/trends_stitcher.py`, `tests/test_trends_stitcher.py`, `docs/signals/google_trends_asvi.md` (math section)
- **Live fetcher**: `data/google_trends_client.py`, `tests/test_google_trends_client.py`, `requirements.txt`
- **Persistence store**: `data/trends_store.py`, `tests/test_trends_store.py`, `conftest.py` (root-level, not `tests/conftest.py`), `tests/conftest.py` (fixture removed from here)
- **Pipeline wiring**: `pipeline/production_steps.py`, `settings.py`, `main_orchestrator.py`, `tests/test_production_steps_google_trends.py`, `tests/test_state_snapshot_parity.py`
- **Daemon/API/webapp reconciliation**: `desktop/daemon_runtime.py`, `api/data_api.py`, `webapp/src/api/client.ts`, `webapp/src/api/mock.ts`, `webapp/src/screens/TrendsVisualizer.tsx`, `tests/test_data_api.py`, `tests/test_google_trends_daemon.py` (`tests/test_data_api_trends.py` deleted — tested the reverted endpoint)
- **LSTM-Attention model**: `forecasting_engine.py`, `cnn_lstm_worker.py`, `ml/asvi_feature_engineering.py`, `api/pilots_api.py` (one import line), `"Gravity AI Review Suite.py"`, `CLAUDE.md`/`AGENTS.md`, `tests/test_asvi_feature_engineering.py`, `tests/test_lstm_attention_worker.py`, `tests/test_pilots_strategy_matrix.py`
- **Docs** (done directly by the orchestrating session, not delegated): `docs/signals/google_trends_asvi.md` (Section D overclaim, missing settings, related-files header), `docs/architecture/data-layer.md` (duplicate bullet block removal)

## Documentation-update step (explicit, per CLAUDE.md)

- `docs/signals/google_trends_asvi.md` — updated: corrected stitcher formula (§2A), corrected Section D (no longer overclaims live fetching as unconditionally enabled), added missing settings (`GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS`, `GOOGLE_TRENDS_MAX_SECONDS_PER_CYCLE`), expanded "Related Files" header. **Done.**
- `docs/architecture/data-layer.md` — duplicate `data/google_trends_client.py`/`data/trends_store.py` bullet block (pasted twice in the original branch) reduced to one occurrence each. **Done.**
- `CLAUDE.md`/`AGENTS.md` — Phase 4 LSTM-Attention bullet corrected with an explicit "Correction (audit fix)" addendum documenting the ImportError bug and its fix, including live end-to-end verification. Both files confirmed byte-identical after the edit. **Done.**

## Verification

- `pytest` across all touched test files: 217 passed (domain-specific run) + 190 passed (broader blast-radius sanity sweep on `settings.py`/`main_orchestrator.py`/`conftest.py` consumers) = 407 total, 0 failures.
- `npm run --prefix webapp typecheck`: clean.
- Two ImportErrors in the LSTM model fixed and verified **live** (real TensorFlow subprocess dispatch, real non-NaN prediction returned).
- Diagnostic-only boundary re-confirmed: zero references anywhere in `SIGNAL_WEIGHTS`/`signals/` package for any Google Trends/ASVI feature.
- 3-way endpoint conflict resolved: `api/data_api.py`, `webapp/src/api/client.ts`, `webapp/src/api/mock.ts`, `webapp/src/screens/TrendsVisualizer.tsx`, `tests/test_data_api.py` confirmed byte-identical to `main` (i.e., to PR #957's already-merged contract) after the revert.

## Known, disclosed follow-ups (not fixed in this PR — flagged, not silently ignored)

- **Existing production-DB migration**: this machine's real shared `~/.stockpy_local/quant_platform.db` already has 3,126+ un-deduplicated `raw_trends_downloads` rows from this branch's own code having already run live (outside any PR/merge process) before this audit. The new `UniqueConstraint` only protects NEW databases going forward — it does not retroactively deduplicate the existing table. Migrating the live table requires explicit operator sign-off and was deliberately not attempted here.
- **Rate limiter is still in-process only** (no cross-process throttle layer, unlike FMP/EDGAR/GDELT) — a real gap disclosed by the fetcher audit, not fixed here (out of scope: a full cross-process throttle port is a separate, larger task).
