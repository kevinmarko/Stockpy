# Implementation Plan: Worktree Reconciliation, Gap Closure, and Diagnostic Widget Rollout

Adopting the roadmap to reconcile `/Users/kevinlee/Stockpy-live` and the `integrate_mcp_devtools_widget` worktree, verify leakage and gross position limits, and sequence widget rollout with rigorous "known-bad" test coverage.

---

## User Review Required

> [!IMPORTANT]
> **No Premature Merges to Main**: All findings from Phase 0 (test output capture, assertion categorization, diff inspection) and Phase 1 (gross cap sweep and purge/embargo audit) will be presented with raw command outputs and diffs before proposing any promotion or merge.

---

## Phase Breakdown

### Phase 0: Worktree Reconciliation & Test Assertion Audit (Immediate)
1. **Raw Test Suite Execution**:
   - Run `pytest tests/test_investyo_mcp_widgets.py -v` and `pytest tests/test_investyo_mcp_server.py -v`, capturing raw output to `/tmp/widget_test_output.txt`.
2. **Assertion Categorization**:
   - Audit `tests/test_investyo_mcp_widgets.py` line-by-line: categorize checks into (a) registration/placeholder substitution vs. (b) behavioral schema & degradation assertions.
3. **Change Surface & Diff Analysis**:
   - Review `git diff origin/main...HEAD --stat` and verify that `walkthrough.md` matches the actual code surface.
4. **Widget Triage Table**:
   - Break down all 10 unmerged widgets:
     - **Diagnostic Priority (Phase 2)**: `pit-audit-matrix.html`, `model-diagnostics.html`
     - **Quant & Trading Core (Phase 3)**: `backtest-tearsheet.html`, `macro-regime-radar.html`, `order-ticket.html`
     - **PWA Dev Tools (Deferred)**: `visual-diff.html`, `network-trace.html`, `devtools-inspector.html`, `lighthouse-scorecard.html`
     - **Parameter Sensitivity (Deferred to post-1a)**: `strategy-tuner.html`

---

### Phase 1: Close Functionally Incomplete Gaps
1. **1a. Calibrate `MAX_PORTFOLIO_GROSS`**:
   - Audit `sizing/position_sizer.py::apply_portfolio_gross_cap()` and `settings.MAX_PORTFOLIO_GROSS`.
   - Run historical evaluation across candidate gross caps (1.0, 1.5, 2.0, 3.0) to observe binding frequency and drawdown impact.
   - Document calibrated default and rationale.
2. **1b. Verify CNN-LSTM Leakage Mitigation**:
   - Audit `cnn_lstm_worker.py::purge()` and cross-sectional normalization fold scoping.
   - Verify purge/embargo boundaries across all walk-forward splits.
   - Write a standalone test/script asserting no training timestamp $\ge$ validation timestamp minus embargo.

---

### Phase 2: Promote Diagnostic Widgets with "Known-Bad" Test Cases
1. **2a. PIT Fundamentals Matrix (`pit-audit-matrix.html`)**:
   - Wire against post-Phase-1 validated pipeline.
   - Write unit tests with synthetic known-bad inputs (lookahead filing dated post-evaluation, missing 45d lag buffer) to verify the report and widget flag them red.
2. **2b. Model Diagnostics & Drift (`model-diagnostics.html`)**:
   - Write unit tests with synthetic injected drift (>15% skill decay, PSI spikes) asserting drift warnings fire.

---

### Phase 3: Sequence Trading & Quant Widgets
- Validate and promote `backtest-tearsheet.html`, `macro-regime-radar.html`, and `order-ticket.html` with verified constraints.

---

## Verification Plan

### Automated Tests
```bash
# Capture raw outputs
pytest tests/test_investyo_mcp_widgets.py -v > /tmp/widget_test_output.txt 2>&1
pytest tests/test_investyo_mcp_server.py -v >> /tmp/widget_test_output.txt 2>&1

# Known-bad regression tests
pytest tests/test_pit_leakage_regression.py -v
pytest tests/test_model_drift_synthetic.py -v
```

### Manual & Diff Inspection
- Review raw test logs in `/tmp/widget_test_output.txt`.
- Inspect `git diff origin/main...HEAD --stat`.
