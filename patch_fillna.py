import re

with open("scripts/refresh_validations.py", "r") as f:
    text = f.read()

replacement = """    X = pd.DataFrame(index=valid_idx)
    X["Z_Score"] = signals_df["z_score"].loc[valid_idx].ffill().fillna(0.0)
    X["Spread"] = signals_df["spread"].loc[valid_idx]
    X["Beta"] = signals_df["beta"].loc[valid_idx].ffill().fillna(1.0)
    X["Position"] = signals_df["position"].loc[valid_idx].fillna(0.0)"""

text = re.sub(
    r'    X = pd\.DataFrame\(index=valid_idx\)\n    X\["Z_Score"\] = signals_df\["z_score"\].loc\[valid_idx\].fillna\(0\.0\)\n    X\["Spread"\] = signals_df\["spread"\].loc\[valid_idx\].fillna\(0\.0\)\n    X\["Beta"\] = signals_df\["beta"\].loc\[valid_idx\].fillna\(1\.0\)\n    X\["Position"\] = signals_df\["position"\].loc\[valid_idx\].fillna\(0\.0\)',
    replacement,
    text
)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(text)
