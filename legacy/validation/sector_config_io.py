"""I/O and derivation layer for the empirical per-sector forecast config.

This module bridges the walk-forward backtest (``sector_forecast_backtest.py``,
out of scope here) and the runtime engine (``forecasting_engine.py``): it
validates untrusted config entries, derives a per-sector best (model, horizon)
choice from a grid of ``CellResult`` cells, assembles/writes the committed
JSON artifact, and — most safety-critically — loads that artifact back at
process start without ever raising or corrupting the hardcoded fallback.

Only stdlib is used (``json``, ``logging``, ``pathlib``, ``datetime``) — no
new third-party dependencies, per the frozen contract in
``sector_forecast_types.py``.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Sequence

from validation.sector_forecast_types import (
    BacktestConfig,
    CellResult,
    ForecastArtifact,
    SECTOR_MODELS,
    SectorConfigEntry,
)

logger = logging.getLogger("sector_config_io")

# Schema version for the artifact written by build_artifact/write_artifact and
# read back by load_sector_configs. Bump this if the artifact shape changes
# in a backward-incompatible way.
SCHEMA_VERSION = 1

# The exact set of valid horizon values a sector config entry's "days" may take.
_VALID_DAYS = frozenset((30, 60, 90))


def validate_sector_config_entry(entry: object) -> Optional[SectorConfigEntry]:
    """Return a normalized ``{'days': int, 'model': str}`` if ``entry`` is valid.

    Validity requires:
      - ``entry`` supports mapping-style ``__getitem__``/``__contains__``
        lookups for both ``'days'`` and ``'model'`` (in practice, a ``dict``
        or ``dict``-like object such as one freshly parsed from JSON).
      - ``entry['days']`` is an ``int`` (NOT a ``bool`` — ``bool`` is a
        subclass of ``int`` in Python but is rejected here since a boolean
        was never a legitimate "days" value) and a member of ``{30, 60, 90}``.
      - ``entry['model']`` is a ``str`` and a member of ``SECTOR_MODELS``
        (``'MC'``, ``'ARIMA'``, ``'HW'``).

    Design choice — coercion vs. rejection for wrongly-typed values: this
    function REJECTS (returns ``None``) rather than coerces. A `days` value
    of the *string* ``"30"`` is rejected, not silently converted to the int
    ``30``. Rationale: this predicate is shared between the runtime loader
    (parsing a JSON artifact — where a string here indicates the artifact
    was hand-edited or produced by a buggy writer, a signal worth failing
    on) and ``settings.py``'s pydantic field_validator (where lenient
    coercion could mask a genuinely malformed `.env` override). Rejecting
    the whole entry and falling back to the caller's default is always safe;
    silently coercing is not. See ``tests/test_sector_config_io.py`` for the
    explicit test of this behavior.

    Never raises — any exception encountered while inspecting ``entry``
    (e.g. a custom object whose ``__getitem__`` raises something other than
    ``KeyError``/``TypeError``) is caught and treated as invalid.
    """
    try:
        if entry is None or isinstance(entry, (str, bytes, int, float, bool, list, tuple, set)):
            return None
        if not isinstance(entry, Mapping):
            # Allow any other dict-like mapping (e.g. a Mapping subclass)
            # but reject anything without real mapping semantics.
            if not (hasattr(entry, "__getitem__") and hasattr(entry, "__contains__")):
                return None

        if "days" not in entry or "model" not in entry:
            return None

        days = entry["days"]
        model = entry["model"]

        # bool is a subclass of int -- explicitly reject True/False as "days".
        if isinstance(days, bool) or not isinstance(days, int):
            return None
        if days not in _VALID_DAYS:
            return None

        if not isinstance(model, str):
            return None
        if model not in SECTOR_MODELS:
            return None

        return {"days": int(days), "model": model}
    except Exception:
        return None


def derive_sector_configs(
    results: Sequence[CellResult],
    fallback: Mapping[str, SectorConfigEntry],
    min_forecasts: int = 30,
) -> dict:
    """Derive the per-sector (model, horizon) config from backtest cells.

    Per sector (the union of sectors appearing in ``results`` and in
    ``fallback``), picks the qualifying ``CellResult`` (``n_forecasts >=
    min_forecasts`` and both ``mase``/``rmse`` finite) with:
      1. lowest ``mase``
      2. tiebreak: lowest ``rmse``
      3. tiebreak: lowest ``horizon``

    Falls back to ``fallback[sector]`` verbatim when no qualifying cell
    exists for that sector. A sector present in neither ``results`` nor
    ``fallback`` never appears in the output (never fabricated).
    """
    by_sector: dict = {}
    for cell in results:
        by_sector.setdefault(cell.sector, []).append(cell)

    sectors = set(by_sector.keys()) | set(fallback.keys())

    out: dict = {}
    for sector in sectors:
        candidates = [
            c
            for c in by_sector.get(sector, [])
            if c.n_forecasts >= min_forecasts
            and math.isfinite(c.mase)
            and math.isfinite(c.rmse)
        ]
        if candidates:
            best = min(candidates, key=lambda c: (c.mase, c.rmse, c.horizon))
            out[sector] = {"days": best.horizon, "model": best.model}
        elif sector in fallback:
            out[sector] = dict(fallback[sector])
        # else: sector had candidates-less results but no fallback entry --
        # never fabricate, so it's simply omitted.

    return out


def build_artifact(
    results: Sequence[CellResult],
    derived: Mapping[str, SectorConfigEntry],
    config: BacktestConfig,
    population_meta: dict,
) -> dict:
    """Assemble the full JSON-serializable artifact dict.

    ``generated_at`` uses ``datetime.now(timezone.utc).isoformat()`` directly
    -- a non-deterministic timestamp in the artifact is expected and is not
    a lookahead/determinism concern (only the data payload, not the
    timestamp, is asserted deterministic by the test suite).
    """
    backtest_meta = {
        "method": "expanding_window_walk_forward",
        "models": list(config.models),
        "horizons": list(config.horizons),
        "lookback_days": config.lookback_days,
        "step_days": config.step_days,
        "embargo_days": config.embargo_days,
        "min_train_bars": config.min_train_bars,
        **population_meta,
    }

    grid = [
        {
            "sector": c.sector,
            "model": c.model,
            "horizon": c.horizon,
            "mase": c.mase,
            "rmse": c.rmse,
            "n_forecasts": c.n_forecasts,
            "n_symbols": c.n_symbols,
        }
        for c in results
    ]

    artifact: dict = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backtest": backtest_meta,
        "sector_configs": {sector: dict(entry) for sector, entry in derived.items()},
        "grid": grid,
    }
    return artifact


def write_artifact(path, artifact: dict) -> None:
    """Write ``artifact`` as pretty-printed, key-sorted JSON with a trailing
    newline, so re-runs on unchanged inputs produce byte-identical files.

    Creates parent directories if needed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, sort_keys=True, indent=2)
        f.write("\n")


