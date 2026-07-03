Analyze the provided source code for Step 5. Verify external data ingestion and macro logic:
1. FRED API INTEGRATION: Code must pull the 10Y-2Y Yield Curve, Sahm Rule Unemployment data, and High Yield Credit Spreads.
2. VALUATION INDEPENDENCE: Verify that the Graham Number and Gordon Fair Value are calculated using completely distinct mathematical bounds and mapped to independent dictionary keys to prevent collision.
3. REGIME GOVERNANCE: Verify an override mechanism where if the Yield Curve is inverted AND Credit Spreads are widening, the system translates this to a "CREDIT EVENT" state. The macroeconomic penalty for 'RECESSION' or 'CREDIT EVENT' must apply a reduced -5 score deduction rather than a hard score freeze.

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
