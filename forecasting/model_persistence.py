"""
Forecast Model Persistence (2026-07, PR C hot-path)
====================================================
Generic, dependency-light save/load/staleness helpers for the CNN-LSTM and
Prophet forecasters in ``forecasting_engine.py``. Both models were previously
retrained from scratch on EVERY ``generate_forecast()`` call, per ticker, per
cycle -- by far the dominant CPU cost in the pipeline (the CNN-LSTM docstring
itself flags this as unable to scale). This module splits train from
inference exactly like ``regime/hmm_regime.py``'s ``HMMRegimeDetector``
already does: fit only on a retrain cadence, otherwise load the cached
artifact and just call ``.predict()``.

Design goals
------------
* **Dead-letter resilient** (CONSTRAINT #6): every public function wraps its
  body in try/except and degrades to ``None`` / ``False`` -- a missing,
  corrupt, or unreadable artifact NEVER raises; the caller falls back to a
  fresh fit exactly as if persistence were disabled.
* **One artifact per ticker** (not a dated history like ``ml/lgbm_ranker.py``
  or ``ml/meta_labeling.py``): only the most recent fit matters for a live
  forecaster, so each ticker gets a fixed filename that is simply
  overwritten on every retrain. Staleness is the file's mtime, not a
  timestamp embedded in the filename -- simpler than the glob-latest
  convention used by the per-signal ML models, and avoids unbounded artifact
  growth across thousands of cycles.
* Callers are responsible for the actual (de)serialization of a specific
  object type (``pickle.dump``/``pickle.load`` for scalers/Prophet models,
  ``model.save()``/``tf.keras.models.load_model()`` for Keras models) --
  this module only owns path construction and the staleness decision.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ForecastModelPersistence")

# One shared directory for all persisted forecaster artifacts, alongside the
# existing per-signal ML model artifacts convention (ml/models/*.pkl).
MODELS_DIR = Path(__file__).resolve().parent.parent / "ml" / "models" / "forecast_cache"


def _safe_ticker(ticker: str) -> str:
    """Uppercase + strip anything that isn't filename-safe (defense in depth;
    tickers are already validated upstream, this is a belt-and-suspenders
    guard against a stray '/' or '..' reaching a filesystem path)."""
    return "".join(c for c in str(ticker).upper() if c.isalnum() or c in ("-", "_")) or "UNKNOWN"


def artifact_path(prefix: str, ticker: str, suffix: str) -> Path:
    """Fixed (non-dated) path for a ticker's persisted artifact.

    e.g. ``artifact_path("cnn_lstm", "AAPL", ".keras")`` ->
    ``ml/models/forecast_cache/cnn_lstm_AAPL.keras``.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MODELS_DIR / f"{prefix}_{_safe_ticker(ticker)}{suffix}"


def is_fresh(path: Path, retrain_days: int) -> bool:
    """True iff ``path`` exists and its mtime is within ``retrain_days``.

    Never raises: any OSError (permission, race with a concurrent writer,
    the file vanishing between an existence check and stat) is treated as
    "not fresh" so the caller safely falls back to a retrain.
    """
    try:
        if not path.exists():
            return False
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds < max(0, int(retrain_days)) * 86400.0
    except OSError as exc:  # noqa: BLE001 - dead-letter, never raise into the forecast path
        logger.debug("is_fresh() stat failed for %s: %s", path, exc)
        return False


def touch(path: Path) -> None:
    """Best-effort mtime bump (used after a successful save so the freshness
    clock starts from the save, not a stale pre-existing mtime on some
    filesystems that don't update mtime reliably on rewrite)."""
    try:
        path.touch(exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.debug("touch() failed for %s: %s", path, exc)
