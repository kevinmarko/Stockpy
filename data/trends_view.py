"""Read-only view builders for the Google Trends "Search Attention" viewer.

The daemon downloads Google Trends in overlapping windows, stitches them into
one long daily series and stores both (``data/trends_store.py``). This module
turns what is stored into JSON-ready dicts that show *how* the stitched series
was made, so an operator can check it by eye. It is pure: it reads a store
object it is handed, never imports pytrends or anything from ``api/``, and
never writes.

Why stitching exists
--------------------
Google normalizes every download so its own maximum is 100. Two windows that
overlap in time therefore disagree about the *scale* of the shared days: the
same day might be 40 in one window and 70 in the next. To join them,
``GoogleTrendsStitcher`` multiplies the later window by a factor ``f`` equal
to the ratio of the two windows' sums over the shared days, then averages the
overlap. This module never re-implements that formula; it calls
``get_scaling_metadata`` and ``stitch_intervals`` and reports what they did.

applied_factor vs pair_factor
-----------------------------
Both are shown per seam (the join between window ``k-1`` and window ``k``)
because they answer different questions:

* ``applied_factor`` is the factor the stitcher *actually used*. The stitcher
  is cumulative: for seam ``k`` it compares the **stitch built so far**
  (windows 0..k-1 already joined and rescaled) against raw window ``k``.
  So ``applied_factor`` carries the rescaling of every earlier seam with it.
* ``pair_factor`` is the **raw neighbour ratio**: the sum of raw window
  ``k-1`` over the shared days divided by the sum of raw window ``k`` over the
  same days. It ignores everything before window ``k-1``.

For seam 1 the two are equal (the stitch so far *is* window 0). From seam 2
on they differ by exactly the drift the earlier seams introduced, which is the
number worth looking at: a large gap means the chain is compounding scale
error. ``degenerate_guard`` is true when either overlap sum is at or below
``1e-9``; the stitcher then refuses to divide and falls back to
``applied_factor == 1.0`` (and ``pair_factor`` is ``None``).

Honesty rules (CONSTRAINT #4 and #6)
------------------------------------
Any number that cannot be computed (NaN, infinite, a ratio over a vanishing
sum) is ``None``, never ``0``. The builders never raise: on any exception they
return ``status: "error"`` whose ``reason`` names only the exception *type*.
"""
from __future__ import annotations

import calendar
import inspect
import logging
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

import pandas as pd

from data.trends_stitcher import ASVICalculator, GoogleTrendsStitcher
from settings import settings

logger = logging.getLogger(__name__)

#: A term the API accepts: upper-case ticker-like symbols.
TERM_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")

#: A batch is stale once it is older than this many refresh intervals.
STALE_INTERVALS: float = 2.0

#: Read from ``compute_asvi``'s own signature so the view cannot drift from it.
ASVI_LOOKBACK_DAYS: int = int(
    inspect.signature(ASVICalculator.compute_asvi).parameters["lookback_weeks"].default * 7
)

#: Same near-zero threshold ``GoogleTrendsStitcher.get_scaling_metadata`` uses.
_SUM_EPS = 1e-9


