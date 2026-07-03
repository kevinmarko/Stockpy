from __future__ import annotations

from __future__ import annotations
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import streamlit as st
from settings import settings
from gui import env_io, orchestrator_runner, help_widgets
from gui.symbol_search import filter_by_symbol
from gui.orchestrator_runner import StageStatus
from gui.panels._shared import (  # noqa: E402
    GICS_SECTORS,
    _BF_EDITOR_COLUMNS,
    _REPO_ROOT,
    _active_symbols,
    _held_symbols,
    _kill_switch,
    _signal_symbols,
    _watchlist_symbols,
    load_block_log,
    logger,
)


def _pr_source_badge(source: str) -> str:
    """Return a one-word emoji badge describing where a resolved prompt came from."""
    return {
        "pin": "📌 pin",
        "remote": "🌐 remote",
        "cache": "💾 cache",
        "baseline": "📦 baseline",
    }.get(source, source)



def _pr_resolve_source(reg, prompt_id: str) -> Tuple[str, str]:
    """Return ``(resolved_version, source_label)`` for *prompt_id* without calling get().

    Used by the status table to display metadata without echoing the full body.
    The logic mirrors PromptRegistry._resolve_chain() but stops at the first hit
    and returns a label rather than the body — so it is safe to call for every row.
    """
    # Pin
    pinned_ver = getattr(reg, "_pins", {}).get(prompt_id)
    if pinned_ver is not None:
        return pinned_ver, "pin"
    # Remote manifest (already fetched into reg._manifest by a prior sync())
    manifest = getattr(reg, "_manifest", None)
    if manifest is not None:
        ver_obj = manifest.prompts.get(prompt_id)
        if ver_obj is not None:
            return ver_obj.latest, "remote"
    # Disk cache — newest version
    cache = getattr(reg, "_cache", None)
    if cache is not None:
        try:
            versions = cache.list_versions(prompt_id)
            if versions:
                return versions[-1], "cache"
        except Exception:
            pass
    # Baseline
    try:
        from prompt_registry.cache import read_baseline
        if read_baseline(prompt_id) is not None:
            return "baseline", "baseline"
    except Exception:
        pass
    return "—", "unknown"



def _pr_cached_versions(reg, prompt_id: str) -> List[str]:
    """Return all version strings cached on disk for *prompt_id*, sorted ascending."""
    cache = getattr(reg, "_cache", None)
    if cache is None:
        return []
    try:
        return list(cache.list_versions(prompt_id))
    except Exception:
        return []



def _pr_body_for_version(reg, prompt_id: str, version: str) -> Optional[str]:
    """Resolve a specific version body (baseline keyword supported)."""
    try:
        from prompt_registry.__main__ import _resolve_body_for_version
        return _resolve_body_for_version(reg, prompt_id, version)
    except Exception:
        return None



@st.cache_data(ttl=60)
def _pr_all_known_ids(enabled: bool) -> List[str]:
    """Return sorted union of baseline IDs + manifest IDs + pinned IDs.

    Cached for 60 s to avoid re-importing the registry on every widget interaction.
    The ``enabled`` arg is a cache-invalidation key so a Sync can bust the cache.
    """
    try:
        from prompt_registry import get_registry, list_baseline_ids
        reg = get_registry()
        ids: set[str] = set(list_baseline_ids())
        manifest = getattr(reg, "_manifest", None)
        if manifest is not None:
            ids.update(manifest.prompts.keys())
        ids.update(getattr(reg, "_pins", {}).keys())
        return sorted(ids)
    except Exception:
        return []


# ===========================================================================
# Tab 13 — AI Insights (Tier 9 Scope 3)
# ===========================================================================



