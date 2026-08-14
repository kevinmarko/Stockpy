"""pilots/prompt_registry.py — read the Prompt Registry for the PWA.
================================================================================

Pure reader wrapping ``prompt_registry.registry.get_registry()`` (the module
Agent D fixed's ``os.environ`` -> ``settings`` bug in — this is the SAME
package, a different concern: this file never touches ``_build_registry_from_
settings()`` itself). Powers ``GET /prompts`` and ``GET /prompts/{id}`` on
``api/pilots_api.py``.

Design invariants (identical to the rest of the Pilots read layer —
``pilots/strategy_matrix.py``, ``pilots/options.py``, ``pilots/run_status.py``):

* **Dependency-light** — imports only ``settings`` + stdlib + the
  ``prompt_registry`` package itself (confirmed independently dependency-light
  by its own module docstrings: ``models``/``cache``/``guardrails``/``signing``/
  ``store``/``registry``/``__main__`` are all stdlib-only). Never imports
  ``gui.panels.prompt_registry`` — that module imports ``streamlit`` at its own
  module top, which this dependency-light reader must never pull onto the
  AST-guarded ``api/pilots_api.py`` import path. See
  ``tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light``
  for the enforced allowlist.

* **Never raises (CONSTRAINT #6)** — a disabled registry, an empty baseline
  directory, or any internal failure degrades to an honest empty/partial shape
  with a ``reason``, never an exception. Callers (the FastAPI endpoints) do not
  need their own try/except around these functions.

* **Honesty (CONSTRAINT #4)** — a prompt with no resolvable version anywhere
  (no pin, no manifest, no cache, no baseline) reports ``resolved_version:
  None`` / ``source: None``, never a fabricated version string.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["list_prompts", "get_prompt_body"]


def _resolve_source(reg: Any, prompt_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(resolved_version, source_label)`` for *prompt_id* WITHOUT
    invoking ``PromptRegistry.get()`` (which returns a sentinel string rather
    than ``None`` on total failure — unsuitable for a status listing).

    Independently re-implements ``gui/panels/prompt_registry.py::
    _pr_resolve_source``'s exact pin > remote-manifest > disk-cache > baseline
    precedence — duplicated, not imported, because that module imports
    ``streamlit`` at its own top (see module docstring). Returns
    ``(None, None)`` when nothing resolves for this ID at all.
    """
    pinned_ver = getattr(reg, "_pins", {}).get(prompt_id)
    if pinned_ver is not None:
        return pinned_ver, "pin"

    manifest = getattr(reg, "_manifest", None)
    if manifest is not None:
        ver_obj = manifest.prompts.get(prompt_id)
        if ver_obj is not None:
            return ver_obj.latest, "remote"

    cache = getattr(reg, "_cache", None)
    if cache is not None:
        try:
            versions = cache.list_versions(prompt_id)
        except Exception as exc:  # noqa: BLE001 — dead-letter, never fatal
            logger.debug("prompt_registry: cache.list_versions failed for %s: %s", prompt_id, exc)
            versions = []
        if versions:
            return versions[0], "cache"

    try:
        from prompt_registry.cache import read_baseline
        if read_baseline(prompt_id) is not None:
            return "baseline", "baseline"
    except Exception as exc:  # noqa: BLE001 — dead-letter, never fatal
        logger.debug("prompt_registry: read_baseline failed for %s: %s", prompt_id, exc)

    return None, None


