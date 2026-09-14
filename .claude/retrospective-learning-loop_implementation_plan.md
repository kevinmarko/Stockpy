# Teamwork Project Prompt — Draft

> Status: Launched
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: Full team

Build out the "Retrospective Learning Loop" for the Stockpy quant platform. This feature adds an honest, per-trade and pattern-level retrospective view to the paper-trade ledger, capturing entry-time signal context and composing it with real MAE/MFE and calibration data without fabricating missing values. Expecting this to run as a full project.

Working directory: /Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/integrate_master_preprompt
Integrity mode: development

## Requirements

### R1. §0 Dependency Check & Schema Tracing
Before writing code, confirm the live state of `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`, trace surviving fields, confirm point-in-time signal archive existence, and locate current `paper_closed_trades` and `calibration_curve` consumers.

### R2. Entry-Time Decision Snapshot Capture (Forward-Only)
Persist provenance (manual vs automated) and signal context at paper-trade open into a new lightweight table. Capture this for EVERY paper trade (automated and manual). Trades predating this ship get "not captured," never a reconstruction.

### R3. Bridge Reliability Metric
Turn the existing fail-open bridge-failure tracking into a queryable completeness metric.

### R4. Read-Only Retrospective Composer
Combine `paper_closed_trades`, `evaluate_portfolio()` outputs, `calibration_curve`, and the new entry snapshot into one per-trade record reusing existing engines.

### R5. Templated Narrative & Batch Insights
Implement a v1 templated (non-LLM) sentence builder with strict 3-way provenance handling (Signal-driven / Manual / Unknown). Add pattern insights that strictly separate manual vs. automated cohorts.

### R6. Webapp UI & Documentation Sync
Render the new trade journal and batch insights in the Pilots PWA with honest empty/partial states. Sync `CLAUDE.md`, `AGENTS.md`, and architecture docs.

## Acceptance Criteria

### Fabrication Risk & Integrity (Strict)
- [ ] A trade's "why" is never upgraded or inferred; reads strictly from provenance tag or marks unknown.
- [ ] MAE/MFE is never shown for a trade the bridge didn't reach; defaults to "evaluation data unavailable".
- [ ] Narrative template branches must have explicit missing-data variants (no `None`/`NaN` rendered as numbers).
- [ ] Manual and signal-driven cohorts are never combined into aggregate batch statistics.
- [ ] Historical trades without an entry snapshot report "not captured", never inferred.

### Verification Protocol (Post-Build)
- [ ] WP-C: Deliberately try to make it claim data for a pre-existing trade and confirm it refuses/reports "not captured".
- [ ] WP-D: Force a real bridge-write failure in a test and confirm the completeness metric actually moves.
- [ ] WP-E: Diff composer's MAE/MFE/Edge Ratio output against a direct call to `evaluate_portfolio()` byte-for-byte.
- [ ] WP-F: Check every template branch against the fabrication-risk checklist.
- [ ] WP-G: Confirm manual/signal-driven cohort separation is structurally enforced.
- [ ] WP-H: Verify UI produces honest "unavailable"/"not captured" states for a worst-case combined state (manual, pre-feature, failed bridge).
