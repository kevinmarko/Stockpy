import re
with open("scripts/refresh_validations.py", "r") as f:
    text = f.read()

text = text.replace(
    '        valid_idx = signals_df.dropna(subset=["spread", "z_score", "beta", "position"]).index',
    '    valid_idx = signals_df.dropna(subset=["spread", "z_score", "beta", "position"]).index'
)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(text)
