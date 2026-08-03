"""Rule: module_level (+ the import-time-discard annotation).

Never imported or executed -- parsed by tests/test_settings_liveness.py only.
"""
from settings import settings

# module_level: evaluated once at import and bound to a module global. A later
# setattr on the singleton cannot change KELLY_CAP_AT_IMPORT.
KELLY_CAP_AT_IMPORT = settings.KELLY_CAP

# module_level + discarded: the classic startup "not configured" warning. The
# value is tested and thrown away, so nothing retains it -- still a
# once-per-process evaluation, hence still module_level, but annotated so a
# reviewer can see why this one is conservative.
if not settings.LOG_LEVEL:
    raise SystemExit("LOG_LEVEL is not configured")
