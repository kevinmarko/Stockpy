# Audit Integrity Enforcement — Walkthrough (re-scoped)

## Re-scope: Component 1 dropped, superseded by PR #984

This branch's original PR (#987) implemented all 3 deferred components
(Daemon Automated Lifecycle, OFI Shield, FIX Venues). While that PR was in
flight, PR #984 — an independent concurrent session — merged its own,
more thorough fix for the identical Component 1 gap directly to `main`,
including per-step `try`/`except` resilience, a `CYCLE_SKIPPED` sentinel to
correctly distinguish a genuinely-skipped cycle from a real one (fixing the
same macro-gate-bypass bug this branch's own code-review pass had
independently found and fixed), and a `run_0dte` parameter to avoid
double-firing 0DTE exits between the daemon's fast timer loop and the full
lifecycle call.

Rather than hand-merging two differently-shaped implementations of the same
daemon-wiring logic on execution-critical code, this branch was reset onto
`main` (post-#984) and re-scoped to carry **only** Components 2 and 3 —
fully orthogonal to #984's file set (`execution/dynamic_circuit_breaker.py`,
`execution/fix_gateway.py`, `pilots/options_vpin.py`, `settings.py` are
untouched by #984) — plus one small enhancement layered onto #984's now-
canonical `execution/options_lifecycle.py`.

## 1. Flash Crash (OFI) Shield — Fail Closed Behavior (Complete)

**Goal**: Implement Constraint #3 (Fail closed) for the OFI Shield without
regressing the shield's existing behavior when real data IS present, and
without turning the shield into a permanent halt once enabled (the actual
gap this required careful handling for — see the "Post-review fixes"
section below for the two bugs the first cut of this shipped with and how
they were corrected).

**Changes**:
- `execution/dynamic_circuit_breaker.py::update_metrics`: the real
  flash-crash check (`check_flash_crash_shield`) runs unconditionally
  whenever both `ofi` and `vpin` are supplied — independent of
  `OFI_SHIELD_ENABLED` — so real, dangerous data always trips the shield.
- `settings.OFI_SHIELD_ENABLED` (default `False`) governs ONLY the
  fail-closed response to a missing `vpin` (the one signal the daemon's
  live updater, `maybe_update_circuit_breaker`, actually computes) — not
  `ofi`'s routine, architecturally-permanent absence.

## 2. FIX Venues Configuration — Fail Closed Behavior (Complete)

**Goal**: Implement Constraint #3 (Fail closed) for the FIX gateway if
venues drop or configuration is malformed, without breaking the gateway's
zero-config default for every existing deployment (the bug the first cut
shipped with — see below).

**Changes**:
- `MultiVenueAggregator` in `execution/fix_gateway.py` loads custom JSON
  from `settings.FIX_VENUES_CONFIG_PATH` only when
  `settings.FIX_MOCK_VENUES_ENABLED` is explicitly set `False`; the default
  (`True`) preserves the always-works hardcoded-mock-venue behavior for
  this fully-simulated (never touches real capital) gateway.
- `route_order()` fails closed (`status: "REJECTED"`) when `self.venues` is
  empty for any reason.

## 3. Position-existence pre-check for delta hedging (Complete, additive to #984)

`data/paper_account_store.py::PaperAccountStore.has_any_open_position()` —
a cheap existence check (no price resolution, unlike `get_open_positions()`)
— is wired into #984's canonical
`execution/options_lifecycle.py::run_automated_delta_hedge_cycle`: the live
SPY quote fetch is skipped entirely when the paper book holds no position
at all, since there's nothing to hedge. (Note: under #984's actual daemon
wiring this only runs once per full pipeline cycle, not on every fast
timer-loop wake as an earlier draft of this branch's own Component 1 work
assumed — so this is a minor efficiency addition on top of already-good
cadence design, not a fix for a live regression.)

## Post-review fixes (10-finding audit pass, applied before the re-scope)

The original 3-component diff was reviewed by 8 agents; all 10 findings
were independently verified (CONFIRMED) and fixed. The findings relevant to
what survived the re-scope (Components 2 & 3):

- **OFI shield inert by default**: `check_flash_crash_shield` had been
  gated behind `OFI_SHIELD_ENABLED` even for real, present data — fixed to
  run unconditionally on real data (see Component 1 above).
- **OFI shield permanent self-halt when enabled**: the fail-closed branch
  originally fired on `ofi is None OR vpin is None`; since the daemon's
  only live caller never supplies `ofi`, enabling the flag caused a
  permanent `SOFT_HALT` every tick. Fixed to fire only on a genuine `vpin`
  gap.
- **FIX gateway rejects every order by default**: `FIX_MOCK_VENUES_ENABLED`
  originally defaulted `False` with no shipped `output/fix_venues.json`,
  silently breaking the gateway out of the box. Flipped default to `True`.
- **`VPINResult.to_dict()` / `apply_defensive_spread_concession()` crash on
  `vpin=None`**: `calculate_vpin()`'s new `vpin=None` sentinel (for
  empty/degenerate trade data) wasn't handled by either function, raising
  `TypeError` on a live `GET /pilots/options/vpin/metrics` call for a
  thinly-traded symbol. Both fixed; `get_options_vpin_metrics()` now routes
  this case through the same honest `data_available: False` response as a
  fetch failure.
- **New settings unregistered**: `OFI_SHIELD_ENABLED`,
  `FIX_MOCK_VENUES_ENABLED`, `FIX_VENUES_CONFIG_PATH` registered in
  `env_io.py`'s `ALLOWED_KEYS` and (for `OFI_SHIELD_ENABLED`, a
  trading/safety-behavior flag) `settings_keysets.py`'s
  `SAFETY_CRITICAL_KEY_REASONS`.
- **FIX fail-closed code paths untested**: `tests/test_fix_gateway.py`'s
  file-wide autouse mock-venues fixture meant no test exercised the new
  JSON-loading or reject-on-empty-venues paths. Added 4 tests that
  explicitly override the fixture to exercise both.

Findings that were specific to this branch's now-dropped Component 1 work
(the daemon-lifecycle macro-gate-bypass bug, the executor-sharing/missing-
gate efficiency issue, the missing task-tracker artifact, the branch-naming
convention) are no longer part of this diff — the macro-gate bug was
independently fixed by #984, the executor-sharing concern doesn't apply to
#984's per-cycle (not per-timer-wake) cadence, and the task-tracker/branch-
naming items were resolved directly on this branch already.

## Validation

- `pytest tests/test_dynamic_circuit_breaker.py tests/test_fix_gateway.py tests/test_options_vpin.py tests/test_options_lifecycle.py tests/test_daemon_runtime.py tests/test_paper_account_store.py tests/test_options_paper_executor.py tests/test_measure_settings_census.py tests/test_settings_liveness.py` — see the companion task tracker for the exact pass count from the final re-scoped run.
- `ruff check . --select=F821,F822,F823,E9` — passes (matches CI's exact lint invocation).

## Open Integrity Gap (unchanged, not addressed by this branch)

`execution/options_lifecycle.py` (via `pilots.zero_dte_engine.manage_0dte_exits`)
wires and heavily depends on `zero_dte_engine`, which has zero
`STRATEGY_REGISTRY` entries and no backtest validation metrics. Previously
flagged in `AGENTS.md`; remains a critical open gap, unaffected by either
this branch or #984.
