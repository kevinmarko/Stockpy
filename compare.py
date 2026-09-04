with open("docs/VALIDATION_STRATEGY_FIX_LOG.md", "r") as f:
    log = f.read()

import re
log_strats = re.findall(r'\| `([a-z0-9_]+)` \|', log)
log_strats = set(log_strats)

with open("scripts/refresh_validations.py", "r") as f:
    reg = f.read()

reg_strats = re.findall(r'^    "([a-z0-9_]+)": \(', reg, re.MULTILINE)
reg_strats = set(reg_strats)

print("In registry but not in log:")
print(reg_strats - log_strats)
print("In log but not in registry:")
print(log_strats - reg_strats)
