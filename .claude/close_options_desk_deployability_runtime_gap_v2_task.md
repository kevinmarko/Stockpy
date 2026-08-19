# Task Tracker: Options Desk Deployability Gate — Runtime Wiring Follow-Up (v2)

Verified against `git log --oneline` and `git diff <commit>...<commit> --stat` for this branch's
actual two authored commits this session (`e13b1a2c`, `1cebcc79`), plus the intervening merge of
`origin/main` (`7989e130`, which pulled in PR #791's regime work — not this session's own scope).

## Code fixes

- [x] Remove the dead `hasattr(store, "get_intraday_bars")`-guarded lookup in
      `pilots/zero_dte_engine.py::get_0dte_signals` (the method doesn't exist on
      `HistoricalStore`, so the branch was permanently dead / always `None`).
- [x] Pass `intraday_bars=None` explicitly to `scan_0dte_breakouts`, with an inline comment
      explaining the structural data-availability gap (no intraday/1-minute bar source exists
      anywhere in this repo).
- [x] Document (inline comment, `api/pilots_api.py`) why
      `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]` has no live consumer, unlike its
      three sibling entries (`earnings_crush`, `dispersion_trading`, `zero_dte_engine`).

## Test coverage

- [x] `tests/test_zero_dte_engine.py`: regression test guarding against the dead
      `HistoricalStore.get_intraday_bars` pattern reappearing.
- [x] `tests/test_zero_dte_engine.py`: coverage for the honest `NO_SIGNAL` degrade when
      `intraday_bars=None`.
- [x] `tests/test_options_desk_deployability_runtime_gap.py`: restore T1 —
      `execute_0dte_trade` refuses (rather than fabricating a `1.50` fallback fill price) when
      no quote/spot is resolvable.
- [x] `tests/test_options_desk_deployability_runtime_gap.py`: restore T2 — SPY/QQQ dispersion
      weight maps are genuinely distinct, not copy-pasted.
- [x] `tests/test_dispersion_trading.py`: add T3 — `execute_dispersion_trade(basket=None)`'s
      real-data-sourcing path derives long direction from a real measured positive spread.
- [x] `tests/test_dispersion_trading.py`: add T3 (second case) — same path derives short
      direction from a real measured negative spread.
- [x] `tests/test_pilots_api.py`: coverage confirming `gate_status` is present and correct on
      the three live execute endpoints' responses.

## Documentation

- [x] `docs/signals/vrp_premium_selling.md`: replace the duplicated `## Backtest Validation`
      section (stale Sharpe 0.612 / DSR 1.000 / `deployable=True`) with the actual measured
      2026-08-15 numbers (Sharpe 0.217, DSR 0.000, `deployable=False`), matching
      `docs/VALIDATION_STRATEGY_FIX_LOG.md`.
- [x] `docs/signals/vol_mispricing.md`: add a "Live Paper-Execution Status" section documenting
      the deliberate absence of a paper-execute endpoint.
- [x] `docs/signals/zero_dte_engine.md`: mark "Defects found" items 1 and 2 FIXED with concrete
      remediation and regression-test references.
- [x] `docs/signals/dispersion_trading.md`: document that the identical-8-stock-basket defect is
      only half-fixed (weight maps distinct; constituent lists still overlap) — corrected for
      accuracy rather than overclaimed as fully closed.
- [x] `docs/signals/earnings_crush.md`: small accuracy note on its live `gate_status` wiring.
- [x] `docs/VALIDATION_STRATEGY_FIX_LOG.md`: new 2026-08-18 "Runtime Wiring Follow-Up &
      Doc-Drift Correction" entry itemizing all five closed gaps with verification evidence.
- [x] `CLAUDE.md` / `AGENTS.md`: correct the F1-F16 remediation bullet — it previously claimed
      all four modules "consistently surface and enforce" the gate; now correctly states
      `vol_mispricing` has no live execute path and its gate entry is informational-only.
      (Files auto-synced to each other per the repo's `sync_agent_docs.sh` convention.)

## Branch hygiene

- [x] `git fetch origin && git rebase/merge origin/main` — merged `origin/main` into
      `phased_agent_audit_system` (commit `7989e130`), pulling in PR #791's Gaussian HMM
      refinement work; resolved conflicts in `docs/settings_field_census.{json,md}` and
      `docs/settings_liveness.json` by regenerating from the merged tree (routine
      artifact-regeneration conflict, not new authored logic).
- [ ] Sync local `main` checkout after this PR merges (`git -C <main-checkout> fetch origin &&
      git -C <main-checkout> merge --ff-only origin/main`) — deferred until this PR is actually
      merged; not yet applicable mid-session.

## PR artifacts (this task)

- [x] `.claude/close_options_desk_deployability_runtime_gap_v2_implementation_plan.md`
- [x] `.claude/close_options_desk_deployability_runtime_gap_v2_task.md` (this file)
- [x] `.claude/close_options_desk_deployability_runtime_gap_v2_walkthrough.md`

## Verification

- [x] `pytest tests/test_options_desk_deployability_runtime_gap.py tests/test_zero_dte_engine.py
      tests/test_dispersion_trading.py tests/test_pilots_api.py -q` — 436 passed, 0 failed (see
      walkthrough for the per-file breakdown).
- [ ] `npm run --prefix webapp typecheck` — not applicable; no `webapp/src/` changes in this
      session's own commits.