def _cached_versions(reg: Any, prompt_id: str) -> List[str]:
    """All version strings cached on disk for *prompt_id*, newest first. ``[]``
    on any failure (never raises)."""
    cache = getattr(reg, "_cache", None)
    if cache is None:
        return []
    try:
        return list(cache.list_versions(prompt_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("prompt_registry: list_versions failed for %s: %s", prompt_id, exc)
        return []


def _all_known_ids(reg: Any) -> List[str]:
    """Union of IDs known to the registry: committed baseline + remote
    manifest (if a sync already ran this process) + pins. Mirrors
    ``prompt_registry.__main__._all_known_ids`` exactly (duplicated rather than
    imported to keep this reader's dependency surface self-documenting — the
    import itself would be safe, see module docstring, but the logic is a
    three-line pure set union not worth a cross-module call for)."""
    try:
        from prompt_registry.cache import list_baseline_ids
        ids: set = set(list_baseline_ids())
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_registry: list_baseline_ids failed: %s", exc)
        ids = set()
    manifest = getattr(reg, "_manifest", None)
    if manifest is not None:
        ids.update(manifest.prompts.keys())
    ids.update(getattr(reg, "_pins", {}).keys())
    return sorted(ids)


def _get_registry_or_none() -> Any:
    """``prompt_registry.registry.get_registry()``, or ``None`` on any import/
    construction failure (never raises — CONSTRAINT #6)."""
    try:
        from prompt_registry.registry import get_registry
        return get_registry()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_registry: get_registry() failed: %s", exc)
        return None


def list_prompts() -> Dict[str, Any]:
    """Every known prompt ID with its resolved version, source, pinned state,
    and cached-version count — the data behind the Prompt Registry screen's
    main table (ports ``gui/panels/prompt_registry.py``'s "Registered
    prompts" table).

    Returns
    -------
    dict
        ``{"enabled": bool, "prompts": [...], "reason": Optional[str]}``.
        ``enabled`` mirrors ``settings.PROMPT_REGISTRY_ENABLED`` — a disabled
        registry still lists every baseline ID (all resolve to ``source:
        "baseline"``), it just never attempted a remote fetch. Each
        ``prompts[]`` row: ``id`` / ``resolved_version`` (``None`` if
        unresolvable) / ``source`` (``"pin"``/``"remote"``/``"cache"``/
        ``"baseline"``/``None``) / ``pinned_version`` (``None`` if unpinned) /
        ``cached_version_count``. ``reason`` is non-``None`` only when the
        registry itself could not be constructed or no IDs are known at all
        (e.g. a corrupt/missing baseline directory) — never raises.
    """
    reg = _get_registry_or_none()
    if reg is None:
        return {
            "enabled": bool(settings.PROMPT_REGISTRY_ENABLED),
            "prompts": [],
            "reason": "Prompt Registry is unavailable (failed to construct).",
        }

    ids = _all_known_ids(reg)
    prompts: List[Dict[str, Any]] = []
    for pid in ids:
        version, source = _resolve_source(reg, pid)
        prompts.append(
            {
                "id": pid,
                "resolved_version": version,
                "source": source,
                "pinned_version": getattr(reg, "_pins", {}).get(pid),
                "cached_version_count": len(_cached_versions(reg, pid)),
            }
        )

    return {
        "enabled": bool(settings.PROMPT_REGISTRY_ENABLED),
        "prompts": prompts,
        "reason": (
            None
            if prompts
            else "No prompt IDs found — the committed baseline directory may be empty."
        ),
    }


def get_prompt_body(prompt_id: str, version: Optional[str] = None) -> Dict[str, Any]:
    """The resolved body for one prompt ID, either via the full resolution
    chain (``version=None``) or a specific version (manifest -> disk cache ->
    the ``"baseline"`` keyword).

    Returns
    -------
    dict
        ``{"id": str, "version": Optional[str], "found": bool,
        "body": Optional[str], "source": Optional[str], "reason":
        Optional[str], "cached_versions": List[str], "has_baseline": bool}``.
        ``version`` in the response is the RESOLVED version when ``found`` is
        True and no explicit version was requested (``None`` in, an actual
        version string out); it echoes the requested version when one was
        given. ``source`` is only ever populated for a full-resolution-chain
        lookup (``version=None``) — a specific-version lookup does not
        re-derive provenance. ``found=False`` is an honest, expected outcome
        (unknown ID, unknown version, or a total resolution failure), never an
        exception. ``cached_versions`` (newest first) and ``has_baseline`` are
        populated on EVERY call, regardless of ``found`` — a caller building a
        diff-version picker (e.g. the PWA's Prompt Registry screen) needs the
        full set of resolvable versions for this id up front, not just
        whichever single version this particular call resolved.
    """
    reg = _get_registry_or_none()
    if reg is None:
        return {
            "id": prompt_id,
            "version": version,
            "found": False,
            "body": None,
            "source": None,
            "reason": "Prompt Registry is unavailable (failed to construct).",
            "cached_versions": [],
            "has_baseline": False,
        }

    cached_versions = _cached_versions(reg, prompt_id)
    try:
        from prompt_registry.cache import read_baseline
        has_baseline = read_baseline(prompt_id) is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("prompt_registry: has_baseline check failed for %s: %s", prompt_id, exc)
        has_baseline = False

    if version:
        try:
            from prompt_registry.__main__ import _resolve_body_for_version
            body = _resolve_body_for_version(reg, prompt_id, version)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "prompt_registry: version resolution failed for %s@%s: %s",
                prompt_id, version, exc,
            )
            body = None
        if body is None:
            return {
                "id": prompt_id,
                "version": version,
                "found": False,
                "body": None,
                "source": None,
                "reason": (
                    f"Version {version!r} of {prompt_id!r} not found in the "
                    "manifest, disk cache, or committed baseline."
                ),
                "cached_versions": cached_versions,
                "has_baseline": has_baseline,
            }
        return {
            "id": prompt_id,
            "version": version,
            "found": True,
            "body": body,
            "source": None,
            "reason": None,
            "cached_versions": cached_versions,
            "has_baseline": has_baseline,
        }

    # Full resolution chain (pin -> remote latest -> disk cache -> baseline ->
    # sentinel). PromptRegistry.get() NEVER returns an empty string
    # (CONSTRAINT #4 on ITS side) but the sentinel is exactly the "nothing
    # resolved" signal this reader must translate into an honest found=False.
    try:
        body = reg.get(prompt_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt_registry: get(%s) raised: %s", prompt_id, exc)
        return {
            "id": prompt_id,
            "version": None,
            "found": False,
            "body": None,
            "source": None,
            "reason": "Resolution failed: internal error",
            "cached_versions": cached_versions,
            "has_baseline": has_baseline,
        }

    if not body or body.startswith("[PROMPT UNAVAILABLE"):
        return {
            "id": prompt_id,
            "version": None,
            "found": False,
            "body": None,
            "source": None,
            "reason": (
                f"No body available for {prompt_id!r} in the registry, cache, "
                "or committed baseline."
            ),
            "cached_versions": cached_versions,
            "has_baseline": has_baseline,
        }

    resolved_version, source = _resolve_source(reg, prompt_id)
    return {
        "id": prompt_id,
        "version": resolved_version,
        "found": True,
        "body": body,
        "source": source,
        "reason": None,
        "cached_versions": cached_versions,
        "has_baseline": has_baseline,
    }
