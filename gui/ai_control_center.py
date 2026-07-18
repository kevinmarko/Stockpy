"""
gui/ai_control_center.py — headless helpers for the AI Control Center tab.
==========================================================================

The Streamlit wiring lives in :func:`gui.panels.render_ai_control_center`.
This module hosts the pure logic it depends on so it is unit-testable WITHOUT
Streamlit — mirrors :mod:`gui.ai_insights_panel` / :mod:`gui.gravity_ai_panel`.

The Control Center is the single operator-facing surface for every AI option
on the platform. This module supplies:

* :data:`CAPABILITIES` — the registry of all AI options (analyst rationale
  commentary, alert commentary — both flexibly routed to Claude OR Gemini
  per LLM_COMMENTARY_RATIONALE_PROVIDER / LLM_COMMENTARY_ALERT_PROVIDER —
  Gemini chart vision, Gravity AI runner, Opal research), each described by
  an :class:`AICapability`.
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


CapabilityStatus = Literal["ready", "disabled", "missing_key", "invalid_key", "not_built"]


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
        The ``settings`` attribute name(s) that COULD hold the required
        provider API key, depending on which provider is configured. Used as
        a static fallback (ALL must be present) when
        ``provider_selector_setting`` is ``None``; otherwise informational
        only — the actual live requirement is resolved dynamically via
        ``provider_selector_setting`` (see below).
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
    provider_selector_setting :
        The ``settings`` attribute name holding an operator-chosen provider
        name (``"claude"`` | ``"gemini"`` | ``"none"``), when this capability
        supports flexible per-job routing (Tier 9 rationale/alert commentary
        — either provider may serve either job). When set, the REQUIRED key
        is resolved dynamically via :data:`_PROVIDER_KEY_MAP` from the LIVE
        value of this setting, rather than from the static
        ``provider_key_settings`` tuple. ``None`` = not flexible (Gravity
        runner, chart vision, Opal — each has a fixed provider).
    """

    key: str
    label: str
    enable_settings: Tuple[str, ...]
    provider_key_settings: Tuple[str, ...]
    module: Optional[str]
    trigger: str
    toggle_key: Optional[str]
    help: str
    provider_selector_setting: Optional[str] = None


