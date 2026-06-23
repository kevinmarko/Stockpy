import os
try:
    os.remove("daily_report_out.html")
    print("Deleted daily_report_out.html")
except Exception as e:
    print(f"Error: {e}")

try:
    os.remove("volatility_bands_out.html")
    print("Deleted volatility_bands_out.html")
except Exception as e:
    print(f"Error: {e}")
