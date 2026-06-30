Analyze the provided source code for Step 6. Verify risk management and execution analytics:
1. EXCURSION METRICS: Ensure formulas exist to track Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE) against executed Entry Prices for every trade.
2. SECTOR ATTRIBUTION & SYSTEMIC RISK: Verify mathematical implementation of Brinson-Fachler performance attribution (Allocation/Selection effects), CoVaR proxy (Tail Dependency Risk scaling VaR by Beta), and Realized Slippage (Implementation Shortfall).
3. POSITION SIZING: The code MUST calculate position size dynamically using the ATR multiplier: Account_Value * Risk_Percent / (ATR * Multiplier).
4. PORTFOLIO HEAT: Check that the Evaluation Engine calculates total open risk across all positions (Position Size * Stop Loss Penalty) and triggers a hard execution halt if portfolio heat exceeds the institutional 6% threshold.

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