def render_prompt_registry() -> None:
    """Prompt Registry — version control for every AI-facing instruction.

    Displays the resolved version + source for each registered prompt ID,
    provides a 🔄 Sync button (calls ``PromptRegistry.sync()`` once on-demand),
    a per-ID diff viewer (select two versions to compare), and an ↩ Rollback
    button that writes the rolled-back pin into ``.env`` via the allowlist-bounded
    :mod:`gui.env_io` writer (effective on the **next** orchestrator launch — the
    running process is never hot-swapped).

    **Security banner** (always rendered):
    "Prompts are advisory text; safety gates are enforced in code and are not
    registry-controlled."

    Design constraints honoured
    ---------------------------
    - CONSTRAINT #3: secrets are never displayed; pins are written via
      ``gui.env_io.write_setting`` (``PROMPT_REGISTRY_PINS`` is in ``ALLOWED_KEYS``).
    - CONSTRAINT #4: resolved bodies are never fabricated; a missing body shows
      the baseline (or "unavailable").
    - CONSTRAINT #5: sync is on-demand only; never called on a timer.
    - CONSTRAINT #6: every network/parse failure degrades gracefully via the
      registry's own fail-closed resolution chain.
    """
    st.subheader("📝 Prompt Registry")

    # ── Mandatory security banner ─────────────────────────────────────────
    st.info(
        "**Prompts are advisory text.** "
        "The registry changes what the AI is *told* — it cannot change what the "
        "platform is *permitted to do*. "
        "Order submission, the advisory quarantine, the risk gate, and the kill "
        "switch are enforced in Python and are **not** registry-controlled.",
        icon="🛡️",
    )

    # ── Lazy import: prompt_registry may not be configured ────────────────
    try:
        from prompt_registry import get_registry, reset_registry, list_baseline_ids
        from prompt_registry.registry import PromptRegistry
    except ImportError as exc:
        st.error(f"prompt_registry package not importable: {exc}")
        return

    reg: PromptRegistry = get_registry()
    is_enabled: bool = getattr(settings, "PROMPT_REGISTRY_ENABLED", False)

    # ── Enabled/disabled banner ───────────────────────────────────────────
    if not is_enabled:
        st.warning(
            "Registry is **disabled** (`PROMPT_REGISTRY_ENABLED=false` in `.env`). "
            "All prompts resolve from the committed baseline — zero network calls. "
            "Set `PROMPT_REGISTRY_ENABLED=true` to enable remote fetch and cache.",
            icon="📦",
        )

    # ── Top action strip: Sync + registry version ─────────────────────────
    col_sync, col_status = st.columns([1, 3])
    with col_sync:
        do_sync = st.button(
            "🔄 Sync prompts",
            type="primary",
            disabled=not is_enabled,
            help=(
                "Fetch the remote manifest, verify every version signature, and "
                "pre-warm the disk cache. On-demand only (CONSTRAINT #5)."
            ),
            width="stretch",
        )
    with col_status:
        manifest = getattr(reg, "_manifest", None)
        if manifest is not None:
            st.caption(
                f"Manifest version: `{manifest.registry_version}` · "
                f"signing alg: `{manifest.signing_alg}`"
            )
        else:
            st.caption("No manifest loaded yet — click **🔄 Sync prompts** to fetch.")

    if do_sync:
        if not is_enabled:
            st.warning("Enable the registry first (`PROMPT_REGISTRY_ENABLED=true`).")
        else:
            with st.spinner("Syncing remote manifest…"):
                try:
                    reg.sync()
                    _pr_all_known_ids.clear()  # bust the ID cache
                    st.success(
                        f"Sync complete. "
                        f"Manifest: `{getattr(reg._manifest, 'registry_version', '?')}`"
                    )
                except Exception as exc:
                    st.error(f"Sync failed (registry fell back to cache/baseline): {exc}")

    st.divider()

    # ── Prompt status table ───────────────────────────────────────────────
    st.markdown("#### Registered prompts")
    all_ids = _pr_all_known_ids(is_enabled)
    if not all_ids:
        st.info("No prompt IDs found. Run a Sync or check that `prompt_registry/baseline/` is intact.")
        return

    rows = []
    for pid in all_ids:
        ver, src = _pr_resolve_source(reg, pid)
        pinned = getattr(reg, "_pins", {}).get(pid, "—")
        cached = _pr_cached_versions(reg, pid)
        rows.append({
            "Prompt ID": pid,
            "Resolved version": ver,
            "Source": _pr_source_badge(src),
            "Pinned": pinned if pinned != "—" else "—",
            "Cached versions": len(cached),
        })

    st.dataframe(
        pd.DataFrame(rows).set_index("Prompt ID"),
        width="stretch",
    )

    st.divider()

    # ── Per-ID detail expander ────────────────────────────────────────────
    selected_id = st.selectbox(
        "Inspect a prompt ID",
        options=["(select…)"] + all_ids,
        key="pr_selected_id",
    )
    if selected_id == "(select…)":
        return

    ver, src = _pr_resolve_source(reg, selected_id)
    cached_versions = _pr_cached_versions(reg, selected_id)
    has_baseline = bool(_pr_body_for_version(reg, selected_id, "baseline"))
    version_choices = (["baseline"] if has_baseline else []) + cached_versions

    # ── 1. View current resolved body ─────────────────────────────────────
    with st.expander(f"👁️ View resolved body  ·  {_pr_source_badge(src)}  ·  `{ver}`", expanded=False):
        try:
            body = reg.get(selected_id)
            if body and not body.startswith("[PROMPT UNAVAILABLE"):
                st.code(body, language="markdown")
            else:
                st.warning(f"Prompt unavailable for `{selected_id}`. Sentinel returned.")
        except Exception as exc:
            st.error(f"Could not resolve body: {exc}")

    # ── 2. Unified diff viewer ─────────────────────────────────────────────
    with st.expander("🔍 Diff two versions", expanded=False):
        if len(version_choices) < 2:
            st.info(
                "Need at least 2 versions to diff (baseline + one cached, or two cached). "
                "Sync to populate the cache."
            )
        else:
            diff_col_a, diff_col_b = st.columns(2)
            with diff_col_a:
                ver_a = st.selectbox(
                    "Version A (from)",
                    options=version_choices,
                    key="pr_diff_ver_a",
                )
            with diff_col_b:
                ver_b = st.selectbox(
                    "Version B (to)",
                    options=version_choices,
                    index=min(1, len(version_choices) - 1),
                    key="pr_diff_ver_b",
                )
            if st.button("Compare", key="pr_diff_btn"):
                body_a = _pr_body_for_version(reg, selected_id, ver_a)
                body_b = _pr_body_for_version(reg, selected_id, ver_b)
                if body_a is None:
                    st.error(f"Version `{ver_a}` not found.")
                elif body_b is None:
                    st.error(f"Version `{ver_b}` not found.")
                else:
                    import difflib
                    diff_lines = list(
                        difflib.unified_diff(
                            body_a.splitlines(keepends=True),
                            body_b.splitlines(keepends=True),
                            fromfile=f"{selected_id}@{ver_a}",
                            tofile=f"{selected_id}@{ver_b}",
                        )
                    )
                    if diff_lines:
                        st.code("".join(diff_lines), language="diff")
                    else:
                        st.success("No differences between the two versions.")

    # ── 3. Rollback / pin control ─────────────────────────────────────────
    with st.expander("↩ Rollback / pin", expanded=False):
        st.caption(
            "Pins take effect on the **next** orchestrator launch. "
            "The running process is never hot-swapped. "
            "Written to `.env` via the allowlist-bounded `gui.env_io` writer."
        )

        current_pin = getattr(reg, "_pins", {}).get(selected_id)
        if current_pin:
            st.info(f"Currently pinned to: `{current_pin}`")
        else:
            st.caption("No pin set — resolves to remote latest or cache.")

        pin_col, rb_col = st.columns(2)

        # Manual pin to a specific version
        with pin_col:
            if version_choices:
                pin_target = st.selectbox(
                    "Pin to version",
                    options=version_choices,
                    key="pr_pin_target",
                )
                if st.button("📌 Set pin", key="pr_set_pin", width="stretch"):
                    body_check = _pr_body_for_version(reg, selected_id, pin_target)
                    if body_check is None:
                        st.error(f"Version `{pin_target}` not found; pin not set.")
                    else:
                        reg._pins[selected_id] = pin_target
                        try:
                            import json
                            pins_json = json.dumps(dict(sorted(reg._pins.items())))
                            env_io.write_setting("PROMPT_REGISTRY_PINS", pins_json)
                            st.success(
                                f"Pinned `{selected_id}` → `{pin_target}`. "
                                "Saved to `.env`; effective on next launch."
                            )
                        except env_io.SecretWriteError as exc:
                            st.error(f"Secret write blocked: {exc}")
                        except env_io.DisallowedKeyError as exc:
                            st.warning(
                                f"Pin set in-memory but `.env` write failed "
                                f"(PROMPT_REGISTRY_PINS not in ALLOWED_KEYS yet): {exc}"
                            )
                        except Exception as exc:
                            st.warning(
                                f"Pin set in-memory but `.env` write failed: {exc}"
                            )
            else:
                st.info("No versions available to pin.")

        # Auto-rollback to previous cached version
        with rb_col:
            st.markdown("**Auto-rollback**")
            st.caption("Repoints the pin to the previous cached version.")
            if st.button("↩ Rollback", key="pr_rollback", width="stretch"):
                try:
                    ok = reg.rollback(selected_id)
                    if ok:
                        new_pin = reg._pins.get(selected_id)
                        try:
                            import json
                            pins_json = json.dumps(dict(sorted(reg._pins.items())))
                            env_io.write_setting("PROMPT_REGISTRY_PINS", pins_json)
                            st.success(
                                f"Rolled back `{selected_id}` → `{new_pin}`. "
                                "Saved to `.env`; effective on next launch."
                            )
                        except env_io.SecretWriteError as exc:
                            st.error(f"Secret write blocked: {exc}")
                        except Exception as exc:
                            st.warning(
                                f"Rolled back in-memory but `.env` write failed: {exc}"
                            )
                    else:
                        st.warning(
                            f"No older cached version found for `{selected_id}`. "
                            "Sync to populate the cache with more versions."
                        )
                except Exception as exc:
                    st.error(f"Rollback failed: {exc}")

        # Clear pin
        if current_pin:
            if st.button("🗑️ Clear pin", key="pr_clear_pin"):
                reg._pins.pop(selected_id, None)
                try:
                    import json
                    pins_json = json.dumps(dict(sorted(reg._pins.items())))
                    env_io.write_setting("PROMPT_REGISTRY_PINS", pins_json)
                    st.success(
                        f"Pin for `{selected_id}` cleared. "
                        "Will resolve to remote latest on next launch."
                    )
                except Exception as exc:
                    st.warning(f"Pin cleared in-memory but `.env` write failed: {exc}")


def utcnow_str() -> str:
    """UTC timestamp string for footer display."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")



def utcnow_str() -> str:
    """UTC timestamp string for footer display."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