def load_sector_configs(
    path,
    fallback: Mapping[str, SectorConfigEntry],
    overrides: Optional[Mapping[str, SectorConfigEntry]] = None,
) -> dict:
    """THE RUNTIME LOADER.

    Called by ``ForecastingEngine.__init__`` on every process start. Must
    NEVER raise and must NEVER silently corrupt the returned config dict --
    worst case it returns exactly ``dict(fallback)``.

    Resolution order (later steps overlay, never replace wholesale):
      1. Start from a copy of ``fallback``.
      2. Overlay valid entries from the JSON artifact at ``path`` (if any).
      3. Overlay valid entries from ``overrides`` (if any) -- final say.
    """
    result: dict = dict(fallback)

    # --- Step 2: overlay from the artifact file, if present and valid. ---
    try:
        if path:
            p = Path(path)
            if p.exists():
                try:
                    with p.open("r", encoding="utf-8") as f:
                        parsed = json.load(f)
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    logger.warning(
                        "sector_config_io: failed to read/parse artifact at %s (%s); "
                        "keeping fallback sector_configs.",
                        path,
                        exc,
                    )
                    parsed = None

                if isinstance(parsed, dict):
                    sector_configs = parsed.get("sector_configs")
                    if isinstance(sector_configs, dict):
                        for sector, raw_entry in sector_configs.items():
                            try:
                                validated = validate_sector_config_entry(raw_entry)
                            except Exception as exc:  # pragma: no cover - defensive
                                logger.warning(
                                    "sector_config_io: error validating entry for "
                                    "sector %r in artifact %s (%s); skipping.",
                                    sector,
                                    path,
                                    exc,
                                )
                                validated = None
                            if validated is not None:
                                result[sector] = validated
                            else:
                                logger.warning(
                                    "sector_config_io: invalid sector_configs entry "
                                    "for sector %r in artifact %s (%r); keeping "
                                    "fallback value for this sector.",
                                    sector,
                                    path,
                                    raw_entry,
                                )
                    else:
                        logger.warning(
                            "sector_config_io: artifact at %s has no valid "
                            "'sector_configs' dict; keeping fallback sector_configs.",
                            path,
                        )
                elif parsed is not None:
                    logger.warning(
                        "sector_config_io: artifact at %s did not parse to a JSON "
                        "object; keeping fallback sector_configs.",
                        path,
                    )
    except Exception as exc:  # pragma: no cover - belt-and-suspenders
        logger.warning(
            "sector_config_io: unexpected error loading artifact at %s (%s); "
            "keeping fallback/partial sector_configs.",
            path,
            exc,
        )

    # --- Step 3: overlay explicit overrides, validated independently. ---
    try:
        if overrides:
            for sector, raw_entry in dict(overrides).items():
                try:
                    validated = validate_sector_config_entry(raw_entry)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "sector_config_io: error validating override entry for "
                        "sector %r (%s); skipping.",
                        sector,
                        exc,
                    )
                    validated = None
                if validated is not None:
                    result[sector] = validated
                else:
                    logger.warning(
                        "sector_config_io: invalid override entry for sector %r "
                        "(%r); skipping.",
                        sector,
                        raw_entry,
                    )
    except Exception as exc:  # pragma: no cover - belt-and-suspenders
        logger.warning(
            "sector_config_io: unexpected error applying overrides (%s); "
            "keeping result as-is.",
            exc,
        )

    return result
