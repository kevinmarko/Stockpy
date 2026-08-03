"""Rule: os_environ -- all three shapes.

pydantic-settings loads `.env` into the Settings MODEL, not into the real
`os.environ`, so a setattr on the singleton can never be observed by any of
these. Never live-patchable, by construction.
"""
import os

TIMEOUT = os.getenv("LLM_COMMENTARY_TIMEOUT_SECONDS")
VERBOSITY = os.environ.get("RATIONALE_VERBOSITY")
CHANNELS = os.environ["ALERT_CHANNELS"]
