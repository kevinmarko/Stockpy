"""
gui/ai_control_center.py — headless helpers for the AI Control Center tab.
==========================================================================

The Streamlit wiring lives in :func:`gui.panels.render_ai_control_center`.
This module hosts the pure logic it depends on so it is unit-testable WITHOUT
Streamlit — mirrors :mod:`gui.ai_insights_panel` / :mod:`gui.gravity_ai_panel`.

The Control Center is the single operator-facing surface for every AI option
on the platform. This module supplies:

* :data:`CAPABILITIES` — the registry of all AI options (Claude commentary,
  Gemini alert commentary, Gemini chart vision, Gravity AI runner, Opal
  research), each described by an :class:`AICapability`.
* :func:`capability_status` — a four-state classifier (``ready`` /
  ``disabled`` / ``missing_key`` / ``not_built``) per capability, derived
  from the live settings + whether the backing module is importable.
* :func:`control_center_overview` — one status row per capability for the grid.
* :func:`validate_toggle_write` — guards a toggle write: the key must be in
  ``gui.env_io.ALLOWED_KEYS`` and must NOT be a secret (CONSTRAINT #3).

Design invariants
-----------------
* No Streamlit import — everything here is testable cold.
* Operator-only — this module never triggers an AI call; it only describes
  state and validates writes. Triggering happens via the panel's buttons.
* No secret exposure — key-presence is reported as a bool, never the value.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)


CapabilityStatus = Literal["ready", "disabled", "missing_key", "not_built"]


@dataclass(frozen=True)
class AICapability:
    """One AI option surfaced in the Control Center.

    Attributes
    ----------
    key :
        Stable identifier (e.g. ``"claude_commentary"``).
    label :
        Operator-facing name.
    enable_settings :
        The ``settings`` boolean/string attributes that gate this capability.
        The capability is "enabled" iff the primary (first) switch is truthy
        AND, when a provider-selector is listed, it is not ``"none"``.
    provider_key_settings :
        The ``settings`` attribute name(s) holding the required provider API
        key(s). ALL must be present for the capability to be ``ready``.
    module :
        Import path whose presence means the backing code is built (used to
        gate Opal until its backend ships). ``None`` = always built.
    trigger :
        ``"on_demand"`` (a per-symbol / run-now button) or ``"scheduled"``
        (fires automatically only during an operator-started --interval/--agent
        run — e.g. Gemini alert commentary).
    toggle_key :
        The ``.env`` key the Control Center toggle writes (must be in
        ``ALLOWED_KEYS``). ``None`` = read-only status row.
    help :
        One-line operator help.
    """

    key: str
    label: str
    enable_settings: Tuple[str, ...]
    provider_key_settings: Tuple[str, ...]
    module: Optional[str]
    trigger: str
    toggle_key: Optional[str]
    help: str


# ---------------------------------------------------------------------------
# The registry — every AI option on the platform, in display order.
# ---------------------------------------------------------------------------
CAPABILITIES: Tuple[AICapability, ...] = (
    AICapability(
        key="claude_commentary",
        label="Claude analyst commentary",
        enable_settings=("LLM_COMMENTARY_ENABLED", "LLM_COMMENTARY_RATIONALE_PROVIDER"),
        provider_key_settings=("ANTHROPIC_API_KEY",),
        module="llm.commentary",
        trigger="on_demand",
        toggle_key="LLM_COMMENTARY_ENABLED",
        help="Per-symbol analyst 'why' note. On-demand button (Section B).",
    ),
    AICapability(
        key="gemini_alerts",
        label="Gemini alert commentary",
        enable_settings=("LLM_COMMENTARY_ENABLED", "LLM_COMMENTARY_ALERT_PROVIDER"),
        provider_key_settings=("GEMINI_API_KEY",),
        module="llm.commentary",
        trigger="scheduled",
        toggle_key="LLM_COMMENTARY_ENABLED",
        help="Concise ntfy alert bodies. Fires automatically during a scheduled run (Section D).",
    ),
    AICapability(
        key="gemini_vision",
        label="Gemini chart vision",
        enable_settings=("LLM_COMMENTARY_ENABLED",),
        provider_key_settings=("GEMINI_API_KEY",),
        module="llm.chart_insight",
        trigger="on_demand",
        toggle_key="LLM_COMMENTARY_ENABLED",
        help="Chart-pattern interpretation from a rendered chart. On-demand button (Section B).",
    ),
    AICapability(
        key="gravity_ai_runner",
        label="Gravity AI runner (Claude + Gemini)",
        enable_settings=("GRAVITY_AI_RUNNER_ENABLED",),
        provider_key_settings=("ANTHROPIC_API_KEY", "GEMINI_API_KEY"),
        module="engine.gravity_ai_runner",
        trigger="on_demand",
        toggle_key="GRAVITY_AI_RUNNER_ENABLED",
        help="AI audit of the codebase — Claude auditor + Gemini cross-checker. Run-now button (Section C).",
    ),
    AICapability(
        key="opal_research",
        label="Opal research agent (OpenAI)",
        enable_settings=("OPAL_RESEARCH_ENABLED", "OPAL_RESEARCH_PROVIDER"),
        provider_key_settings=("OPENAI_API_KEY",),
        module="llm.research",  # gated until the Opal backend ships (docs/OPAL_BUILD_SPEC.md)
        trigger="on_demand",
        toggle_key="OPAL_RESEARCH_ENABLED",
        help="Grounded research brief per symbol (front-of-pipeline). Requires the Opal backend build.",
    ),
)


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def _module_available(module: Optional[str]) -> bool:
    """True if ``module`` can be imported (backing code is built)."""
    if not module:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _is_enabled(settings_obj: Any, cap: AICapability) -> bool:
    """True iff the capability's primary switch is on (and provider != none)."""
    if not cap.enable_settings:
        return False
    primary = getattr(settings_obj, cap.enable_settings[0], False)
    if not primary:
        return False
    # A provider-selector, when present as a second gate, must not be "none".
    for extra in cap.enable_settings[1:]:
        val = getattr(settings_obj, extra, None)
        if isinstance(val, str) and val.strip().lower() == "none":
            return False
    return True


