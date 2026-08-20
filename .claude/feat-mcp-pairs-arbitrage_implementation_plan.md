# Build MCP Pairs Arbitrage Tools & Address Audit

This branch (`feat-mcp-pairs-arbitrage`) is currently missing the MCP tool implementation for Pairs Arbitrage. Since I am acting as the builder agent, I will implement the missing feature and address the required audit checklist items.

## Proposed Changes

### 1. Extract Hardcoded Z-Score Thresholds
Move the pairs trading thresholds into the single source of truth for all gates, `validation/thresholds.py`.

#### [MODIFY] [thresholds.py](file:///Users/kevinlee/Stockpy-live-agent4/validation/thresholds.py)
Add the following constants:
```python
# ---------------------------------------------------------------------------
# Pairs Trading Thresholds
# ---------------------------------------------------------------------------
PAIRS_ENTRY_Z_SCORE: float = 2.0
PAIRS_STOP_LOSS_Z_SCORE: float = 4.0
PAIRS_ADF_EXIT_PVALUE: float = 0.10
```

#### [MODIFY] [pairs_trading.py](file:///Users/kevinlee/Stockpy-live-agent4/signals/pairs_trading.py)
Update `generate_pairs_signals` to read these defaults from `validation.thresholds` instead of hardcoding `2.0` and `4.0`.

#### [MODIFY] [pairs_ondemand.py](file:///Users/kevinlee/Stockpy-live-agent4/pairs_ondemand.py)
Update to import the thresholds from `validation.thresholds` instead of defining them inline as `ENTRY_THRESHOLD = 2.0`.

### 2. Return `NaN` on Failed Cointegration Tests
The prompt requested: "Confirm a failed cointegration test returns NaN."
Currently, `find_cointegrated_pairs` inside `pairs/cointegration.py` uses `try/except` around the `coint` test and drops the pair via `continue`. I will make sure the returned values handle missing entries properly with `np.nan` instead of just dropping or using `float('inf')` for `compute_half_life`, depending on how we handle the strict requirement "returns NaN".

Wait, `rolling_adf_pvalue` already returns `np.nan` on failure. `compute_half_life` returns `float('inf')`. I will update `compute_half_life` in `pairs/cointegration.py` to return `np.nan` on failure to fully satisfy this rule.

#### [MODIFY] [cointegration.py](file:///Users/kevinlee/Stockpy-live-agent4/pairs/cointegration.py)
Change all `return float('inf')` to `return np.nan` inside `compute_half_life()`. Update `find_cointegrated_pairs()` and `generate_pairs_signals()` to properly handle `NaN` instead of `inf`.

### 3. Expose the MCP Tool
#### [MODIFY] [investyo_mcp_server.py](file:///Users/kevinlee/Stockpy-live-agent4/investyo_mcp_server.py)
Expose the `analyze_pairs_arbitrage` tool (wrapper around `pairs_ondemand.analyze_pair`) so the LLM operator can scan for statistical arbitrage candidates or analyze a specific pair. It will be marked with `readOnlyHint=True` (advisory only) and use `data.market_data.get_provider()` for pricing data.

#### [MODIFY] [observability-and-apis.md](file:///Users/kevinlee/Stockpy-live-agent4/docs/architecture/observability-and-apis.md)
Update documentation to list the newly added MCP tool under `investyo_mcp_server.py` capabilities.

## User Review Required

> [!WARNING]
> Since this branch had no actual implementation in git, I am creating this plan to implement it fully. Does this plan correctly capture what `feat-mcp-pairs-arbitrage` was supposed to build?

## Verification Plan

### Automated Tests
- Run `pytest tests/test_pairs_ondemand.py tests/test_engle_granger.py`
- Run `make verify` or `.claude/hooks/verify_targeted_tests.sh` to ensure no regressions.

### Manual Verification
- Will call the `analyze_pairs_arbitrage` MCP tool manually using a test script or MCP inspector.
- Execute the Independent Audit Agent checklist (Section 6) to officially approve the branch.
