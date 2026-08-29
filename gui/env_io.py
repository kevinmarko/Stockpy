"""
gui/env_io.py
==============
Re-export shim. The real implementation moved to the top-level ``env_io.py``
(F13, docs/module_efficiency_redundancy_audit.md): this module is a
safety-critical SECRET_KEYS/ALLOWED_KEYS credential-write registry, imported
directly by production API code entirely outside the ``gui/`` package
(``api/pilots_api.py``, ``api/_redact.py``, ``api/data_api.py``,
``investyo_mcp_server.py``, ``runtime_flags_writer.py``, and others) --
living inside a decommissioned, no-new-development package was an
architectural inversion for code the live backend genuinely depends on.

This file exists SOLELY so the frozen Command Center's own internal imports
(``from gui import env_io`` / ``from gui.env_io import X``, used throughout
``gui/panels/*.py``, ``gui/ai_control_center.py``, ``gui/strategy_registry.py``,
``gui/robinhood_execution_panel.py``, ``gui/help_content.py``) keep working
completely unchanged -- do not add new logic here; add it to ``env_io.py``.

TRUE ALIAS, not a copy -- ``sys.modules`` identity, not ``from env_io import
*``. A first attempt used ``import *``, which turned out to be a genuine bug,
not a style choice: ``import *`` performs a STATIC, IMPORT-TIME value copy of
every name into this module's own namespace. That means ``gui.env_io.X`` and
``env_io.X`` become two SEPARATE dict entries the instant this shim is first
imported -- a test that later does ``monkeypatch.setattr(env_io, "X", ...)``
(patching the real module, correctly, per the module docstring's own
instruction) has NO effect on ``gui.env_io.X``, which still holds whatever
``env_io.X`` was at shim-import time. Confirmed as a real, live incident
during this relocation: two tests whose production code under test
(``gui/strategy_registry.py::set_active_mode``) calls ``gui.env_io.write_setting``
silently bypassed their own monkeypatch and wrote to this operator's REAL
``.env`` file (twice) before this was caught and the aliasing scheme below was
adopted instead.

Registering this exact same module object under BOTH names in ``sys.modules``
makes ``gui.env_io`` and ``env_io`` the LITERALLY IDENTICAL object -- one
namespace dict, so ``gui.env_io.write_setting`` and ``env_io.write_setting``
are always the same attribute, and a patch on either name is visible through
both, at every access, forever (not just at the moment this file first
imports). ``sys.modules`` aliasing is a standard, well-understood CPython
idiom for exactly this "old import path -> new module, transparently"
relocation case.
"""
import sys

import env_io as _env_io

sys.modules[__name__] = _env_io