def _keys_present(settings_obj: Any, cap: AICapability) -> bool:
    """True iff every required provider key is present + non-empty."""
    for k in cap.provider_key_settings:
        if not (getattr(settings_obj, k, None) or ""):
            return False
    return True


def capability_status(settings_obj: Any, cap: AICapability) -> Dict[str, Any]:
    """Classify a single capability's readiness.

    Returns a dict: ``{enabled, key_present, built, status}`` where ``status``
    is one of ``ready`` / ``disabled`` / ``missing_key`` / ``not_built``.

    Ordering of the verdict (most-blocking first):
      1. ``not_built``    — backing module absent (e.g. Opal before its build).
      2. ``disabled``     — master switch off.
      3. ``missing_key``  — enabled but a provider key is unset.
      4. ``ready``        — enabled + built + keys present.
    """
    built = _module_available(cap.module)
    enabled = _is_enabled(settings_obj, cap)
    key_present = _keys_present(settings_obj, cap)

    if not built:
        status: CapabilityStatus = "not_built"
    elif not enabled:
        status = "disabled"
    elif not key_present:
        status = "missing_key"
    else:
        status = "ready"

    return {
        "enabled": bool(enabled),
        "key_present": bool(key_present),
        "built": bool(built),
        "status": status,
    }


def control_center_overview(settings_obj: Any) -> List[Dict[str, Any]]:
    """Return one status row per capability, in display order."""
    rows: List[Dict[str, Any]] = []
    for cap in CAPABILITIES:
        st = capability_status(settings_obj, cap)
        rows.append({
            "key": cap.key,
            "label": cap.label,
            "trigger": cap.trigger,
            "toggle_key": cap.toggle_key,
            "provider_keys": list(cap.provider_key_settings),
            **st,
        })
    return rows


STATUS_BADGE: Dict[str, str] = {
    "ready": "🟢 ready",
    "disabled": "⚪ disabled",
    "missing_key": "🟡 key missing",
    "not_built": "🚧 not built",
}


def status_badge(status: str) -> str:
    """Map a status token to an operator-facing badge string."""
    return STATUS_BADGE.get(status, status)


# ---------------------------------------------------------------------------
# Toggle-write guard (CONSTRAINT #3)
# ---------------------------------------------------------------------------


def validate_toggle_write(key: str) -> None:
    """Raise if ``key`` is not a safe, GUI-writable Control Center toggle.

    * A secret key (in ``gui.env_io.SECRET_KEYS``) raises ``SecretWriteError``.
    * A key outside ``gui.env_io.ALLOWED_KEYS`` raises ``DisallowedKeyError``.

    The actual write still goes through :func:`gui.env_io.write_setting` (which
    re-checks); this is a pre-flight guard so the panel can refuse a bad toggle
    before touching ``.env``.
    """
    from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, DisallowedKeyError, SecretWriteError  # noqa: PLC0415

    if key in SECRET_KEYS:
        raise SecretWriteError(
            f"{key} is a secret and can never be written from the GUI (CONSTRAINT #3)."
        )
    if key not in ALLOWED_KEYS:
        raise DisallowedKeyError(f"{key} is not a Control-Center-writable toggle.")


def opal_built() -> bool:
    """Convenience: is the Opal backend importable yet?"""
    return _module_available("llm.research")