# ── Small helpers ────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _num(x: Any) -> Optional[float]:
    """A finite float, or ``None`` (NaN/inf/None/non-numeric are never 0)."""
    try:
        if x is None:
            return None
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _iso_dt(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_date(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, pd.Timestamp):
        d = d.date()
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _as_date(d: Any) -> date:
    return d.date() if isinstance(d, (pd.Timestamp, datetime)) else d


def _epoch_ms(d: Any) -> int:
    """Epoch milliseconds at UTC midnight of a date-like."""
    return calendar.timegm(_as_date(d).timetuple()) * 1000


def _points(series: pd.Series) -> list:
    return [[_epoch_ms(idx), _num(v)] for idx, v in series.items()]


def _stale_after_hours() -> float:
    return STALE_INTERVALS * float(settings.GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS)


def _age_and_stale(ref: Optional[datetime], now: datetime) -> tuple:
    if ref is None:
        return None, None
    age = round((now - ref).total_seconds() / 3600.0, 3)
    return age, age > _stale_after_hours()


def config_echo() -> dict:
    """The live Google Trends settings the viewer needs to explain itself."""
    return {
        "enabled": bool(settings.GOOGLE_TRENDS_ENABLED),
        "window_days": int(settings.GOOGLE_TRENDS_WINDOW_DAYS),
        "overlap_days": int(settings.GOOGLE_TRENDS_OVERLAP_DAYS),
        "refresh_interval_hours": float(settings.GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS),
        "max_terms_per_pass": int(settings.GOOGLE_TRENDS_MAX_TERMS_PER_PASS),
        "retention_batches": int(settings.GOOGLE_TRENDS_RAW_RETENTION_BATCHES),
        "stale_after_hours": _stale_after_hours(),
        "asvi_lookback_days": ASVI_LOOKBACK_DAYS,
    }


def empty_svi(term: str, status: str, reason: Optional[str]) -> dict:
    """The full SVI response shape with nothing in it."""
    return {
        **config_echo(),
        "term": term,
        "status": status,
        "reason": reason,
        "batch": None,
        "latest_attempt": None,
        "raw_windows": [],
        "seams": [],
        "rescaled_windows": [],
        "stitched": None,
        "recompute_check": None,
        "asvi": None,
    }


def _batch_view(batch: Optional[dict], now: datetime) -> Optional[dict]:
    if not batch:
        return None
    ref = batch.get("finished_at") or batch.get("started_at")
    age, stale = _age_and_stale(ref, now)
    return {
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "reason": batch.get("reason"),
        "started_at": _iso_dt(batch.get("started_at")),
        "finished_at": _iso_dt(batch.get("finished_at")),
        "windows_expected": batch.get("windows_expected"),
        "windows_fetched": batch.get("windows_fetched"),
        "age_hours": age,
        "stale": stale,
    }


def _rows_to_series(rows: list) -> pd.Series:
    """Stored stitched rows as a date-indexed series, built exactly like
    ``_apply_google_trends_asvi`` builds it so the ASVI is bit-identical."""
    return pd.Series([float(r["value"]) for r in rows], index=[r["date"] for r in rows])


def _asvi_of(rows: list) -> pd.Series:
    return ASVICalculator.compute_asvi(_rows_to_series(rows))


def _latest_asvi(asvi: pd.Series) -> tuple:
    valid = asvi.dropna()
    if valid.empty:
        return None, None
    value = _num(valid.iloc[-1])
    if value is None:
        return None, None
    return value, _iso_date(valid.index[-1])


# ── Terms view ───────────────────────────────────────────────────────────────


def build_terms_view(
    store: Any,
    tracked_universe: Optional[Iterable[str]],
    *,
    now: Optional[datetime] = None,
) -> dict:
    """One row per term the store knows about, plus coverage of the tracked
    universe. ``tracked_universe=None`` means "unknown": coverage is ``None``
    rather than a guess."""
    try:
        now = now or _utc_now()
        batches = store.latest_batches()
        summary = store.stitched_summary()
        universe = (
            None if tracked_universe is None
            else {str(t).strip().upper() for t in tracked_universe if str(t).strip()}
        )

        rows = []
        for term in sorted(set(batches) | set(summary)):
            slot = batches.get(term) or {}
            latest, complete = slot.get("latest"), slot.get("complete")
            stitched = summary.get(term)
            stitched_days = int(stitched["days"]) if stitched else 0

            if complete is not None:
                ref = complete.get("finished_at") or complete.get("started_at")
            else:
                ref = stitched["stitched_at"] if stitched else None
            age, stale = _age_and_stale(ref, now)

            latest_asvi = latest_asvi_date = None
            if stitched_days > 0:
                try:
                    latest_asvi, latest_asvi_date = _latest_asvi(
                        _asvi_of(store.get_stitched_rows(term))
                    )
                except Exception as exc:  # one bad term never sinks the list
                    logger.warning("build_terms_view: ASVI for %s failed: %s", term, type(exc).__name__)

            rows.append({
                "term": term,
                "in_tracked_universe": None if universe is None else term in universe,
                "last_attempt_at": _iso_dt(latest["started_at"]) if latest else None,
                "last_attempt_status": latest["status"] if latest else None,
                "last_attempt_reason": latest.get("reason") if latest else None,
                "last_complete_at": _iso_dt(complete.get("finished_at")) if complete else None,
                "age_hours": age,
                "stale": stale,
                "stitched_days": stitched_days,
                "last_date": _iso_date(stitched["last_date"]) if stitched else None,
                "latest_asvi": latest_asvi,
                "latest_asvi_date": latest_asvi_date,
                "has_complete_batch": complete is not None,
            })

        enabled = bool(settings.GOOGLE_TRENDS_ENABLED)
        if rows:
            status, reason = "ok", None
        elif enabled:
            status, reason = "no_data", "no Google Trends batches or stitched series stored yet"
        else:
            status, reason = "disabled", "GOOGLE_TRENDS_ENABLED is false and nothing is stored"

        with_data = {r["term"] for r in rows if r["stitched_days"] > 0}
        return {
            **config_echo(),
            "status": status,
            "reason": reason,
            "tracked_universe_size": None if universe is None else len(universe),
            "tracked_with_data": None if universe is None else len(universe & with_data),
            "terms": rows,
        }
    except Exception as exc:
        logger.warning("build_terms_view failed: %s", type(exc).__name__)
        return {
            **config_echo(),
            "status": "error",
            "reason": type(exc).__name__,
            "tracked_universe_size": None,
            "tracked_with_data": None,
            "terms": [],
        }


# ── SVI view ─────────────────────────────────────────────────────────────────


def _walk_chain(windows: list) -> tuple:
    """Walk the stitch window by window, calling the stitcher's own functions.

    Returns ``(seams, rescaled_windows, stitched_series)``."""
    stitcher = GoogleTrendsStitcher
    seams: list = []
    rescaled: list = [{"index": 0, "points": _points(windows[0])}]
    stitched = windows[0]
    for k in range(1, len(windows)):
        prev, cur = windows[k - 1], windows[k]
        meta = stitcher.get_scaling_metadata(stitched, cur)
        dates = meta["overlap_dates"]
        raw_sum_a = float(stitched.loc[dates].sum())
        raw_sum_b = float(cur.loc[dates].sum())

        pair_dates = prev.index.intersection(cur.index)
        pair_a = float(prev.loc[pair_dates].sum())
        pair_b = float(cur.loc[pair_dates].sum())
        pair_factor = (
            None if pair_a <= _SUM_EPS or pair_b <= _SUM_EPS else _num(pair_a / pair_b)
        )

        seams.append({
            "index": k,
            "a_window": k - 1,
            "b_window": k,
            "overlap_start": _iso_date(dates[0]),
            "overlap_end": _iso_date(dates[-1]),
            "overlap_days": int(len(dates)),
            "sum_a": _num(raw_sum_a),
            "sum_b": _num(raw_sum_b),
            "applied_factor": _num(meta["f"]),
            "pair_factor": pair_factor,
            "degenerate_guard": bool(raw_sum_a <= _SUM_EPS or raw_sum_b <= _SUM_EPS),
        })
        rescaled.append({"index": k, "points": _points(cur * meta["f"])})
        stitched = stitcher.stitch_intervals(stitched, cur)
    return seams, rescaled, stitched


def _recompute_check(stitched: pd.Series, rows: list) -> dict:
    """Compare a fresh stitch of the stored raw windows with the stored series."""
    fresh = {_as_date(d): float(v) for d, v in stitched.dropna().items()}
    stored = {r["date"]: float(r["value"]) for r in rows}
    common = sorted(set(fresh) & set(stored))
    diffs = [abs(fresh[d] - stored[d]) for d in common]
    max_diff = _num(max(diffs)) if diffs else None
    same_dates = set(fresh) == set(stored)
    return {
        "compared_days": len(common),
        "max_abs_diff": max_diff,
        "same_dates": same_dates,
        "matches": bool(same_dates and max_diff is not None and max_diff < _SUM_EPS),
    }


def build_svi_view(store: Any, term: str, *, now: Optional[datetime] = None) -> dict:
    """The full stitching story for one (already validated, upper-cased) term."""
    try:
        now = now or _utc_now()
        complete = store.latest_batch(term, complete_only=True)
        latest = store.latest_batch(term, complete_only=False)
        rows = store.get_stitched_rows(term)

        if not latest and not rows:
            if bool(settings.GOOGLE_TRENDS_ENABLED):
                return empty_svi(term, "no_data", "no Google Trends data stored for this term yet")
            return empty_svi(term, "disabled", "GOOGLE_TRENDS_ENABLED is false and nothing is stored")

        out = empty_svi(term, "ok", None)
        out["batch"] = _batch_view(complete, now)
        out["latest_attempt"] = _batch_view(latest, now)

        if rows:
            stamps = {r["stitched_at"] for r in rows}
            finished = complete.get("finished_at") if complete else None
            out["stitched"] = {
                "points": [[_epoch_ms(r["date"]), _num(r["value"])] for r in rows],
                "first_date": _iso_date(rows[0]["date"]),
                "last_date": _iso_date(rows[-1]["date"]),
                "days": len(rows),
                "persisted_at": _iso_dt(max(stamps)),
                "from_latest_batch": bool(
                    complete is not None and finished is not None and stamps == {finished}
                ),
            }
            asvi = _asvi_of(rows)
            value, value_date = _latest_asvi(asvi)
            out["asvi"] = {
                "points": [[_epoch_ms(d), _num(v)] for d, v in asvi.items()],
                "latest": value,
                "latest_date": value_date,
                "lookback_days": ASVI_LOOKBACK_DAYS,
            }

        if complete is None:
            if rows:
                out["status"] = "stitched_only"
                out["reason"] = (
                    "no complete download batch is stored (it was pruned or never "
                    "finished); showing the stored stitched series without its raw windows"
                )
            else:
                out["status"] = "partial_only"
                out["reason"] = (
                    "only partial or failed download batches exist; the daemon does "
                    "not stitch those, so there is no series yet"
                )
            return out

        grouped = GoogleTrendsStitcher.group_raw_windows_into_series(
            store.load_batch_windows(complete["batch_id"])
        )
        if not grouped:
            return empty_svi(term, "error", "complete batch has no raw rows stored")
        windows = [s for _, s in grouped]

        out["raw_windows"] = []
        for i, w in enumerate(windows):
            peak_idx = w.idxmax() if len(w) and w.notna().any() else None
            out["raw_windows"].append({
                "index": i,
                "start": _iso_date(w.index.min()),
                "end": _iso_date(w.index.max()),
                "points": _points(w),
                "peak_date": _iso_date(peak_idx),
                "peak_value": _num(w.loc[peak_idx]) if peak_idx is not None else None,
            })

        seams, rescaled, stitched = _walk_chain(windows)
        out["seams"] = seams
        out["rescaled_windows"] = rescaled
        if rows:
            out["recompute_check"] = _recompute_check(stitched, rows)
        return out
    except Exception as exc:
        logger.warning("build_svi_view(%s) failed: %s", term, type(exc).__name__)
        return empty_svi(term, "error", type(exc).__name__)
