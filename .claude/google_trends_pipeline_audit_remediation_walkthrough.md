# Walkthrough: Google Trends Pipeline Audit Remediation

## Overview

This PR is the outcome of a full independent audit — 7 parallel Claude Code agents, one per domain — of a large (35-file, ~1,500-line) Google Trends SVI/ASVI pipeline stack built by Gemini Antigravity across 6 sub-efforts plus an LSTM-Attention forecasting model. The stack had never been pushed to GitHub or reviewed. Every domain audited turned up at least one real, confirmed, reproduced bug; two were serious enough to matter for a live paper-trading platform.

## What the audit found, and what this PR fixes

**Critical:**
1. A **~3000x data-fabrication regression** in the stitcher's own "hardening" fix — an asymmetric epsilon floor (`max(sum_a,0.1)/max(sum_b,0.1)`) meant a real, later reading could be rescaled by a factor of 3000 whenever the OTHER window's overlap happened to be zero. Fixed with a symmetric guard (`f=1.0` whenever either side is near-zero) and a direct reproduction test.
2. The **LSTM-Attention model was completely non-functional** — both of its only two invocation paths (`forecasting_engine.py`'s subprocess dispatch, `api/pilots_api.py`'s endpoint) raised `ImportError` on every call, both silently swallowed by broad excepts, so the feature always returned a fake-looking NaN sentinel with no visible error. Fixed and **verified live** — a real TensorFlow subprocess now launches, trains, and returns a genuine prediction.
3. A **genuine 3-way API contract conflict**: this branch had replaced `GET /data/trends/stitch-demo` with an incompatible `/data/trends/{symbol}` contract, directly competing with PR #957 (already merged) and PR #961 (still open), both of which kept the original contract. Per operator direction, PR #957's contract wins — this branch's competing changes to `api/data_api.py`, `webapp/src/api/client.ts`, `webapp/src/api/mock.ts`, `webapp/src/screens/TrendsVisualizer.tsx`, and `tests/test_data_api.py` were reverted to match `main` exactly, while keeping the genuinely valuable parts (the daemon scheduling + `TrendsStore` persistence + pipeline dashboard column) as backend-only diagnostic infrastructure.

**Serious, independently reproduced:**
- A CONSTRAINT #6 violation in the live fetcher: `TrendReq()`'s own network call could raise uncaught, invisible to tests because the constructor was always mocked.
- A CONSTRAINT #4 fabrication in the LSTM feature builder: missing OHLCV/technical columns were zero-filled instead of raising/excluding.
- A test that "passed" for the wrong reason: it patched a module attribute the function under test never actually reads (a local import shadowed it).
- A phantom settings field (read via `getattr(..., None)`, never actually declared).
- A `state_snapshot.json` field documented as real but never actually emitted.
- Dead code in `Gravity AI Review Suite.py` (a function pasted outside its class, never called, would `NameError` if it were).
- Duplicate content pasted twice into `docs/architecture/data-layer.md`.
- Doc overclaims ("live fetching is enabled... pulling authentic data") that ignored the opt-in default-off flag and daemon-only cadence.
- Unbounded growth of a new SQLite table with no dedup (confirmed via the real, already-populated shared production DB — 3,126+ rows from this branch's code having already run live outside any PR process; disclosed as an unmigrated follow-up, not silently fixed).

**What was genuinely solid and needed no fix:**
- The persistence store's core design (correctly resolves its DB path, no hardcoded-literal-path bug — the exact bug class this repo has hit twice before).
- The LSTM lookahead-bias perturbation test.
- The diagnostic-only scoring boundary — confirmed zero references anywhere in `SIGNAL_WEIGHTS`/`signals/` for any of this.

## How this was built

7 audit agents → operator decision on 2 open questions (endpoint contract, remediation scope) → 6 parallel fix agents (one per domain, each given the exact confirmed bug + a precise fix spec) → 2 direct doc fixes by the orchestrating session → independent re-verification of everything (not trusting any fix agent's self-report) → this PR.

## Testing & Validation

- 217 tests passed across every domain-specific test file touched.
- 190 more tests passed in a broader sanity sweep on `settings.py`/`main_orchestrator.py`/`desktop/daemon_runtime.py` (files with wide blast radius that were touched).
- `npm run --prefix webapp typecheck`: clean.
- Both LSTM `ImportError` fixes independently confirmed via direct grep of the corrected import lines, plus a live end-to-end subprocess-dispatch verification.
- `CLAUDE.md`/`AGENTS.md` confirmed byte-identical via `cmp`.
- The 5 reverted endpoint-contract files confirmed byte-identical to `main` via `git diff --stat`.
