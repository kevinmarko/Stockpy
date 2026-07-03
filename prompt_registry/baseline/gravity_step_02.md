Analyze the provided source code for Step 2. Verify the mathematical implementation of the following indicators and strategy logic:
1. MACD & RSI: Check that Moving Average Convergence Divergence (12, 26, 9) and Relative Strength Index (14) are calculated using Exponential Moving Averages (EMA).
2. VOLATILITY BANDS: Verify the presence of Average True Range (ATR) and Chandelier Exits or Bollinger Bands.
3. STRATEGY CHOP-FILTERS: Confirm the Strategy Engine utilizes an Aroon Oscillator Chop-Filter to suppress false MACD whipsaws, GARCH Volatility to penalize tail-risk, and the Edge Ratio (expectancy) as a gate before allowing maximum Kelly sizing.
4. LOOKAHEAD BIAS: Ensure that indicators only calculate using historical closing prices up to time t, never t+1 (e.g., correct use of .shift(1) where necessary).

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
