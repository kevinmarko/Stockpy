# Implementation Plan: Google Trends Overlapping Stitcher, ASVI Attention Engine & FMP Data Loader

## Summary
Integrated the quantitative data ingestion and attention modeling architecture described in the blueprint and reference code:
1. `GoogleTrendsStitcher` (`data/trends_stitcher.py`): Adjacent 90-day interval overlapping daily stitching eliminating boundary step-discontinuities.
2. `ASVICalculator` (`data/trends_stitcher.py`): Da, Engelberg & Gao (2011) Abnormal Search Volume Index with strict $t-1$ shift for zero lookahead bias.
3. `FMPDataLoader` (`data/trends_stitcher.py`): Standardized daily OHLCV bar generation and technical feature indicators.
4. Synced and rebased on `origin/main` incorporating PRs #771, #772, #773, #774, #776, #777.

## User Review Required

> [!NOTE]
> All new data structures and algorithms strictly preserve existing platform invariants: zero lookahead bias ($t-1$ shifted rolling windows), zero fabricated numbers on missing data, and opt-in settings.

---

## Proposed Changes

Grouped by component:

### 1. Data Layer: Trends Stitching & ASVI Calculator

#### [NEW] [`data/trends_stitcher.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/data/trends_stitcher.py)
- **`GoogleTrendsStitcher`**:
  - `stitch_intervals(period_a_svi: pd.Series, period_b_svi: pd.Series) -> pd.Series`: Computes scaling factor $f = \frac{\sum SVI_{A, \text{overlap}}}{\sum SVI_{B, \text{overlap}}}$ for non-zero overlapping days, rescales period B, and averages overlapping points.
  - `stitch_multiple_intervals(intervals: Sequence[pd.Series]) -> pd.Series`: Sequential left-to-right stitching across arbitrary chained periods.
- **`ASVICalculator`**:
  - `compute_asvi(svi_series: pd.Series, lookback_weeks: int = 12, epsilon: float = 0.1) -> pd.Series`: Calculates $ASVI_t = \ln(SVI_t) - \ln(\text{Median}(SVI_{t-k \dots t-1}))$. Strictly causal with `.shift(1)` to avoid lookahead bias.
- **`FMPDataLoader`**:
  - `fetch_historical_ohlcv(symbol, start_date, end_date)`: Fetches or generates standardized daily OHLCV bars.
  - `compute_technical_indicators(df: pd.DataFrame)`: Calculates EMA-12, EMA-26, MACD, MACD Signal, MACD Hist, RSI-14 with epsilon zero-division guard.

---

### 2. Integration into Attention Sources

#### [MODIFY] [`data/attention_sources.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/data/attention_sources.py)
- Integrate `ASVICalculator` and `GoogleTrendsStitcher` utilities for normalizing search volume series.

---

### 3. Documentation

#### [NEW] [`docs/signals/google_trends_asvi.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/docs/signals/google_trends_asvi.md)
- Complete mathematical specification of the Overlapping Window Stitching algorithm, ASVI derivation from Da, Engelberg & Gao (2011), and FMP sequence tensor alignment.

#### [MODIFY] [`docs/architecture/data-layer.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/docs/architecture/data-layer.md)
- Reference `data/trends_stitcher.py` and the Google Trends / ASVI attention workflow.

#### [MODIFY] [`AGENTS.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/AGENTS.md) / [`CLAUDE.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/CLAUDE.md)
- Document the new module in the conventions reference.

---

### 4. Unit & Lookahead Testing

#### [NEW] [`tests/test_trends_stitcher.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/phased_agent_audit_system/tests/test_trends_stitcher.py)
- Test 2-interval stitching with known analytical scaling factors.
- Test multi-interval chained stitching.
- Test error handling when intervals do not overlap.
- Test ASVI calculation against exact hand-computed values.
- Test no-lookahead bias: mutating future SVI does not change past/present ASVI.
- Test FMP technical indicators (EMA, MACD, RSI-14) calculation correctness and output bounds.

---

## Verification Plan

### Automated Tests
```bash
uv run pytest tests/test_trends_stitcher.py -v
uv run pytest tests/test_attention_sources.py tests/test_attention_pit_lookahead.py -v
python3 scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH
```
