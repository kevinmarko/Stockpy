"""legacy/streamlit_command_center/desktop_shell/ — frozen native-shell package.

The pywebview wrapper's supporting modules (``net_util.py``, ``ui_server.py``,
``engine_supervisor.py``) that pop the frozen Streamlit Command Center into a
native window. Was ``desktop/``'s native-shell trio — the rest of
``desktop/`` (``daemon_runtime.py``, ``orchestrator_daemon.py``,
``run_history_store.py``, ``daemon_status.py``, ``assets/``) is live backend
infrastructure and stays at ``desktop/``, unaffected by this package.
"""

from __future__ import annotations
