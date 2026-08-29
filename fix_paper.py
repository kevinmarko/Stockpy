with open("scripts/refresh_validations.py", "r") as f:
    text = f.read()

text = text.replace(
    '    "put_debit_spread",\n    "vrp_premium_selling",',
    '    "put_debit_spread",\n    "vol_mispricing",\n    "vrp_premium_selling",'
)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(text)
