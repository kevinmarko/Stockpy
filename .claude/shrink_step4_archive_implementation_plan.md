# Shrink-in-place step 4: archive to `legacy/` (implementation plan)

Follows step 3 (options desk decoupled, PRs #1056–#1064). Operator decisions already made:
- Archive (`git mv` to `legacy/`, not delete): the options desk, 0DTE, ETF volatility transmission, the Google Sheet publisher and Follow-a-Pilot.
- Keep: agentic Robinhood trading, the MCP server, forecasting, and equity paper trading.

Inventory: origin/main @ 96f81c44 (AST import graph of 1,037 modules).

## Traps this plan is built around
1. **`credentials.json` doubles as the real-data switch.** `pipeline/production_steps.py:132` and `desktop/daemon_runtime.py:431` pick `DataEngine` over `MockDataEngine` via `os.path.exists("credentials.json")`. Retiring Sheets without replacing this gate silently runs the daemon on fabricated data. **Fix the gate first, in 4e.**
2. **`execution/compose.py:885`** imports `FOLLOW_MIN_CONVICTION` inside the outer `try`. An ImportError returns `None`, and the execution queue is silently never written. Replace it with the literal `0.0` before anything in Follow moves (4c).
3. **Portfolio gross cap shares a `try` with the ETF covariance build** (`production_steps.py:~2891-2921`). A dangling ETF reference would skip `MAX_PORTFOLIO_GROSS` entirely. Keep `apply_portfolio_gross_cap` unconditional (4d).
4. **Ordering traps:**
   - Edit `StateSnapshotStep`'s payload columns (`:3273`) before removing `True_IVR`/`Option Strategy` from `COLUMN_SCHEMA`.
   - Remove the ETF NaN pre-fill only in the same PR as the schema trim, so Pandera doesn't fail.
   - Remove setting readers before the fields (4f last).
5. **MCP `validate_order_compliance`** returns "unavailable" for everything (including the kept Kelly-cap check) if the VRP import fails. Drop its VRP half before the signal moves (4a).
6. **Autouse `conftest.py` fixtures:**
   - `_isolate_execution_audit_db_in_tests` imports the store unconditionally. Delete it in the same PR as the move (4b).
   - `_stub_paper_marking_network_in_tests` must track the paper-store option-marking code.

## PRs (in order; each merged with the suite green before dependents start)

### 4a · Options unwire (edits only, no moves)
- **MCP tools:**
  - `get_options_directive`, `analyze_options_chain` and `scan_0dte_signals` become retired-message stubs, so they never raise.
  - `validate_order_compliance` drops its VRP half and keeps the Kelly-cap check.
- **Paper store:** `data/paper_option_marks.py` becomes self-contained. The OCC parser and Black-Scholes price move in from `pilots/options_risk`, so any legacy option position is still marked honestly. `execution/fmp_paper_broker.py` uses that parser.
- **`pilots/paper_broker.py`:** delete the options helpers (`get_portfolio_greeks`, the strategy-options candidate/execute helpers, `manage_position_exits`, `execute_roll`, `execute_paper_order`). Only tests call them.
- **`scripts/export_notebooklm.py`:** drop the Greeks half of the portfolio section.
- **`validation/harness.py`:** remove the options-validation CLI path.
- **`scripts/refresh_validations.py`:** remove the options, copula, earnings-crush, dispersion, 0DTE and gamma entries and adapters. Regenerate `cli_introspect/command_manifest.json`.
- **`ml/forecast_backfill.py`:** remove the VRP-proxy branch.
- **`llm/__init__.py`:** remove the research-copilot names.
- **`broker_live_execution_mcp.py`:** remove the multi-broker branch.
- **Gravity:** remove the IVR/VRP and options-matrix audits.
- **`pilots/catalog.py`:** remove the 10 options Pilots and `OPTIONS_DIRECTIVE_STRATEGY_TO_PILOT_ID`. Keep the report-card legacy aliases so historical paper trades still attribute.

### 4b · Options archive (moves)
- `git mv` the ~50 orphan and cluster modules (list in the inventory) and their ~54 test files to `legacy/`, preserving paths.
- Delete the execution-audit autouse fixture.
- Move `test_soft_halt_alert_dispatch` to `tests/test_kill_switch.py`.
- Edit the ~15 mixed tests. The GARCH equivalence tests pin their golden numbers instead of importing the old engine.
- `SignalContext.options_flow_sentiment` goes.
- Import smoke check: with the moved modules blocked via `sys.modules[...] = None`, `import main, main_orchestrator, investyo_mcp_server, broker_live_execution_mcp, api.pilots_api, api.data_api` all succeed.

### 4c · Follow-a-Pilot
- `compose.py:885` becomes literal `0.0` **first commit**, with a test that `compose_and_emit` writes a queue with no follows module importable.
- **Remove:**
  - the `/follows` and `/pilots/{id}/follow` routes, and the `aum_proxy`/`followers_proxy` fields
  - the 5 MCP tools, plus the `follow-result` widget
  - the export_notebooklm follow sections
  - Gravity step_92
  - the webapp FollowModal and the follow buttons/sections in Comparison, PilotDetail, Portfolio and SettingsModules
- Move `pilots/mirror.py`, the follows store and `pilots/portfolio_attribution.py` (+ tests) to `legacy/`.
- Keep `FOLLOW_API_TOKEN`: it is the general command token.

### 4d · ETF volatility transmission
- Unwire the pipeline (`production_steps` measurement, derate and covariance), keeping the plain gross cap unconditional.
- **Remove:**
  - the snapshot keys (`main_orchestrator.py`)
  - `pilots/observability` ETF section
  - `shared/help_content` entries
  - `/settings/etf-transmission`
  - the webapp settings screen and Observability section
- Move `risk/etf_transmission.py` and `data/etf_holdings.py` (+ 10 tests) to `legacy/`. `etf_transmission_multiplier` in `size_position` stays at its 1.0 default until 4f.

### 4e · Google Sheet publisher
- **First:** replace the `credentials.json` data-engine gate with an explicit check (`settings.FRED_API_KEY` present), with a test.
- **Then:**
  - remove `main.py`'s Sheets sink and the Sheet2 ticker fallback
  - drop `pipeline/steps` `SHEET_NAME`
  - move `reporting/sheet_publisher.py` (+ tests)
  - drop gspread/gspread-dataframe from requirements (keep google-auth if anything else uses it)

### 4f · Settings and schema trim
- Retire ~60 fields, together with their entries in `ALLOWED_KEYS`/`SECRET_KEYS`/`DANGEROUS_KEYS`, `feature_flags`, `settings_domains`, the "Options Desk Automation" group in `_TUNABLE_GROUPS`, `mock.ts` and `help_content`.
- Trim the 8 dead `COLUMN_SCHEMA` columns (`True_IVR`, `VRP`, `Realized_Vol_Rank`, `Option Strategy`, 4× ETF): payload cols first, then `_TREND_VOL_COLUMN_MAP`, parity tests and the webapp.
- Remove `etf_transmission_multiplier` from `size_position`.
- Regenerate `docs/settings_liveness.json` and the census.
- **Keep:** `OPTIONS_RISK_FREE_RATE` and `PAPER_OPTION_MARK_CACHE_SECONDS` (paper option marking), `DAILY_LOSS_LIMIT_PCT`, `PILOTS_TOP_N`, `FOLLOW_API_TOKEN`.

## Parallelism
- 4a first (it touches the MCP server, harness and catalog that later PRs also touch).
- Then 4b, 4c, 4d and 4e in parallel worktrees, rebased onto each other as each merges.
- 4f last.
- Forecasting F1 runs alongside throughout (no overlapping files).

## Verification (every PR)
- Targeted tests, then the full offline suite.
- Ruff F821/F822/F823/E9.
- Webapp typecheck and vitest (4c, 4d, 4f).
- Settings artifacts regenerated (4f).
- The import smoke check (4b onward).
- 4c: a `compose_and_emit` run that writes `execution_queue.json` into a sandboxed output dir.
- 4e: a daemon construction test proving `DataEngine` (not Mock) is chosen with no `credentials.json` present.

## Docs
- `CLAUDE.md`/`AGENTS.md`: collapse each archived area's bullets to a one-line "archived to legacy/ (2026-09, step 4x)" pointer.
- `docs/architecture/*.md` banners.
- `legacy/README.md` lists what moved and why.
