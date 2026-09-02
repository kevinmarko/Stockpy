# Audit Integrity Enforcement — Task Tracker

Companion tracker for `audit_integrity_enforcement_framework_implementation_plan.md`
and `audit_integrity_enforcement_framework_walkthrough.md`.

## Component tasks (re-scoped 2026-09 — see walkthrough for why)

| # | Task | Status |
|---|------|--------|
| 1 | Daemon Automated Lifecycle (`execution/options_lifecycle.py` wiring) | **Dropped from this branch** — superseded by PR #984's independent, more thorough merge of the same fix (including its own fix for the same macro-gate-bypass bug). `main`'s version accepted as-is. |
| 2 | OFI/Flash-Crash Shield fail-closed behavior (`execution/dynamic_circuit_breaker.py`) | Done — including the post-review corrections (shield no longer inert by default; no longer permanently self-halts when enabled). |
| 3 | FIX venue configuration fail-closed behavior (`execution/fix_gateway.py`) | Done — including the post-review correction (`FIX_MOCK_VENUES_ENABLED` defaults `True`, preserving zero-config behavior). |
| 4 | `PaperAccountStore.has_any_open_position()` pre-check, layered onto #984's `run_automated_delta_hedge_cycle` | Done (additive enhancement, not a regression fix — see walkthrough for the cadence-context nuance). |

## Original 10-finding audit pass (2026-09)

| # | Finding | Status |
|---|---------|--------|
| 1 | Flash-crash shield inert by default | Fixed, survives re-scope |
| 2 | OFI shield permanent self-halt when enabled | Fixed, survives re-scope |
| 3 | FIX gateway rejects all orders by default | Fixed, survives re-scope |
| 4 | `macro_dto=None` bypasses VIX/CREDIT-EVENT gate | Fixed on this branch pre-re-scope; **independently also fixed by #984** — no longer this branch's concern post-re-scope |
| 5 | `VPINResult.to_dict()` / `apply_defensive_spread_concession()` crash on `vpin=None` | Fixed, survives re-scope |
| 6 | New settings unregistered in census/allowlists | Fixed, survives re-scope |
| 7 | FIX fail-closed code paths untested | Fixed, survives re-scope |
| 8 | Daemon fast-cadence executor churn + missing gate | Fixed on this branch pre-re-scope; **not applicable to #984's design** (delta hedging isn't wired into the fast timer loop there, only the per-cycle path) — dropped with Component 1 |
| 9 | SPY quote fetched every wake, no position check | Fixed on this branch pre-re-scope; re-applied post-re-scope as an additive optimization on top of #984's `execution/options_lifecycle.py` (see walkthrough item 3) |
| 10 | Missing task-tracker artifact + branch not kebab-case | Fixed (this file; branch renamed to `audit-integrity-enforcement-framework`) |

## Post-merge CI fixes (same branch, before merge)

- `main_orchestrator.py`: `F821 Undefined name 'RunContext'` ruff failure —
  fixed via a `TYPE_CHECKING`-guarded top-level import.
- `docs/settings_field_census.json/.md`, `docs/settings_liveness.json`:
  regenerated multiple times to stay fresh as `main` moved quickly
  (concurrent PRs landing every few minutes) during this PR's CI runs.
