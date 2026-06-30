Analyze the provided source code for Step 3. Verify the quantitative derivatives modeling:
1. BLACK-SCHOLES: Validate the Black-Scholes PDE implementation. It must calculate theoretical pricing and output the Greeks (Delta, Gamma, Theta, Vega).
2. IVR CALCULATION: Ensure Implied Volatility Rank (IVR) is calculated correctly over a 52-week rolling window: (Current IV - 52W Low IV) / (52W High IV - 52W Low IV) * 100.
3. STRATEGY MATRIX: Check the automated options routing logic.
   * If IVR > 70: Must deploy Credit Spreads or Iron Condors.
   * If IVR < 30: Must deploy Debit Spreads or Calendar Spreads.
4. DELTA HEDGING: Verify logic for calculating portfolio beta/delta and sizing protective puts.

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
