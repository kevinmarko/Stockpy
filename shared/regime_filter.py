"""
gui/regime_filter.py
====================
Pure, dependency-light helpers implementing the cross-tab macro-**regime
filter** that the sidebar in ``gui/app.py`` exposes but which — until now — no
panel actually consumed.

The public surface is deliberately small and Streamlit-free so it is trivially
unit-testable and reusable:

* :func:`apply_regime_filter` — filter a list of per-signal dicts to those whose
  macro regime matches a selected regime.  ``None`` / ``"All"`` / ``"All
  regimes"`` (case-insensitive) is a **no-op pass-through** returning the exact
  same object, so the default sidebar selection reproduces today's behavior
  byte-for-byte.
* :func:`filter_snapshot` — snapshot-aware wrapper that filters the ``signals``
  list inside a ``state_snapshot.json`` dict, falling back to the snapshot's
  top-level ``market_regime`` for signals that carry no per-row regime key (the
  advisory writer, ``reporting/state_snapshot.py``, emits a single market-wide
  regime rather than a per-symbol ``macro_status``).

Everything here is dead-letter safe (CONSTRAINT #6): malformed / non-list /
``None`` inputs are returned unchanged rather than raising, because a GUI
convenience filter must never take down a panel.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# The two state-snapshot writers spell the per-signal regime differently:
#   * main_orchestrator._write_state_snapshot -> per-signal "macro_status"
#   * reporting/state_snapshot.write_state_snapshot -> no per-signal key; a
#     single top-level "market_regime" (handled by filter_snapshot's fallback).
# We probe a small ordered set of candidate keys so either writer's shape works.
_SIGNAL_REGIME_KEYS = ("macro_status", "market_regime", "regime")

# Sentinel selections that mean "do not filter".  Compared case-insensitively.
_ALL_SENTINELS = frozenset({"", "all", "all regimes", "any", "*"})

#: Canonical label used by the sidebar selectbox for the no-filter option.
ALL_REGIMES_LABEL = "All regimes"


def is_all_regimes(regime: Optional[str]) -> bool:
    """Return ``True`` when *regime* means "show everything" (no filtering).

    ``None`` and any of the :data:`_ALL_SENTINELS` (case-insensitive) count as
    "all".  Anything else is treated as a concrete regime selection.
    """
    if regime is None:
        return True
    try:
        return str(regime).strip().lower() in _ALL_SENTINELS
    except Exception:  # noqa: BLE001 - never raise from a predicate
        return True


def _signal_regime(sig: Dict[str, Any]) -> str:
    """Extract a signal's macro regime (upper-cased, stripped) or ``""``."""
    for key in _SIGNAL_REGIME_KEYS:
        val = sig.get(key)
        if val:
            text = str(val).strip()
            if text:
                return text.upper()
    return ""


def apply_regime_filter(
    signals: Any,
    regime: Optional[str],
    *,
    default_regime: Optional[str] = None,
) -> Any:
    """Return the subset of *signals* whose macro regime matches *regime*.

    Parameters
    ----------
    signals:
        A list of per-signal dicts (the ``signals`` array from a
        ``state_snapshot.json``).  Any non-list input (``None``, a dict, …) is
        returned unchanged — dead-letter safe.
    regime:
        The selected regime.  ``None`` / ``"All"`` / ``"All regimes"`` (and the
        other :data:`_ALL_SENTINELS`) → **identical object returned** (no-op).
    default_regime:
        Optional market-wide regime used as the per-signal fallback when a
        signal dict carries none of :data:`_SIGNAL_REGIME_KEYS` (e.g. the
        advisory snapshot writer, which stores only a top-level
        ``market_regime``).  When ``None`` a signal with no regime key never
        matches a concrete selection.

    Notes
    -----
    * When *regime* is a concrete selection, a **new list** is returned; the
      input list is never mutated.
    * When *regime* means "all", the *exact same object* is returned so callers
      can rely on identity to detect the no-op path.
    """
    if is_all_regimes(regime):
        return signals
    if not isinstance(signals, list):
        return signals

    target = str(regime).strip().upper()
    fallback = (
        str(default_regime).strip().upper()
        if default_regime not in (None, "")
        else ""
    )

    matched: List[Any] = []
    for sig in signals:
        if not isinstance(sig, dict):
            # Non-dict entries can't carry a regime; drop them under a concrete
            # filter rather than raising.
            continue
        sig_regime = _signal_regime(sig) or fallback
        if sig_regime == target:
            matched.append(sig)
    return matched


def filter_snapshot(snapshot: Any, regime: Optional[str]) -> Any:
    """Return *snapshot* with its ``signals`` list regime-filtered.

    Snapshot-aware wrapper around :func:`apply_regime_filter`:

    * ``None`` / non-dict snapshots and the "all regimes" selection return the
      snapshot **unchanged** (identity — the behavior-preserving default).
    * Otherwise a **shallow copy** is returned with a filtered ``signals`` list,
      so the (possibly ``@st.cache_data``-owned) original dict is never mutated.
    * The snapshot's top-level ``market_regime`` is threaded through as the
      per-signal fallback so advisory-mode snapshots (which have no per-signal
      regime key) filter correctly against the run's single market regime.
    """
    if is_all_regimes(regime):
        return snapshot
    if not isinstance(snapshot, dict):
        return snapshot

    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return snapshot

    snapshot_regime = snapshot.get("market_regime")
    filtered = apply_regime_filter(
        signals, regime, default_regime=snapshot_regime
    )
    if filtered is signals:  # nothing changed — preserve identity
        return snapshot

    new_snapshot = dict(snapshot)
    new_snapshot["signals"] = filtered
    return new_snapshot
