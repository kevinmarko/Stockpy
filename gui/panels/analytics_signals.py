"""
gui/panels/analytics_signals.py
================================
Analytics-tab render helpers for three read-only signal/risk views owned by
PR2 Agent C. Imported and called by ``gui/panels/analytics.py`` (Agent B).

Public contract (FIXED — do not rename/resignature)
---------------------------------------------------
- ``render_ml_registry() -> None``            — ML model registry table.
- ``render_news_sentiment(snap) -> None``     — per-symbol FinBERT sentiment.
- ``render_slippage_covar(snap) -> None``     — realized slippage + CoVaR proxy.

Design rules (this codebase's GUI conventions)
----------------------------------------------
- Every file/DB/parse access is wrapped in ``try/except`` and degrades to an
  empty-state message — never a traceback into the UI (CONSTRAINT #6).
- Missing/``null`` values render "—" / are skipped — never a fabricated ``0.0``
  (CONSTRAINT #4).
- All the real logic lives in small pure helpers (``_load_registry_rows``,
  ``_sentiment_rows``, ``_risk_rows``) that are unit-testable outside a
  Streamlit runtime; the ``render_*`` wrappers only do presentation.
- Read-only: the sole file touched is the ``ml/registry.yaml`` YAML file. No
  DB writes, no order/execution code.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

# Repo root = three levels up from this file (gui/panels/analytics_signals.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Shared formatting helpers (pure)
# ---------------------------------------------------------------------------


def _fmt_metric(value: Any, fmt: str = "{:.4f}") -> str:
    """Format a numeric metric, degrading ``None``/``NaN``/non-numeric to "—".

    CONSTRAINT #4: a missing metric is "—", never a fabricated 0.
    """
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return fmt.format(f)


# ===========================================================================
# 1. ML model registry — reads ml/registry.yaml
# ===========================================================================

# Deployability gate context (from the registry file header):
#   deployable iff cpcv_dsr > 0.95 AND pbo < 0.50 (AND Gravity gates).
_DSR_GATE = 0.95
_PBO_GATE = 0.50


def _load_registry_rows(path: str = "ml/registry.yaml") -> List[Dict[str, Any]]:
    """Load + normalise ``ml/registry.yaml`` into a flat list of row dicts.

    Pure and testable: returns ``[]`` on ANY failure (missing file, unreadable,
    malformed YAML, unexpected shape) so the caller can render an info message
    instead of a traceback (CONSTRAINT #6). ``null`` metrics are preserved as
    ``None`` (the render layer maps them to "—", never 0 — CONSTRAINT #4).

    A relative ``path`` is resolved against the repo root so the helper works
    regardless of the process CWD; an absolute path is used as-is.

    Returns
    -------
    list[dict]
        One dict per model with keys: ``model``, ``role``, ``trained_date``,
        ``cpcv_dsr``, ``pbo``, ``n_train``, ``deployable``, ``notes``.
    """
    try:
        import yaml  # PyYAML — already a repo dependency.
    except Exception as exc:  # noqa: BLE001
        logger.debug("PyYAML unavailable for registry load: %s", exc)
        return []

    p = Path(path)
    if not p.is_absolute():
        p = _REPO_ROOT / p

    try:
        if not p.exists():
            return []
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.warning("Could not read/parse %s: %s", p, exc)
        return []

    if not isinstance(raw, dict):
        return []
    models = raw.get("models")
    if not isinstance(models, dict):
        return []

    rows: List[Dict[str, Any]] = []
    for name, meta in models.items():
        if not isinstance(meta, dict):
            # Skip malformed entries rather than fabricating fields.
            continue
        rows.append({
            "model": str(name),
            "role": meta.get("role"),
            "trained_date": meta.get("trained_date"),
            "cpcv_dsr": meta.get("cpcv_dsr"),
            "pbo": meta.get("pbo"),
            "n_train": meta.get("n_train"),
            "deployable": meta.get("deployable"),
            "notes": meta.get("notes"),
        })
    return rows


def render_ml_registry() -> None:
    """Render the ML model registry table from ``ml/registry.yaml``."""
    st.markdown("### 🤖 ML Model Registry")
    st.caption(
        "Production models tracked in `ml/registry.yaml` (updated by the monthly "
        "retraining job). A model is **deployable** only when "
        f"`cpcv_dsr > {_DSR_GATE}` **and** `pbo < {_PBO_GATE}` (plus Gravity gates). "
        "`null` metrics render `—` — never a fabricated 0."
    )

    rows = _load_registry_rows()
    if not rows:
        st.info("No ML model registry found (ml/registry.yaml).")
        return

    notes_by_model: Dict[str, str] = {}
    table_rows: List[Dict[str, Any]] = []
    for r in rows:
        model = r.get("model", "—")
        deployable = r.get("deployable")
        if deployable is True:
            deploy_str = "✅ Yes"
        elif deployable is False:
            deploy_str = "❌ No"
        else:
            deploy_str = "—"

        note = r.get("notes")
        if note:
            notes_by_model[str(model)] = str(note)

        table_rows.append({
            "Model": model,
            "Role": r.get("role") or "—",
            "Trained": r.get("trained_date") or "—",
            "CPCV DSR": _fmt_metric(r.get("cpcv_dsr")),
            "PBO": _fmt_metric(r.get("pbo")),
            "Deployable": deploy_str,
            "N-Train": _fmt_metric(r.get("n_train"), "{:.0f}"),
        })

    df = pd.DataFrame(
        table_rows,
        columns=["Model", "Role", "Trained", "CPCV DSR", "PBO", "Deployable", "N-Train"],
    )
    st.dataframe(df, width="stretch", hide_index=True)

    if notes_by_model:
        with st.expander("📝 Model notes"):
            for model, note in notes_by_model.items():
                st.markdown(f"**{model}** — {note}")


# ===========================================================================
# 2. News sentiment — per-symbol FinBERT from state snapshot
# ===========================================================================


def _sentiment_label(value: float) -> str:
    """Map a sentiment score in ~[-1, 1] to an emoji/label cue (pure)."""
    if value > 0.15:
        return "🟢 Positive"
    if value < -0.15:
        return "🔴 Negative"
    return "🟡 Neutral"


def _sentiment_rows(snap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract per-symbol news-sentiment rows from a state snapshot (pure).

    Each ``snap['signals']`` entry may carry a ``news_sentiment`` float in
    roughly ``[-1, 1]`` (or ``null``/absent when ``news_catalyst`` did not run,
    or on older snapshots that predate the key). Only symbols with a non-null,
    numeric value are returned; everything else is skipped (CONSTRAINT #4 — no
    fabricated neutral score for a symbol that simply has no news).

    Returns ``[]`` safely for ``None``, missing ``signals``, or any parse error.
    """
    if not isinstance(snap, dict):
        return []
    try:
        signals = snap.get("signals") or []
        rows: List[Dict[str, Any]] = []
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            val = sig.get("news_sentiment")
            if val is None:
                continue
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if f != f:  # NaN
                continue
            rows.append({
                "Symbol": str(sig.get("symbol", "—")),
                "News Sentiment": round(f, 3),
                "Signal": _sentiment_label(f),
            })
        return rows
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.debug("sentiment row extraction failed: %s", exc)
        return []


def render_news_sentiment(snap: Optional[Dict[str, Any]]) -> None:
    """Render a per-symbol FinBERT news-sentiment table from the snapshot."""
    st.markdown("### 📰 News Sentiment (FinBERT)")
    st.caption(
        "Per-symbol news-catalyst sentiment score (~-1 negative … +1 positive) "
        "from the latest state snapshot. Symbols with no scored news are omitted "
        "rather than shown as neutral."
    )

    rows = _sentiment_rows(snap)
    if not rows:
        st.info(
            "No news-sentiment data in the latest snapshot "
            "(news_catalyst may not have run)."
        )
        return

    df = pd.DataFrame(rows, columns=["Symbol", "News Sentiment", "Signal"])
    df = df.sort_values("News Sentiment", ascending=False)
    st.dataframe(df, width="stretch", hide_index=True)


# ===========================================================================
# 3. Realized slippage + CoVaR tail-risk — from state snapshot
# ===========================================================================


def _risk_rows(snap: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract per-symbol slippage / CoVaR rows from a state snapshot (pure).

    Each ``snap['signals']`` entry may carry ``realized_slippage`` and
    ``covar_proxy`` floats (or ``null``/absent — being populated by the
    snapshot writers this PR; older snapshots lack them). A symbol is included
    if EITHER value is present and numeric; the missing member renders "—"
    (CONSTRAINT #4). Symbols with neither value are skipped entirely.

    Returns ``[]`` safely for ``None``, missing ``signals``, or any parse error.
    """
    if not isinstance(snap, dict):
        return []

    def _num(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if f != f else f  # drop NaN

    try:
        signals = snap.get("signals") or []
        rows: List[Dict[str, Any]] = []
        for sig in signals:
            if not isinstance(sig, dict):
                continue
            slip = _num(sig.get("realized_slippage"))
            covar = _num(sig.get("covar_proxy"))
            if slip is None and covar is None:
                continue
            rows.append({
                "Symbol": str(sig.get("symbol", "—")),
                "Realized Slippage": slip,
                "CoVaR Proxy": covar,
            })
        return rows
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.debug("risk row extraction failed: %s", exc)
        return []


def render_slippage_covar(snap: Optional[Dict[str, Any]]) -> None:
    """Render per-symbol realized-slippage + CoVaR tail-risk table."""
    st.markdown("### 📉 Realized Slippage & CoVaR Tail Risk")
    st.caption(
        "**Realized Slippage** — the execution cost gap between the decision "
        "price and the actual fill (higher = more costly execution).  "
        "**CoVaR Proxy** — a tail-dependency measure: how much a symbol's tail "
        "loss co-moves with a portfolio/market tail event (higher = more "
        "systemic tail risk). `—` = not available in the latest snapshot."
    )

    rows = _risk_rows(snap)
    if not rows:
        st.info(
            "No realized-slippage / CoVaR data in the latest snapshot."
        )
        return

    df = pd.DataFrame(rows, columns=["Symbol", "Realized Slippage", "CoVaR Proxy"])
    # Present with "—" for the null member of an otherwise-present row.
    display = df.copy()
    display["Realized Slippage"] = display["Realized Slippage"].map(
        lambda v: _fmt_metric(v)
    )
    display["CoVaR Proxy"] = display["CoVaR Proxy"].map(lambda v: _fmt_metric(v))
    st.dataframe(display, width="stretch", hide_index=True)
