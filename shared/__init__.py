"""
shared/ — live backend logic shared by the Pilots PWA/API layer and the
frozen legacy Streamlit Command Center (legacy/streamlit_command_center/).

These modules originated in gui/ but are NOT part of the decommissioned
UI — they are imported directly by production code (api/pilots_api.py,
api/data_api.py, api/_jobs.py, pilots/*, main.py, evaluation_engine.py,
alerting.py, diagnostics_and_visuals.py, and scripts/). Keep this file free
of any real import statement — tests/test_pilots_api.py's
test_gui_package_init_stays_import_inert enforces this, since
api/pilots_api.py importing shared.daemon_client executes this file as a
side effect and would silently inherit anything added here.
"""

from __future__ import annotations
