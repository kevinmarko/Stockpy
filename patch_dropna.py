import re

with open("scripts/refresh_validations.py", "r") as f:
    text = f.read()

replacement1 = """    # Drop rows where any required copula metric is genuinely missing instead of fabricating zeros (Constraint #4)
    valid_idx = signals_df.dropna(subset=["spread", "z_score", "beta", "position"]).index"""
text = re.sub(r'    valid_idx = signals_df\.dropna\(subset=\["spread"\]\)\.index', replacement1, text)

replacement2 = """    X = pd.DataFrame(index=valid_idx)
    X["Z_Score"] = signals_df["z_score"].loc[valid_idx]
    X["Spread"] = signals_df["spread"].loc[valid_idx]
    X["Beta"] = signals_df["beta"].loc[valid_idx]
    X["Position"] = signals_df["position"].loc[valid_idx]"""

text = re.sub(
    r'    X = pd\.DataFrame\(index=valid_idx\)\n    X\["Z_Score"\].*\n    X\["Spread"\].*\n    X\["Beta"\].*\n    X\["Position"\].*',
    replacement2,
    text
)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(text)
