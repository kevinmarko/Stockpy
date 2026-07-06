"""
api/state_api.py
=================
STANDALONE, read-only FastAPI service (WS10) proving that this platform's
engine/UI boundary — persisted files (``output/state_snapshot.json``,
``output/heartbeat.txt``, ``output/risk_gate_blocks.jsonl``) plus
``quant_platform.db`` (read via ``transactions_store.TransactionsStore``) —
is real and sufficient to serve a future web/mobile frontend.

Deliberately NOT wired into the desktop shell, GUI, or any orchestrator. It
is an independent addition that only reads already-persisted state:

  - It NEVER imports engine/calculation modules (``processing_engine``,
    ``strategy_engine``, ``forecasting_engine``, ``macro_engine``, etc.).
  - It NEVER imports broker/execution modules (``execution/*``).
  - It only touches the filesystem (``settings.OUTPUT_DIR / "state_snapshot.json"``)
    and ``transactions_store.TransactionsStore`` (SQLite reads).

Run standalone:
    uvicorn api.state_api:app --port 8600

Endpoints:
  GET /health   -> liveness check for this API process (not the trading engine)
  GET /state    -> full parsed output/state_snapshot.json, or 404 if absent
  GET /signals  -> just the "signals" list from that same snapshot
  GET /trades   -> closed trades from TransactionsStore, or [] if none

CONSTRAINT #4 (never fabricate data): a missing snapshot returns a 404 with
a clear error body — it never returns a placeholder/synthetic snapshot.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from settings import settings
from transactions_store import TransactionsStore

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo State API (read-only)",
    description=(
        "Standalone, read-only view over the file-backed state this platform's "
        "engine and UI already communicate through. Foundation for a future "
        "web/mobile frontend — not wired into the desktop shell or any "
        "orchestrator."
    ),
    version="0.1.0",
)

_MISSING_SNAPSHOT_DETAIL = "No state snapshot yet — run the pipeline first."


def _state_snapshot_path() -> Path:
    """Resolve the state-snapshot path from live settings on every call so
    tests can monkeypatch ``settings.OUTPUT_DIR`` per-test without needing a
    module reload."""
    return settings.OUTPUT_DIR / "state_snapshot.json"


def _read_state_snapshot() -> Dict[str, Any] | None:
    """Read + parse output/state_snapshot.json. Returns None (never a
    fabricated placeholder) when the file is absent, unreadable, or invalid
    JSON — dead-letter resilient, matching the rest of this codebase's
    CONSTRAINT #6 (never crash on a missing/degraded file)."""
    path = _state_snapshot_path()
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - dead-letter: any read/parse failure degrades to None
        logger.warning("state_api: failed to read %s: %s", path, exc)
        return None


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness check for the API process itself, not the trading engine."""
    return {"status": "ok"}


@app.get("/state")
def get_state() -> Dict[str, Any]:
    """Return the full parsed contents of output/state_snapshot.json.

    404s with a clear JSON error body when the snapshot doesn't exist yet —
    never fabricates a placeholder snapshot (CONSTRAINT #4)."""
    snapshot = _read_state_snapshot()
    if snapshot is None:
        return JSONResponse(
            status_code=404,
            content={"detail": _MISSING_SNAPSHOT_DETAIL},
        )
    return snapshot


@app.get("/signals")
def get_signals() -> List[Any]:
    """Return just the ``signals`` field from output/state_snapshot.json —
    same key/shape already consumed by gui/panels/observability.py via
    ``load_state_snapshot().get("signals", [])``.

    404s with the same error body as /state when the snapshot is missing."""
    snapshot = _read_state_snapshot()
    if snapshot is None:
        return JSONResponse(
            status_code=404,
            content={"detail": _MISSING_SNAPSHOT_DETAIL},
        )
    return snapshot.get("signals", []) if isinstance(snapshot, dict) else []


@app.get("/trades")
def get_trades() -> List[Dict[str, Any]]:
    """Return closed trades from TransactionsStore as a JSON list of records.

    Returns an empty list (not an error) when the DB has no closed trades
    yet, or when the DB read fails outright — dead-letter resilient, never
    a raw 500 traceback."""
    try:
        store = TransactionsStore()
        df = store.closed_trades_df()
        if df is None or df.empty:
            return []
        # Timestamps -> ISO strings so the JSON encoder never chokes on
        # pandas/numpy datetime types.
        df = df.copy()
        for col in df.columns:
            if str(df[col].dtype).startswith("datetime"):
                df[col] = df[col].astype(str)
        return df.to_dict(orient="records")
    except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
        logger.warning("state_api: failed to read closed trades: %s", exc)
        return []
