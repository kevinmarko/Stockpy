import re

with open("api/pilots_api.py", "r") as f:
    content = f.read()

# Add gamma_scalper to OPTIONS_DESK_DEPLOYABILITY_GATES
addition = """    "zero_dte_engine": {
        "deployable": False,
        "gate_status": "UNGATEABLE_DATA_GAP",
        "reason": "Not gateable: No 1-minute intraday history exists for mandatory historical stress windows outside 30-day retention.",
    },
    "gamma_scalper": {
        "deployable": False,
        "gate_status": "UNGATEABLE_NOT_A_STRATEGY",
        "reason": "Excluded: Not a strategy (no scan/evaluate/execute path, no PaperAccountStore import, its only threshold is a hedge band).",
    },"""
content = re.sub(r'    "zero_dte_engine": \{.*?\n    \},', addition, content, flags=re.DOTALL)

# Add gate_status to gamma-scalp response
res_addition = """    res = to_gamma_scalp_response(raw)
    if isinstance(res, dict):
        res["gate_status"] = OPTIONS_DESK_DEPLOYABILITY_GATES["gamma_scalper"]
    return res"""

content = re.sub(r'    return to_gamma_scalp_response\(raw\)', res_addition, content)

with open("api/pilots_api.py", "w") as f:
    f.write(content)
