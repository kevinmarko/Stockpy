import re
with open("scripts/refresh_validations.py", "r") as f:
    text = f.read()

print("dispersion" in text)
print("earnings_crush" in text)
matches = re.findall(r'^    "([a-z0-9_]+)": \(', text, re.MULTILINE)
print("Regex matches:", matches)
