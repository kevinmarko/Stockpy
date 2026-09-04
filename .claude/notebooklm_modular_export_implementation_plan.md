# Modular Multi-Source Knowledge Pack for Google NotebookLM (6-Agent Execution)

This plan expands the Google NotebookLM pipeline from a single consolidated export into a **Modular Multi-Source Knowledge Pack** (generating both the unified `notebooklm_source.md` and dedicated, deep-dive source documents under `output/notebooklm/`). This directly satisfies NotebookLM's multi-source architecture (up to 50 sources per notebook), unlocking focused Audio Overviews ("deep-dive podcasts") and cross-source comparative queries.

The work will be executed across **6 specialized agent roles**:
1. **Agent 1 (Macro & Regime)**: `01_macro_and_regime.md` generator
2. **Agent 2 (Portfolio & Greeks)**: `02_portfolio_and_greeks.md` generator
3. **Agent 3 (Signals & Picks)**: `03_strategy_signals_and_picks.md` generator
4. **Agent 4 (Trade Journal & Ledger)**: `04_trade_journal_and_ledger.md` generator
5. **Agent 5 (Options Matrix & Directives)**: `05_options_directives_and_matrix.md` generator
6. **Agent 6 (Driver, Tests & Honesty Auditor)**: Unified CLI orchestrator, atomic writer, comprehensive test coverage (`tests/test_export_notebooklm.py`), and documentation update.

---

## User Review Required

> [!IMPORTANT]
> **Output Architecture & Backward Compatibility**:
> By default, running `python scripts/export_notebooklm.py` will generate:
> 1. The existing consolidated `notebooklm_source.md` (preserving full backwards compatibility for users who upload a single file to NotebookLM).
> 2. The modular folder `output/notebooklm/` containing all 5 numbered source documents (`01_macro_and_regime.md` through `05_options_directives_and_matrix.md`).
> An optional CLI flag `--modular-only` or `--consolidated-only` will be provided.

> [!NOTE]
> **CONSTRAINT #4 (Never Fabricate Data)**:
> In accordance with platform integrity rules, every section degrades cleanly to `"N/A"` or explicit notice banners when underlying data is missing or cold; no zero-filling or placeholder values are ever introduced.

---

## Proposed Changes & 6-Agent Breakdown

### Component 1: `scripts/export_notebooklm.py` (Modular Architecture)

Refactor `scripts/export_notebooklm.py` into modular, independently callable generator functions that compose both into separate files and the consolidated overview.

#### [MODIFY] [`scripts/export_notebooklm.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/integrate_google_notebook_pipeline/scripts/export_notebooklm.py)

- **Agent 1 (`macro-regime-builder`)**:
  - Implement `generate_macro_regime_source(store, output_dir=None) -> str`:
  - Pulls FRED series (`VIXCLS`, `T10Y2Y`, `BAMLH0A0HYM2`) from `HistoricalStore(readonly=True)`.
  - Reads HMM regime state, HMM risk-on probability, and macro kill-switch state from `output/state_snapshot.json` (or `MacroEconomicDTO`).
  - Emits markdown formatted with clear sections, definitions, and status badges.

- **Agent 2 (`portfolio-greeks-builder`)**:
  - Implement `generate_portfolio_greeks_source(store, output_dir=None) -> str`:
  - Reads `store.latest_account_snapshot()` mapped via `_serialize_portfolio`.
  - Calculates live portfolio net Greeks via `pilots.options_risk.calculate_portfolio_greeks()` ($\Delta_{\$}$, $\Gamma$, $\Theta$, $\mathcal{V}$, $\beta$-weighted $\Delta_{\text{SPY}}$, SPY spot price).
  - Emits position tables with cost basis, current price, market value, unrealized P&L, and portfolio Greek totals.

- **Agent 3 (`signals-picks-builder`)**:
  - Implement `generate_signals_picks_source(output_dir=None) -> str`:
  - Reads `output/state_snapshot.json`'s `signals` list.
  - Formats top BUY / SELL / HOLD ratings, conviction levels, `buy_range`, `sell_range`, Kelly position sizing targets, and multifactor z-scores (`Value_Z`, `Quality_Z`, `Momentum_Z`, `LowVol_Z`, `Size_Z`).
  - Includes active strategy subscriptions from `FollowsStore().list_active()`.

