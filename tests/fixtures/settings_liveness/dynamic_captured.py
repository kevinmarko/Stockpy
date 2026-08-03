"""The fail-closed poison case: a dynamic getattr in a CAPTURE context.

The key arrives as a variable, so this read is unattributable to any single
field; and it runs at import, so whatever it read is frozen. Neither half can
be repaired, so the only honest answer is that NO field can be called
live_safe or no_op while this exists. Analysed in isolation by the test --
if it were part of the real tree it would (correctly) collapse the whole
report.
"""
from settings import settings

_KEY_NAME = "LOG_LEVEL"

DEFAULT_LEVEL = getattr(settings, _KEY_NAME, "INFO")
