# Task tracker — fix-known-open-gaps

**Corrected 2026-08-29.** The original version of this tracker checked off two items that were
not actually true at the time — see `.claude/fix_known_open_gaps_walkthrough.md` for the full
post-mortem. Both are now genuinely done, as tracked below.

- [x] Fetch covered symbols from `ForecastTracker`
      — the as-shipped `get_covered_symbols()` queried a nonexistent `forecasts` table,
      referenced a nonexistent `self.readonly` attribute, and leaked the tracker's shared
      connection. Rewritten against the real `forecast_errors` table with a `try/except`
      dead-letter guard and a `window_days=7` recency filter. **Actually verified working** via
      `tests/test_forecast_tracker.py::TestGetCoveredSymbols` (real, unmocked SQLite).
- [x] Pass `forecast_symbols` to `build_sync_report`
      — done via `api/data_api.py::get_sync_report()`. **Correction: `data/portfolio_sync.py`
      was never modified and did not need to be** — `build_sync_report()` already accepted
      `forecast_symbols` from an earlier commit. That parameter had zero test coverage before
      this PR; added in `tests/test_portfolio_sync.py`, plus an end-to-end real-`ForecastTracker`
      regression test in `tests/test_data_api.py`.
- [x] Enforce deployability gate on option pilot endpoints — **all four**, not three
      - [x] `earnings_crush` — enforced in the original commit.
      - [x] `dispersion_trading` — enforced in the original commit.
      - [x] `zero_dte_engine` — **the original commit added the `override_deployability_gate`
            field to the request model but never added the enforcing check to the handler
            (`post_options_zero_dte_execute` called `execute_0dte_trade` unconditionally).
            Originally checked off as done; it was not. Fixed by an independent audit pass** —
            the handler now blocks by default exactly like the other two, proceeding only on
            `override_deployability_gate: true`.
      - [x] `vol_mispricing` — already enforced before this PR (pre-existing `MEASURED_FAIL` gate).
      - [x] Disclosed the resulting policy reversal in `CLAUDE.md`/`AGENTS.md`/
            `docs/signals/vol_mispricing.md`: these three strategies' gates used to be documented
            as "informational and never blocks"; that is no longer true for any of them.
- [x] Fix two pre-existing tests broken by the earnings_crush/dispersion enforcement landing
      without a matching test update (`tests/test_options_desk_deployability_runtime_gap.py`) —
      found and fixed by the audit alongside the zero_dte gap, not related to it.
- [x] Add `mock_exec.assert_not_called()`-style coverage proving the block actually prevents
      execution (not just that the response shape looks blocked), for all three
      `UNGATEABLE_DATA_GAP` endpoints.
- [x] Update broken tests (`tests/test_data_api.py` kwargs-tolerant mock lambdas — from the
      original commit; still correct)
- [x] Re-verify from scratch (independent adversarial pass): re-ran every touched test file fresh,
      re-derived the real DB schema/attribute names against the source instead of trusting the
      original diagnosis, and added coverage that checks actual call behavior (mock call counts,
      a real unmocked end-to-end path) rather than only response shape.
- [x] Fourth pass — ran `tests/test_pilots_paper_broker.py`, which no prior pass had run: found 8
      pre-existing tests (2 earnings_crush, 3 dispersion, 3 zero_dte) legitimately testing real
      execution/rejection behavior now broke against the default-blocked gate. Added
      `override_deployability_gate: true` to each, since the correct fix is opting the test in
      (mirroring what a real operator now must do), not loosening the gate.
- [x] Brought all three endpoints' blocked/success response shape into parity with
      `vol_mispricing`'s own precedent: added `gate_status` to the blocked response and
      `override_applied` to the success response for `earnings_crush`/`dispersion_trading`/
      `zero_dte_engine` — corrected 3 tests that had asserted `gate_status` absent.
- [x] Regenerated `docs/settings_field_census.json`/`.md` (`scripts/measure_settings_census.py
      --write`), stale after `api/pilots_api.py`'s edits and failing
      `tests/test_measure_settings_census.py`.
- [x] Full targeted suite green: 915 passed, 0 failed across every file this PR (and every audit
      pass) touches.

## Explicitly NOT done / out of scope
- `data/portfolio_sync.py` — confirmed unmodified; no code change was needed here.
- No change to `main.py`/`main_orchestrator.py`'s own universe-building logic — this PR is scoped
  to the Pilots API's sync-report surface only.
