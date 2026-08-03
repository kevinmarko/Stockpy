"""No rule should fire on ANY of these -- every one is a fresh read.

The lambda cases mirror gui/help_content.py's lazily-wrapped help strings: the
f-string interpolating a settings value is deferred into a zero-arg callable
precisely so the value is resolved at render time rather than at import.
"""
from settings import settings


def current_cap():
    return settings.KELLY_CAP


CAP_TEXT = lambda: f"cap={settings.KELLY_FRACTION}"  # noqa: E731

HELP = {
    "vol": lambda: f"Vol target is {settings.VOL_TARGET}",
}


class Provider:
    @property
    def dry_run(self):
        # A property body is re-evaluated on every attribute access.
        return settings.DRY_RUN

    def refresh(self):
        return settings.BARS_BACKFILL_DAYS
