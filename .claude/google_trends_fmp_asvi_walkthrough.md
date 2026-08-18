# Walkthrough: Google Trends Overlapping Window Stitcher, ASVI Engine & FMP Data Loader

## Summary of Changes

We have integrated the data ingestion and modeling pipeline described in the architecture diagram and reference code:
1. **Google Trends Overlapping Window Stitcher (`GoogleTrendsStitcher`)**:
   - Reconstructs long-term continuous daily Search Volume Index (SVI) series from adjacent 90-day daily Google Trends intervals.
   - Computes empirical scaling factors $f = \frac{\sum SVI_{A, \text{overlap}}}{\sum SVI_{B, \text{overlap}}}$ for non-zero overlapping days to eliminate boundary jumps.
   - Smooths overlapping dates via boundary averaging.
   - Supports multi-window chained stitching.
2. **Abnormal Search Volume Index Calculator (`ASVICalculator`)**:
   - Implements Da, Engelberg & Gao (2011) Abnormal Search Volume Index:
     $$ASVI_t = \ln(SVI_t) - \ln(\text{Median}(SVI_{t-k \dots t-1}))$$
   - Enforces zero lookahead bias with strict $t-1$ shifting on the rolling median calculation.
   - Verified via perturbation testing (mutating future SVI does not change past/present ASVI).
3. **FMP Data Loader & Technical Indicators (`FMPDataLoader`)**:
   - Standardized daily OHLCV bar generation and ingestion.
   - Computes EMA-12, EMA-26, MACD, MACD Signal, MACD Histogram, and RSI-14 with epsilon bounds for sequence input tensors.
4. **Documentation & Tests**:
   - Comprehensive documentation in `docs/signals/google_trends_asvi.md`, `docs/architecture/data-layer.md`, `docs/signals/README.md`, `CLAUDE.md`, and `AGENTS.md`.
   - Unit tests and no-lookahead perturbation tests in `tests/test_trends_stitcher.py`.

---

## Verification Results

| Suite / Gate | Details | Status |
|---|---|---|
| **Trends Stitcher & ASVI Tests** | `tests/test_trends_stitcher.py` (9 tests) | ✅ **9/9 Passed** (0.44s) |
| **Combined Attention Test Suite** | `test_trends_stitcher.py` + `test_attention_sources.py` + `test_attention_pit_lookahead.py` (49 tests) | ✅ **49/49 Passed** (1.04s) |
| **Static Codebase Auditor** | `stockpy_codebase_auditor.py --root . --fail-on HIGH` | ✅ **0 Critical / 0 High** |