- **Agent 4 (`trade-ledger-builder`)**:
  - Implement `generate_trade_journal_source(output_dir=None) -> str`:
  - Reads `pilots.trade_history.trade_history_view()` and `BrokerFillsStore(readonly=True)`.
  - Summarizes lifetime & recent performance KPIs: total closed trades, win rate %, profit factor, gross profit/loss, average return %, and average holding duration.
  - Renders a clean chronological table of the most recent 25 closed trades with symbol, entry/exit dates, holding days, realized P&L, and return %.

- **Agent 5 (`options-matrix-builder`)**:
  - Implement `generate_options_matrix_source(output_dir=None) -> str`:
  - Reads `output/options_matrix.json`.
  - Extracts active premium directives (Call/Put Credit Spreads, Iron Condors), target DTE, IV Rank (`True_IVR` / `IVR_Proxy`), GARCH sigma, trend bias, strikes, and credit received.
  - Summarizes fundamental safety metrics (Altman Z-Score, Piotroski F-Score) and recent headline news catalysts per candidate.

- **Agent 6 (`exporter-driver-auditor`)**:
  - CLI & Dispatcher: Updates `build_export()` to orchestrate all 5 modules, writing to `output/notebooklm/01_macro_and_regime.md` .. `05_options_directives_and_matrix.md` plus `output/notebooklm_source.md`.
  - Ensures atomic writing (`.tmp` + `replace`) for every output file.
  - Adds CLI options: `--output-dir`, `--modular-only`, `--consolidated-only`, `--section <name>`.
  - Re-generates command manifest via `python3 scripts/build_command_manifest.py`.

---

### Component 2: Verification & Test Suite

#### [MODIFY] [`tests/test_export_notebooklm.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/integrate_google_notebook_pipeline/tests/test_export_notebooklm.py)
- Expand test suite to test:
  1. Each modular generator function independently with mock and degraded stores.
  2. Multi-file directory creation and atomic replacement.
  3. Strict CONSTRAINT #4 compliance (asserting missing values render as `"N/A"` and never `$0.00`).
  4. CLI argument parsing (`--modular-only`, `--consolidated-only`, etc.).

---

### Component 3: Documentation Update

#### [MODIFY] [`docs/GOOGLE_NOTEBOOK_INTEGRATION.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/integrate_google_notebook_pipeline/docs/GOOGLE_NOTEBOOK_INTEGRATION.md)
- Update Phase 1 / Phase 2 status from roadmap to delivered feature.
- Document file schemas for `01_macro_and_regime.md` through `05_options_directives_and_matrix.md`.
- Provide recommended prompts to use within NotebookLM for cross-source analysis and Audio Overview generation.

---

## Verification Plan

### Automated Tests
1. Run updated test suite:
   ```bash
   python3 -m pytest tests/test_export_notebooklm.py -v
   ```
2. Verify command manifest freshness:
   ```bash
   python3 -m pytest tests/test_command_manifest_freshness.py tests/test_build_command_manifest.py -v
   ```
3. Run the export script live:
   ```bash
   python3 scripts/export_notebooklm.py
   ```
4. Check generated file outputs:
   - `~/.stockpy_local/output/notebooklm_source.md`
   - `~/.stockpy_local/output/notebooklm/01_macro_and_regime.md`
   - `~/.stockpy_local/output/notebooklm/02_portfolio_and_greeks.md`
   - `~/.stockpy_local/output/notebooklm/03_strategy_signals_and_picks.md`
   - `~/.stockpy_local/output/notebooklm/04_trade_journal_and_ledger.md`
   - `~/.stockpy_local/output/notebooklm/05_options_directives_and_matrix.md`

### Manual Verification
- Inspect generated markdown content to ensure readability, table formatting, and accurate presentation in NotebookLM source preview.
