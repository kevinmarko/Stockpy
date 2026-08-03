"""Rule: dynamic_name_literal_unattributable.

MULTIFACTOR_MICROCAP_THRESHOLD has zero statically-attributable reads, but its
name is right here as a string constant feeding a name-driven dispatcher.
Calling it `no_op` ("flipping this does nothing, ever") would be a lie, so it
fails closed to restart_required with the literal site as evidence. Mirrors
gui/panels/settings_manager.py's _SETTINGS_LAYOUT walk.
"""
from settings import settings

_LAYOUT = ["MULTIFACTOR_MICROCAP_THRESHOLD"]


def render():
    for key in _LAYOUT:
        yield key, getattr(settings, key, None)
