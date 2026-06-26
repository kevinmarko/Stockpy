# GUI Operational Efficiency, UX & Architectural Integration — Implementation Plan

**Status:** Planning doc, ready for an implementing agent to pick up.
**Authored:** 2026-06-26
**Owner (current):** Claude Code session — handoff to next agent via the AGENT HANDOFF NOTES block at the bottom.

This plan covers four operator-facing improvements to the InvestYo Command Center GUI
(`gui/app.py` + `gui/panels.py`), plus the small architectural refactors they require.
Each section is independently mergeable; the recommended landing order is in
[§8 Sequencing](#8-sequencing-for-the-implementing-agent).

---

## 0. Scope summary (what changes, what doesn't)

| Area | New module | Modified module | Reuses existing |
|---|---|---|---|
| Launcher kill switch + Safe Mode | — | `gui/panels.py` | `execution.kill_switch.GlobalKillSwitch` |
| Preflight panel | `gui/preflight_runner.py` | `gui/panels.py` | `scripts/preflight_check.py` (subprocess `--json`) |
| Pipeline stage colour-coding | — | `gui/panels.py`, `gui/orchestrator_runner.py` (light) | `compute_stage_status()` |
| Symbol search (Live Inventory + Reports) | `gui/symbol_search.py` | `gui/panels.py` | existing dataframes |
| Persistent mode header | `gui/run_mode.py` | `gui/app.py`, `gui/panels.py` | `RunHandle.mode`, `strategy_registry.read_active_mode` |
| Strategy Health from Gravity report | `gui/strategy_health.py`, `validation/thresholds.py` | `gui/panels.py` (Safety tab), `validation/harness.py`, `Gravity AI Review Suite.py` | `Gravity AI Review Suite.py` JSON output |

No changes to `signals/`, `ml/`, `execution/risk_gate.py`, or the orchestrator pipeline math.
Everything is GUI-side except (a) ensuring the Gravity suite writes a stable JSON report
contract, and (b) extracting deployability thresholds into `validation/thresholds.py`
so the GUI and harness share one source of truth.

---

## 1. One-Click Kill-Switch & "Safe Mode" on the Launcher tab

**Goal:** operator can flip the kill switch and/or Safe Mode from the Launcher without leaving the tab.

### Definitions
- **Kill Switch** — existing file-backed `output/KILL_SWITCH` via
  `execution.kill_switch.GlobalKillSwitch`. Blocks order submission at the `OrderManager`
  level. Already toggled from the Strategy Matrix tab; we surface the same toggle on
  the Launcher.
- **Safe Mode** — *not* a new persisted concept; it's a UX bundle that, when ON, sets
  both `DRY_RUN=true` and activates the kill switch. Reuses `gui/env_io.write_setting`
  (already allowlists `DRY_RUN`) and `GlobalKillSwitch.activate()`. Effect: next launch
  is dry-run AND any order path is blocked by the file sentinel. The bundle is read by
  deriving state, not by storing a new flag.

### Files
- `gui/panels.py::_render_launcher_safety_controls()` — new private helper, called at
  the very top of `render_launcher()` (above the existing buttons).
  - Two columns:
    - **Kill Switch** card: large 🛑/🟢 status badge + toggle button. Reads
      `GlobalKillSwitch().is_active()`; writes via
      `.activate("Toggled from Launcher tab")` / `.deactivate()`. Confirm dialog for
      activation (matches Strategy Matrix tab pattern).
    - **Safe Mode** card: derived ON iff `kill_switch_active AND settings.DRY_RUN`. Toggle:
      - ON → `GlobalKillSwitch().activate("Safe Mode")` + `write_setting("DRY_RUN", "true")`.
      - OFF → `GlobalKillSwitch().deactivate()` + `write_setting("DRY_RUN", "false")`.
      - Caption: "Safe Mode blocks all live orders and forces dry-run on next launch."
  - Disable the Launch Pipeline button (`disabled=kill_switch_active`) with tooltip:
    "Kill switch active — disable to launch a live broker path. Advisory refresh is still allowed."
    Refresh Data (Advisory) stays enabled — it never touches the broker.

### Constraints honored
- No new env var (Safe Mode is derived).
- CONSTRAINT #3: `gui/env_io.write_setting` already allowlists `DRY_RUN`.
- CONSTRAINT #5: subprocess unchanged; no daemon.

### Tests — `tests/test_launcher_safety_controls.py` (new)
- Pure `safe_mode_state(kill_active, dry_run) -> "ON"|"OFF"` truth table.
- Monkeypatch `GlobalKillSwitch.activate`/`deactivate` and `gui.env_io.write_setting`;
  assert the bundle calls both atomically and that Launch is disabled when the kill
  switch is active.
- Cover: kill on + DRY_RUN on → Safe Mode ON; either off → OFF.

### Gravity
Extend `step_44_safety_analytics_control_audit` (or add
`step_47_launcher_safety_bundle_audit`) to verify (a) Launcher imports
`GlobalKillSwitch`, (b) Safe Mode toggle writes BOTH `DRY_RUN` and the kill-switch
sentinel atomically (AST-grep for both calls in the toggle handler).

---

## 2. Preflight Check on-demand panel

**Goal:** run `scripts/preflight_check.py --json` from the GUI and render the 12 checks.

### Decision — subprocess, not in-process
The script is already designed as a CLI gate with `--json` output and per-check
exception swallowing. Spawning it preserves isolation (a side-effecting check can't
corrupt the running Streamlit process) and matches the on-demand pattern already used
for `orchestrator_runner`.

### Files
- `gui/preflight_runner.py` (new, ~80 lines):
  - `PreflightCheck` frozen dataclass: `name: str, passed: bool, reason: str, warning: bool`.
  - `PreflightReport`: `checks: list[PreflightCheck]`, `all_passed: bool`,
    `ran_at: datetime`, `duration_seconds: float`, `raw_output: str`.
  - `run_preflight(skip: list[str] | None = None, timeout: float = 30.0) -> PreflightReport`
    — `subprocess.run([sys.executable, "scripts/preflight_check.py", "--json", *skip_args], …)`.
    Parses stdout JSON. On timeout / non-JSON, returns a single synthetic
    `PreflightCheck(name="runner", passed=False, reason=<error>, warning=False)`
    (CONSTRAINT #4 — never fabricate a passing result).
- `gui/panels.py::_render_preflight_panel()` — new helper inserted into
  `render_launcher()` between safety controls and the Launch buttons.
  - "🛫 Preflight Check" expander (defaults expanded on first render if any required
    env var is missing).
  - "Run preflight" button + last-run timestamp.
  - Results table: name | status (✅/⚠️/❌) | reason. Use the existing 3-state colour palette.
  - Top-line metric strip: Passed / Warnings / Failures.
  - Stores last `PreflightReport` in `st.session_state["preflight_report"]` so it
    survives tab switches.

### Reuses existing
- `scripts/preflight_check.py` (no source change).
- `gui.orchestrator_runner.validate_required_env` already gates the Launcher
  pre-launch — the preflight panel is the deeper, on-demand sibling.

### Tests — `tests/test_preflight_runner.py` (new, ~10 tests)
- `run_preflight` parses real JSON shape from `preflight_check.py --json`
  (monkeypatch `subprocess.run` with canned stdout).
- Timeout / non-zero exit produces a failed `PreflightReport` (no fabricated success).
- Skip-list is passed through as `--skip` args.
- Empty stdout → synthetic failure.

### Gravity
New `step_48_preflight_runner_audit` — verifies (a) `run_preflight` exists and
returns a typed report, (b) timeout path returns `all_passed=False`,
(c) `gui/panels.py::_render_preflight_panel` is wired into `render_launcher`.

---

## 3. Color-coded pipeline stages

**Goal:** four pipeline stages (Data Acquisition, Processing, Forecasting, Execution)
render with status colour — green=success, yellow=active, red=error, grey=pending/skipped.

### Reality check
`gui/orchestrator_runner.compute_stage_status(handle)` already exists and returns
coarse status strings derived from log markers + heartbeat freshness +
`state_snapshot.json` mtime. We extend it to return a `StageStatus` enum so rendering
can drive colour.

### Files
- `gui/orchestrator_runner.py`:
  - New `class StageStatus(str, Enum)`: `SUCCESS`, `ACTIVE`, `ERROR`, `PENDING`, `SKIPPED`.
  - `compute_stage_status()` return type becomes `dict[str, StageStatus]` (was
    `dict[str, str]`). Map the existing strings:
    `"complete" → SUCCESS`, `"running" → ACTIVE`, `"error" → ERROR`,
    `"pending" → PENDING`. Add `SKIPPED` for "Execution" when `settings.DRY_RUN=true`.
  - **Backwards compatibility:** the enum subclasses `str`, so legacy callers doing
    string comparison still work. Choose enum values
    `"success" / "active" / "error" / "pending" / "skipped"` — note this renames
    `"complete" → "success"` and `"running" → "active"`. Audit `gui/panels.py` for
    callers; only `render_launcher` consumes it. Update there.
- `gui/panels.py::_render_pipeline_stages(status_map)`:
  - Renders four cards (one per stage) using `st.columns(4)`.
  - Colour mapping (Streamlit-native, no custom CSS injection): `st.success` /
    `st.warning` / `st.error` / `st.info` and a Markdown grey block for SKIPPED.
    Each card shows the stage name + a Unicode icon (`✅ ⏳ ❌ ⏸ ⏭`) + the raw status string.
  - Auto-refresh tied to the existing 5 s auto-refresh checkbox.

### Tests — `tests/test_pipeline_stage_status.py` (new)
- `StageStatus` enum membership and string equivalence
  (`StageStatus.SUCCESS == "success"`).
- `compute_stage_status` with a synthetic log + heartbeat + snapshot fixture set
  (tmp_path) returns the expected map.
- `DRY_RUN=true` forces Execution → SKIPPED.

### Gravity
Extend `step_41_launcher_telemetry_audit` to assert (a) `StageStatus` enum exists and
is `str`-based, (b) the four stages are present in `compute_stage_status`'s output,
(c) panel renders four cards.

---

## 4. Symbol search on Live Inventory + Reports tabs

**Goal:** quick filter as the watchlist grows.

### Files
- `gui/panels.py::render_live_inventory`:
  - Add `st.text_input("🔍 Search symbol", key="live_inv_search").strip().upper()`
    directly above the table.
  - Filter the `SyncReport`-derived DataFrame by substring match on the symbol column
    AND on the watchlist-memberships column (so "SPY" finds it whether it's a holding
    or in a list named "Defensive").
  - Show "showing N of M symbols" caption.
- `gui/panels.py::render_report_viewer`:
  - Add the same input above the holdings / recommendation table.
  - Wire the existing "🔬 Drill down by symbol" expander to default to whatever's in
    the search box (handy when the search matches exactly one ticker).
  - Filter survives tab switches via `st.session_state["report_search"]`.

### Pure helper for tests
`gui/symbol_search.py` — `filter_by_symbol(df, query, symbol_col="symbol", extra_cols=()) -> pd.DataFrame`.
Case-insensitive, whitespace-tolerant. Empty query returns the original frame.

### Tests — `tests/test_symbol_search.py` (~6 tests)
- Empty query → identity.
- Exact match.
- Substring match.
- Case insensitivity.
- Multi-column search (symbol OR watchlist name).
- Non-existent column raises `KeyError` (fail-fast).

### Gravity
No new audit step needed (pure UI helper); covered by tests.

---

## 5. Dual-Mode header (Orchestrator vs Advisory) + Execution mode

**Goal:** always-visible header on every tab showing both:
1. **Run Mode** — `Orchestrator` / `Advisory` / `Idle` (which subprocess was last
   launched / is running).
2. **Execution Mode** — `SIMULATION` / `PAPER` / `LIVE` (from
   `gui/strategy_registry.read_active_mode()`).

### Files
- `gui/run_mode.py` (new, ~50 lines):
  - `RunModeState` frozen dataclass:
    `process: Literal["orchestrator","advisory","idle"]`, `since: datetime | None`,
    `log_path: Path | None`.
  - `read_active_run_mode() -> RunModeState` — looks at
    `st.session_state.get("active_handle")` (where `render_launcher` already stashes
    the `RunHandle`); if process is still alive (`handle.poll() is None`), report it;
    else "idle". Also fall back to log-file mtime on `output/gui_run.log` /
    `output/gui_advisory.log` whichever is fresher within the last 5 minutes (handles
    a Streamlit reload mid-run).
- `gui/app.py`:
  - Above `st.tabs(...)`, render a single-row header (`st.columns(3)`) with: process
    badge, execution-mode badge, and quick "Kill switch" status (read-only mirror;
    the actionable toggle lives on Launcher).
  - Process badge colours: Orchestrator = blue, Advisory = teal, Idle = grey.
  - Execution-mode badge colours: SIMULATION = grey, PAPER = blue, LIVE = red.

### Reuses
- `RunHandle.mode` (already exists).
- `strategy_registry.read_active_mode` (already exists).

### Tests — `tests/test_run_mode.py` (~6 tests)
- `read_active_run_mode` with no `session_state` → idle.
- With a live `RunHandle` (poll returns `None`) → reports its mode.
- With a finished handle (poll returns `0`) → idle.
- Log-mtime fallback when `session_state` is empty but a log was touched 30 s ago.

### Gravity
New `step_49_dual_mode_header_audit` — verifies `gui/run_mode.py` exists, exposes
`read_active_run_mode`, and is imported by `gui/app.py`.

---

## 6. Strategy Health view from Gravity report

**Goal:** parse the Gravity Verification Report JSON and show, per strategy, whether
it still meets the four deployability gates (PBO < 0.5, DSR > 0.95, net Sharpe > 0.5,
MaxDD < 30%) — plus the options-selling stress gate when applicable.

### Reality check (must be verified first)
- `Gravity AI Review Suite.py` produces a verification report. Need to confirm exact
  filename and JSON schema before wiring. The CLAUDE.md references "trailing JSON"
  parsed by the Gravity Audit panel; verify whether it persists
  `Gravity_Verification_Report.json` to disk or only streams to stdout. **If it
  doesn't already persist, add a step to write `output/gravity_verification_report.json`
  (atomic write-then-rename) as part of this task** — that's the contract the
  Strategy Health view depends on. This needs a one-line addition to the suite's
  main runner.
- Confirm the per-strategy report shape includes
  `{strategy_id, pbo, dsr, net_sharpe, max_drawdown, is_options_selling, stress_test_passed, deployable, last_audited_at}`.
  If the current suite outputs a different shape, add an adapter; do not change
  `validation/harness.py` output.

### Files
- `gui/strategy_health.py` (new, ~120 lines):
  - `DeployabilityGate` frozen dataclass: `name`, `value: float | None`,
    `threshold: float`, `comparator: Literal["<","<=",">",">="]`, `passed: bool`.
    `None` values render as ❓ and `passed=False` (CONSTRAINT #4).
  - `StrategyHealth` frozen dataclass: `strategy_id`,
    `last_audited_at: datetime | None`, `is_options_selling: bool`,
    `gates: list[DeployabilityGate]`, `stress_passed: bool | None`, `deployable: bool`.
  - `read_gravity_report(path=Path("output/gravity_verification_report.json")) -> list[StrategyHealth]`
    — tolerant JSON read (missing file → `[]`, never raises; corrupt JSON → empty
    list + WARNING log; per-strategy parse failure logged at WARNING and skipped —
    matches the dead-letter pattern).
  - Pure helper `evaluate_gate(value, threshold, comparator)` for unit testing.
- `gui/panels.py::_render_strategy_health()` — new helper inserted into
  `render_gravity_audit()` (the Safety tab) between the existing Circuit Breakers
  section and the Gravity audit launcher. Renders:
  - KPI strip: # strategies, # deployable, # failing, oldest audit age.
  - Per-strategy table: strategy_id | status badge (✅/❌) | each gate as a small
    badge (value, target, ✅/❌) | last audited (relative time) | options-selling
    stress badge (when applicable).
  - "Audit age" warning at > 30 days (config:
    `settings.STRATEGY_HEALTH_STALE_DAYS=30`).
  - Empty state: "No Gravity verification report found. Run the Gravity AI Review
    Suite from the Safety tab to populate this view."

### Reuses existing
- The four deployability thresholds are already canonical in
  `validation/harness.py`'s `ValidationReport.deployable` — `gui/strategy_health.py`
  MUST read those thresholds from a single shared module so they cannot drift.
  Plan: add `validation/thresholds.py` (~15 lines) exporting `PBO_MAX=0.5`,
  `DSR_MIN=0.95`, `NET_SHARPE_MIN=0.5`, `MAX_DRAWDOWN_MAX=0.30`,
  `STRESS_MAX_DRAWDOWN=0.50`. Refactor `validation/harness.py` and the new
  `gui/strategy_health.py` to both import from it. Small refactor — explicitly within
  scope per CONSTRAINT #7.

### Tests — `tests/test_strategy_health.py` (~12 tests)
- `evaluate_gate` truth table (all four comparators, value at/below/above threshold,
  `None` value).
- `read_gravity_report` happy path with a canned JSON fixture (3 strategies:
  1 deployable, 1 failing PBO, 1 options-selling failing stress).
- Missing file → `[]`.
- Corrupt JSON → `[]` + `logger.warning` called.
- Partial strategy entry (missing `dsr`) → strategy still parsed, gate marked
  `value=None, passed=False`.
- Stale audit threshold (`last_audited_at` > 30 days ago) reflected in the helper.

### Gravity
New `step_50_strategy_health_audit` — verifies (a) `gui/strategy_health.py` exists,
(b) `validation/thresholds.py` exists and `validation/harness.py` imports from it,
(c) Gravity suite writes `output/gravity_verification_report.json` (atomic),
(d) `read_gravity_report` returns `[]` on missing file (no fabricated success).

---

## 7. Documentation updates

### `CLAUDE.md` additions (one section per area)
- "Launcher safety controls" — Safe Mode is a derived bundle of kill switch +
  `DRY_RUN`; not a new env var.
- "Preflight panel" — subprocess-based, no in-process import of
  `scripts/preflight_check.py`.
- "Pipeline stage status" — `StageStatus` enum is `str`-subclass (legacy callers
  safe); execution stage → SKIPPED under `DRY_RUN`.
- "Symbol search" — `gui/symbol_search.filter_by_symbol` is the single entry point;
  both tabs reuse it.
- "Dual-Mode header" — `gui/run_mode.py` owns process detection; `RunHandle.mode`
  remains the authoritative tag.
- "Strategy Health + `validation/thresholds.py`" — single source of truth for the
  four deployability thresholds; Gravity report path is
  `output/gravity_verification_report.json`.

### `GEMINI.md`
Same additions; the project already keeps both in sync.

### `docs/HOW_TO_GUIDE.md`
New "Launching from the GUI" subsection covering preflight → safe-mode → launch workflow.

### `docs/RUNBOOK.md`
New incident-response entry: "Kill switch toggled mid-run from Launcher" — verify
orders blocked at `OrderManager`, check `output/risk_gate_blocks.jsonl`, deactivate
when remediation complete.

---

## 8. Sequencing for the implementing agent

Recommended order (each step independently mergeable):

1. **`validation/thresholds.py`** + refactor `validation/harness.py` to consume it.
   (Pre-req for Strategy Health.)
2. **Gravity suite** writes `output/gravity_verification_report.json`.
   (Pre-req for Strategy Health view.)
3. **`StageStatus` enum** in `gui/orchestrator_runner.py` + Launcher rendering.
   (Foundational; touches one shared helper.)
4. **`gui/preflight_runner.py`** + Launcher panel.
5. **Launcher safety controls** (kill switch + Safe Mode).
6. **`gui/run_mode.py`** + persistent header.
7. **`gui/symbol_search.py`** + Live Inventory / Reports wiring.
8. **`gui/strategy_health.py`** + Safety tab integration.
9. **Docs sync** (CLAUDE.md, GEMINI.md, HOW_TO_GUIDE.md, RUNBOOK.md).
10. **Gravity steps 47–50** added at the end so each audit reflects final code.

---

## 9. New dependencies / env vars

**`requirements.txt`** — none. Everything uses stdlib + existing `streamlit`,
`pandas`, `psutil`.

**`.env.example`** — none. Safe Mode is derived; thresholds live in
`validation/thresholds.py` (code, not env).

Optional, deferred until a user asks: `STRATEGY_HEALTH_STALE_DAYS` (default 30) —
only add if the value needs to be user-tunable; otherwise hard-code in
`gui/strategy_health.py`.

---

## 10. Pytest verification commands

```bash
.venv/bin/pytest tests/test_launcher_safety_controls.py -v
.venv/bin/pytest tests/test_preflight_runner.py -v
.venv/bin/pytest tests/test_pipeline_stage_status.py -v
.venv/bin/pytest tests/test_symbol_search.py -v
.venv/bin/pytest tests/test_run_mode.py -v
.venv/bin/pytest tests/test_strategy_health.py -v
.venv/bin/pytest tests/ -k "gui or launcher or preflight or stage or health or run_mode or symbol_search" -v
.venv/bin/pytest                              # full suite — must stay green
.venv/bin/python "Gravity AI Review Suite.py" # all audit steps, including 47–50, must pass
```

Manual smoke (`./launch_gui.command`):
1. Open Launcher → toggle kill switch ON → confirm "Launch Pipeline" disables;
   advisory stays enabled.
2. Toggle Safe Mode ON → kill switch sentinel exists AND `.env` shows `DRY_RUN=true`.
3. "Run preflight" button → 12 checks render with colour.
4. Launch advisory → header shows "Advisory" + execution-mode badge; pipeline stages
   colour-code green as they complete.
5. Live Inventory tab → search "SPY" → only matching rows.
6. Safety tab → Strategy Health table renders with last audit time and per-gate badges.

---

## 11. Blockers / things to verify before coding

1. **Gravity report path/schema** — must inspect `Gravity AI Review Suite.py` to
   confirm whether `output/gravity_verification_report.json` already exists. If not,
   step 2 of the sequence adds it. The implementing agent should
   `grep -n "Verification_Report\|verification_report" "Gravity AI Review Suite.py"`
   first.
2. **`compute_stage_status` callers** — confirm via
   `grep -rn compute_stage_status gui/` before changing the return-value strings.
   As of CLAUDE.md only `render_launcher` consumes it, but verify.
3. **`RunHandle.mode` field** — already documented as carrying
   `"orchestrator"|"advisory"`; `"retry"` is already there for symbol retry. No
   change needed; just consumed by `run_mode.py`.

If any of these don't match expectations, stop and ask before proceeding rather than guess.

---

## AGENT HANDOFF NOTES

- **New modules** to be created:
  - `gui/preflight_runner.py` — subprocess wrapper around
    `scripts/preflight_check.py --json`; returns `PreflightReport`
    (CONSTRAINT #4: timeout → failure, never fabricated success).
  - `gui/symbol_search.py` — pure `filter_by_symbol(df, query, …)` helper shared by
    Live Inventory + Reports.
  - `gui/run_mode.py` — `RunModeState` + `read_active_run_mode()` for the persistent
    header.
  - `gui/strategy_health.py` — parses `output/gravity_verification_report.json` →
    `StrategyHealth` records using shared thresholds.
  - `validation/thresholds.py` — single source of truth for
    PBO / DSR / Sharpe / MaxDD / stress thresholds; both `validation/harness.py` AND
    `gui/strategy_health.py` import from it.

- **New conventions / invariants**:
  - **Safe Mode is derived**, not stored — ON iff kill switch is active AND
    `DRY_RUN=true`. Toggling it writes both atomically via `GlobalKillSwitch` +
    `gui/env_io.write_setting`.
  - **Deployability thresholds live in `validation/thresholds.py` only.** Do not
    hard-code them anywhere else.
  - **`gravity_verification_report.json`** is now a published artifact at
    `output/gravity_verification_report.json` (atomic write-then-rename). Strategy
    Health view reads it tolerantly (missing → empty list, corrupt → empty list,
    never raises).
  - **`StageStatus` enum** is `str`-subclassed for legacy compatibility; values are
    `"success"|"active"|"error"|"pending"|"skipped"`. `DRY_RUN` forces Execution →
    SKIPPED.
  - **Launcher disables "Launch Pipeline" while kill switch is active**, but keeps
    "Refresh Data (Advisory)" enabled — advisory never touches the broker.

- **New env vars**: none.

- **New audit steps in `Gravity AI Review Suite.py`**:
  - `step_47_launcher_safety_bundle_audit` — Safe Mode toggles kill switch +
    `DRY_RUN` atomically.
  - `step_48_preflight_runner_audit` — `run_preflight` typed report; timeout path
    returns `all_passed=False`.
  - `step_49_dual_mode_header_audit` — `gui/run_mode.py` exists and is imported by
    `gui/app.py`.
  - `step_50_strategy_health_audit` — `validation/thresholds.py` is the single
    threshold source; Gravity report path tolerates missing file.
  - `step_41_launcher_telemetry_audit` is **extended** to also verify `StageStatus`
    enum + four-stage map.
  - `step_44_safety_analytics_control_audit` is **extended** to also verify the
    Launcher-tab safety controls (not just the Strategy Matrix tab).

- **Wiring that differs from current codebase comments**:
  - Kill switch UI now lives in TWO places (Strategy Matrix and Launcher) — both
    read/write the same `GlobalKillSwitch`; no source-of-truth split.
  - Preflight is now reachable from the GUI (not only the CLI). The CLI form remains
    the canonical script.
  - `compute_stage_status` return value changes from raw strings to a `StageStatus`
    enum (still string-compatible).

- **Skipped intentionally**: no new package directories, no new external deps, no
  `.env.example` additions, no changes to signal / ML / validation math.
