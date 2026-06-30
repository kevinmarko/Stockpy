Analyze the provided source code for Step 4. Verify the predictive modeling framework:
1. PREPROCESSING: Verify the data is scaled (e.g., MinMaxScaler or StandardScaler) BEFORE being fed into models.
2. TIME-SERIES FORMATTING: Ensure 2D data is successfully reshaped into 3D tensors [samples, time_steps, features] for LSTM models.
3. STRUCTURAL DRIFT: Verify that ARIMA/Holt-Winters models explicitly enforce trend parameters (e.g., trend='t' or 'add') and Monte Carlo simulations mathematically calculate and inject structural drift (mu - 0.5 * var) to prevent horizontal 0-mean averaging.
4. STATIONARITY: Verify the presence of an Augmented Dickey-Fuller (ADF) test ensuring time-series data is stationary before prediction.

Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
