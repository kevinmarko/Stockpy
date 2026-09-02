# Goal Description

This branch originally implemented 3 deferred components: the Daemon
Automated Lifecycle gap, the OFI Shield (Flash Crash Shield), and FIX Venues
configuration. An 8-agent code review of the original diff found and fixed
10 findings across all three components.

## Re-scope note (2026-09)

While this branch's PR (#987) was in flight, an independent concurrent
session's PR #984 ("Phase 7: Dynamic Position Lifecycle & SPY Delta Hedging
- Daemon Gap Fix") merged to `main` with its own, more thorough fix for the
exact same Component 1 (Daemon Automated Lifecycle) gap — including an
independent fix for the same macro-gate-bypass bug this branch's own review
pass had found and fixed. Rather than hand-merging two structurally
different implementations of the same daemon-wiring logic on
execution-critical code, this branch was re-scoped: `main`'s (#984's)
version of `execution/options_lifecycle.py` / `desktop/daemon_runtime.py` /
`main.py` / `main_orchestrator.py` was accepted as-is, and this branch now
carries only:

- **Component 2 (OFI Shield)** and **Component 3 (FIX Venues)** — both
  untouched by #984, fully additive.
- A `PaperAccountStore.has_any_open_position()` cheap pre-check, layered
  onto #984's now-canonical `execution/options_lifecycle.py::
  run_automated_delta_hedge_cycle` (see the walkthrough for detail).

## Proposed Changes

### Component 2: OFI Shield (Flash Crash Shield)

`execution/dynamic_circuit_breaker.py` gains `settings.OFI_SHIELD_ENABLED`
(default `False`): the real flash-crash check (`check_flash_crash_shield`)
runs unconditionally whenever both `ofi` and `vpin` are supplied, regardless
of the flag; the flag only controls whether a missing `vpin` (the signal
`maybe_update_circuit_breaker` actually computes) fails closed to
`SOFT_HALT`. Deliberately does NOT fail closed on `ofi`'s absence alone —
OFI is architecturally unwired platform-wide, so that would make the flag
permanently `SOFT_HALT` every tick once enabled.

### Component 3: FIX Venues Configuration

`execution/fix_gateway.py::MultiVenueAggregator` loads venues from
`settings.FIX_VENUES_CONFIG_PATH` (a JSON file) instead of always
hardcoding mock venues, gated by `settings.FIX_MOCK_VENUES_ENABLED`
(default `True`, preserving the zero-config "just works" behavior for this
fully-simulated gateway). `route_order()` fails closed (`status:
"REJECTED"`) when no venues resolve.

## Verification Plan

### Automated Tests
- `pytest tests/test_dynamic_circuit_breaker.py tests/test_fix_gateway.py tests/test_options_vpin.py tests/test_options_lifecycle.py tests/test_daemon_runtime.py tests/test_paper_account_store.py tests/test_options_paper_executor.py tests/test_measure_settings_census.py tests/test_settings_liveness.py`
- `ruff check . --select=F821,F822,F823,E9` (matches CI's exact lint invocation)