# ---------------------------------------------------------------------------
# The registry — every AI option on the platform, in display order.
# ---------------------------------------------------------------------------
CAPABILITIES: Tuple[AICapability, ...] = (
    AICapability(
        key="claude_commentary",
        label="Analyst rationale commentary",
        enable_settings=("LLM_COMMENTARY_ENABLED", "LLM_COMMENTARY_RATIONALE_PROVIDER"),
        provider_key_settings=("ANTHROPIC_API_KEY", "GEMINI_API_KEY"),
        module="llm.commentary",
        trigger="on_demand",
        toggle_key="LLM_COMMENTARY_ENABLED",
        help="Per-symbol analyst 'why' note. On-demand button (Section B). "
             "Provider is operator-chosen (Claude or Gemini) via "
             "LLM_COMMENTARY_RATIONALE_PROVIDER.",
        provider_selector_setting="LLM_COMMENTARY_RATIONALE_PROVIDER",
    ),
    AICapability(
        key="gemini_alerts",
        label="Alert commentary",
        enable_settings=("LLM_COMMENTARY_ENABLED", "LLM_COMMENTARY_ALERT_PROVIDER"),
        provider_key_settings=("ANTHROPIC_API_KEY", "GEMINI_API_KEY"),
        module="llm.commentary",
        trigger="scheduled",
        toggle_key="LLM_COMMENTARY_ENABLED",
        help="Concise ntfy alert bodies. Fires automatically during a scheduled run "
             "(Section D). Provider is operator-chosen (Claude or Gemini) via "
             "LLM_COMMENTARY_ALERT_PROVIDER.",
        provider_selector_setting="LLM_COMMENTARY_ALERT_PROVIDER",
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
        label="Opal research agent",
        enable_settings=("OPAL_RESEARCH_ENABLED", "OPAL_RESEARCH_PROVIDER"),
        provider_key_settings=("OPENAI_API_KEY", "GEMINI_API_KEY"),
        module="llm.research",
        trigger="on_demand",
        toggle_key="OPAL_RESEARCH_ENABLED",
        help="Grounded research brief per symbol (front-of-pipeline). Provider is "
             "operator-chosen (OpenAI or Gemini) via OPAL_RESEARCH_PROVIDER.",
        provider_selector_setting="OPAL_RESEARCH_PROVIDER",
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


# Maps an operator-chosen provider name (the live value of a
# ``provider_selector_setting`` attribute) to the ``settings`` attribute
# holding that provider's API key.  Used by :func:`_keys_present` /
# :func:`_active_provider` to resolve flexible per-job routing (Tier 9
# rationale/alert commentary — either provider may serve either job).
_PROVIDER_KEY_MAP: Dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _active_provider(settings_obj: Any, cap: AICapability) -> Optional[str]:
    """Return the live provider choice for a flexible capability, or ``None``."""
    if not cap.provider_selector_setting:
        return None
    choice = str(getattr(settings_obj, cap.provider_selector_setting, "") or "").strip().lower()
    return choice or None


def _keys_present(settings_obj: Any, cap: AICapability) -> bool:
    """True iff the required provider key is present + non-empty.

    When ``provider_selector_setting`` is set, the requirement is resolved
    dynamically from the LIVE provider choice (either "claude" or "gemini"
    may serve the job) via :data:`_PROVIDER_KEY_MAP`. Otherwise falls back
    to the static ``provider_key_settings`` tuple (ALL must be present).
    """
    choice = _active_provider(settings_obj, cap)
    if choice is not None:
        key_name = _PROVIDER_KEY_MAP.get(choice)
        if key_name is None:
            # Unknown/"none" provider choice — no key requirement resolvable.
            return False
        return bool(getattr(settings_obj, key_name, None) or "")
    for k in cap.provider_key_settings:
        if not (getattr(settings_obj, k, None) or ""):
            return False
    return True


# Inverse of _PROVIDER_KEY_MAP: key-setting attribute -> provider name.
_KEY_ATTR_TO_PROVIDER: Dict[str, str] = {v: k for k, v in _PROVIDER_KEY_MAP.items()}


def _required_providers(settings_obj: Any, cap: AICapability) -> List[str]:
    """The provider name(s) whose API key this capability actually needs.

    For a flexible capability (``provider_selector_setting`` set) this is the
    single LIVE choice; otherwise it's every provider named by the static
    ``provider_key_settings`` tuple. This is the dual of :func:`_keys_present`,
    reused to resolve which last-call verdict is relevant to the capability.
    """
    choice = _active_provider(settings_obj, cap)
    if choice is not None:
        return [choice] if choice in _PROVIDER_KEY_MAP else []
    return [_KEY_ATTR_TO_PROVIDER[k] for k in cap.provider_key_settings if k in _KEY_ATTR_TO_PROVIDER]


def _invalid_provider(
    settings_obj: Any,
    cap: AICapability,
    last_calls: Optional[Dict[str, Dict[str, Any]]],
) -> Optional[str]:
    """The provider whose LAST REAL call was AUTH-rejected, or ``None``.

    ``last_calls`` is ``llm.status_store.read_all()``'s output, keyed by
    provider name. ONLY ``error_kind == "auth"`` on a current
    (``source == "last_call"``) record counts: a rate_limit / network / timeout
    / schema failure is a real problem worth surfacing as telemetry, but it is
    NOT evidence the key is wrong and must never render as ``invalid_key``.
    ANY required provider being auth-rejected marks the capability invalid (the
    dual of ``_keys_present``'s ALL-must-be-present).
    """
    if not last_calls:
        return None
    for provider in _required_providers(settings_obj, cap):
        lc = last_calls.get(provider)
        if not isinstance(lc, dict):
            continue
        if (
            lc.get("source") == "last_call"
            and lc.get("ok") is False
            and lc.get("error_kind") == "auth"
        ):
            return provider
    return None


def capability_status(
    settings_obj: Any,
    cap: AICapability,
    *,
    last_calls: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Classify a single capability's readiness.

    Returns a dict: ``{enabled, key_present, built, status, active_provider,
    invalid_provider}`` where ``status`` is one of ``ready`` / ``disabled`` /
    ``missing_key`` / ``invalid_key`` / ``not_built``.

    Ordering of the verdict (most-blocking first):
      1. ``not_built``    — backing module absent (e.g. Opal before its build).
      2. ``disabled``     — master switch off.
      3. ``missing_key``  — enabled but a provider key is unset.
      4. ``invalid_key``  — enabled, key present, but the last REAL call to that
         provider was auth-rejected (only reachable when ``last_calls`` is
         supplied). Mutually exclusive with ``missing_key`` by construction
         (that needs ``key_present=False``; this needs ``key_present=True``).
      5. ``ready``        — enabled + built + keys present + no auth rejection.

    ``last_calls`` (``llm.status_store.read_all()`` output) is OPTIONAL. When
    omitted the ``invalid_key`` state is unreachable and every input produces a
    byte-identical ``status`` to the pre-telemetry behavior — the additive
    contract the truth-table tests and Gravity step_86 check 4 rely on.
    """
    built = _module_available(cap.module)
    enabled = _is_enabled(settings_obj, cap)
    key_present = _keys_present(settings_obj, cap)
    invalid_provider = _invalid_provider(settings_obj, cap, last_calls) if key_present else None

    if not built:
        status: CapabilityStatus = "not_built"
    elif not enabled:
        status = "disabled"
    elif not key_present:
        status = "missing_key"
    elif invalid_provider is not None:
        status = "invalid_key"
    else:
        status = "ready"

    return {
        "enabled": bool(enabled),
        "key_present": bool(key_present),
        "built": bool(built),
        "status": status,
        "active_provider": _active_provider(settings_obj, cap),
        "invalid_provider": invalid_provider,
    }


def control_center_overview(
    settings_obj: Any,
    *,
    last_calls: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return one status row per capability, in display order.

    ``last_calls`` is threaded straight through to :func:`capability_status`;
    the caller (the panel or the API) reads ``llm.status_store.read_all()`` and
    passes it — this module never touches the store itself, so it stays
    Streamlit-free AND filesystem-free (testable cold with a bare
    ``SimpleNamespace``).
    """
    rows: List[Dict[str, Any]] = []
    for cap in CAPABILITIES:
        st = capability_status(settings_obj, cap, last_calls=last_calls)
        active = st["active_provider"]
        required_key = _PROVIDER_KEY_MAP.get(active) if active else None
        rows.append({
            "key": cap.key,
            "label": cap.label,
            "trigger": cap.trigger,
            "toggle_key": cap.toggle_key,
            "provider_selector_setting": cap.provider_selector_setting,
            "provider_keys": [required_key] if required_key else list(cap.provider_key_settings),
            **st,
        })
    return rows


STATUS_BADGE: Dict[str, str] = {
    "ready": "🟢 ready",
    "disabled": "⚪ disabled",
    "missing_key": "🟡 key missing",
    "invalid_key": "🔴 key rejected",
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
