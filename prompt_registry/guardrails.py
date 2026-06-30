"""
prompt_registry/guardrails.py
==============================
Authorization-boundary validator for prompt bodies.

``validate_prompt()`` is called on **every** prompt before it may enter the
resolution chain — even a correctly signed one.  A bad signature is discarded
upstream in ``registry.py``; a signed-but-dangerous body must be rejected here.

This module enforces the **hard boundary** stated in
``docs/PROMPT_REGISTRY_PLAN.md`` §4.3 / §0:

    *"A fetched prompt can change what an AI is told.
      It cannot change what the platform is permitted to do."*

Rejection causes the caller to fall through to the last known-good version or
the committed baseline — never empty (CONSTRAINT #4).

Checks (in order)
-----------------
1. **Empty** — a blank body is a data error, always reject.
2. **Size bound** — bodies larger than ``max_chars`` (default 50 000) are
   rejected; extremely large bodies are a denial-of-service vector.
3. **Deny-list** — case-insensitive scan for phrases that would instruct an AI
   to disable a platform safety gate.  This is the structural defense against a
   malicious or careless prompt that attempts to talk past code-level guards.
4. **Required markers** — per-prompt-id mandatory content.  Ensures a heavily
   edited body still contains the context that makes the prompt safe to use
   (e.g., every ``master_preprompt`` version must name ``ADVISORY_ONLY``; every
   Gravity step must still demand JSON output).

Module constants
----------------
``_DENY_LIST``         — tuple of banned case-insensitive substrings.
``_REQUIRED_MARKERS``  — dict mapping prompt id → required substring.
``_DEFAULT_MAX_CHARS`` — default body size ceiling (``settings.PROMPT_MAX_CHARS``).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard limits
# ---------------------------------------------------------------------------

_DEFAULT_MAX_CHARS: int = 50_000
"""Default maximum body length in characters (mirrors ``settings.PROMPT_MAX_CHARS``)."""

# ---------------------------------------------------------------------------
# Deny-list: phrases that, if present (case-insensitive), trigger rejection.
# Each entry maps to an attempt to disable a platform safety control via
# instructional text.  The list is intentionally narrow — we target the
# *specific* bypass instructions rather than generic "security" keywords so
# legitimate discussion of these topics in doc-style prompts is allowed.
# ---------------------------------------------------------------------------

_DENY_LIST: Tuple[str, ...] = (
    # Attempts to lift the advisory quarantine via text
    "ADVISORY_ONLY=false",
    "advisory_only = false",
    "advisory_only= false",
    # Order-submission bypass attempts — match function-call syntax so that audit
    # prompts referencing method *names* (e.g. "OrderManager.submit_order_with_idempotency")
    # are not falsely rejected; only direct call instructions ("submit_order(...)") are blocked.
    "submit_order(",
    "place_order(",
    # Kill-switch manipulation
    "disable the kill switch",
    "deactivate the kill switch",
    # Risk-gate bypass
    "bypass the risk gate",
    "ignore the risk gate",
    # Generic prompt-injection openers
    "ignore previous safety",
    "ignore all previous instructions",
    "disregard previous instructions",
    "override safety",
    "override all safety",
    # Direct code-execution injections
    "eval(",
    "exec(",
    "__import__(",
)

# ---------------------------------------------------------------------------
# Required markers: per-prompt-id substrings that MUST be present.
# A prompt that removes these is considered structurally broken — it either
# strips advisory context (master_preprompt) or removes the output contract
# (Gravity steps).
# ---------------------------------------------------------------------------

_REQUIRED_MARKERS: Dict[str, str] = {
    # Every master pre-prompt version must explicitly name the advisory quarantine.
    "master_preprompt": "ADVISORY_ONLY",
    # Gravity system prompt must preserve the JSON output contract.
    "gravity.system": "JSON",
}

# Every Gravity step must still demand "Respond in JSON" output.
for _step_n in range(1, 8):
    _REQUIRED_MARKERS[f"gravity.step_{_step_n:02d}"] = "Respond in JSON"
del _step_n  # clean up loop variable


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_prompt(
    prompt_id: str,
    body: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> Tuple[bool, List[str]]:
    """Validate a prompt body before it may enter the resolution chain.

    This runs on **every** candidate body — local baseline, signed remote
    version, and cached version alike.

    Parameters
    ----------
    prompt_id:
        The registry key (e.g. ``"master_preprompt"``, ``"gravity.step_01"``).
        Used to look up per-id required markers.
    body:
        The raw prompt text to validate.
    max_chars:
        Override the default size ceiling (useful in tests; normally matches
        ``settings.PROMPT_MAX_CHARS``).

    Returns
    -------
    (ok, issues) : tuple[bool, list[str]]
        *ok* is ``True`` only when every check passes.
        *issues* is the list of human-readable rejection reasons.

    Notes
    -----
    The caller (``registry.py``) is responsible for logging a CRITICAL alert
    via ``observability.alerts.send_alert`` when ``ok`` is ``False``.  This
    function only logs at WARNING so it remains usable headlessly in tests.
    """
    issues: List[str] = []

    # ── Check 1: empty body ──────────────────────────────────────────────────
    if not body or not body.strip():
        issues.append("body is empty or whitespace-only")
        # No point running further checks on an empty string.
        return False, issues

    # ── Check 2: size bound ──────────────────────────────────────────────────
    if len(body) > max_chars:
        issues.append(
            f"body too large: {len(body):,} chars exceeds {max_chars:,} limit"
        )

    # ── Check 3: deny-list (case-insensitive) ────────────────────────────────
    body_lower = body.lower()
    for phrase in _DENY_LIST:
        if phrase.lower() in body_lower:
            issues.append(f"deny-list phrase detected: {phrase!r}")

    # ── Check 4: required markers (per-id) ───────────────────────────────────
    required_marker = _REQUIRED_MARKERS.get(prompt_id)
    if required_marker and required_marker.lower() not in body_lower:
        issues.append(
            f"required marker missing for {prompt_id!r}: "
            f"expected to find {required_marker!r}"
        )

    ok = len(issues) == 0
    if not ok:
        logger.warning(
            "Prompt validation FAILED for %r — %d issue(s): %s",
            prompt_id,
            len(issues),
            "; ".join(issues),
        )
    return ok, issues
