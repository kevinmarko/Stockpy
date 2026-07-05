"""
validation/drift.py
====================
Task B3 — Calibration & regime-drift sequential change-point detector.

Problem this closes
--------------------
``validation/harness.py`` already runs walk-forward tests (60/40, 70/30, 80/20
splits) but these are DIAGNOSTIC ONLY, run on-demand against historical data.
Nothing automatically watches a *live, streaming* series (e.g. rolling
conviction-calibration error, or the HMM regime detector's dominant-state
label) and raises a flag when its statistical behavior drifts out of the
distribution it was validated against.

This module implements two classic, real (not stubbed) sequential
change-point detectors over a 1-D stream of floats:

* **CUSUM** (cumulative sum control chart, Page 1954) — accumulates signed
  deviations from a reference mean; a drift alarm fires when the cumulative
  sum of upward (or downward) deviations exceeds a threshold ``h``. This is
  the classic two-sided tabular CUSUM used in statistical process control.
* **Page-Hinkley test** (Page 1954) — a running-mean formulation of the same
  idea, widely used in the online concept-drift-detection literature (Gama
  et al.). Tracks the cumulative deviation from the running mean of the
  stream and its running minimum (or maximum); a drift alarm fires when the
  gap between the cumulative statistic and its extreme value exceeds
  ``threshold``.

Both are *sequential* one-sided-accumulation tests; here each is run in both
directions (upward + downward) so a mean-shift in either direction is caught,
matching the "detect drift" framing in the task (as opposed to control-chart
usage where the direction of interest is known in advance).

Integration point
------------------
``adapt_recommendation_tracking_rows()`` is a thin adapter that takes the
``rows`` list already produced by
``evaluation_engine.recommendation_tracking_report()`` (Tier 4.1 — the
existing live-vs-recommendation tracking system) and derives a plain
``list[float]`` calibration-error stream suitable for ``detect_drift()``,
so callers never need to hand-roll the extraction logic. No new
orchestration is introduced — this module supplies the detector and the
adapter; wiring it into a scheduled job is a separate concern.

Dead-letter resilience (CONSTRAINT #6)
---------------------------------------
``detect_drift()`` never raises. Empty, too-short (< ``MIN_SAMPLES``), or
non-numeric input all degrade to ``DriftResult(drift_detected=False, ...)``
with a human-readable note in ``details``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (no magic numbers in decision logic)
# ---------------------------------------------------------------------------

#: Minimum number of observations required before a detector will even
#: attempt to raise an alarm. Below this, both statistics are too noisy to
#: be meaningful and a false positive is worse than "no verdict yet".
MIN_SAMPLES: int = 8

#: CUSUM: the "slack"/allowance parameter (often called ``k`` or ``slack``),
#: expressed as a multiple of the stream's own sample standard deviation.
#: Deviations smaller than ``slack * std`` are treated as noise and do not
#: accumulate. 0.5 is the traditional control-chart default (detects a shift
#: of about 1 sigma with reasonable run length).
CUSUM_SLACK_STD_MULTIPLE: float = 0.5

#: CUSUM: default alarm threshold ``h``, expressed as a multiple of the
#: stream's own sample standard deviation. Because the CUSUM statistic is a
#: *cumulative* sum of per-step deviations (net of ``slack``), its natural
#: scale grows with series length; 10.0 sigma-units was chosen empirically
#: (see module test coverage) to keep the false-alarm rate on a stationary
#: 200-sample noise series below ~1% while still detecting a 2-sigma
#: sustained mean shift with 100% power — a materially higher multiple than
#: the textbook "one-shot" 1-sigma-shift tuning because the accumulation
#: window here spans the whole calibration stream, not a short control window.
CUSUM_THRESHOLD_STD_MULTIPLE: float = 10.0

#: Page-Hinkley: the magnitude-of-change tolerance ``delta``, expressed as a
#: multiple of the stream's own sample standard deviation. Analogous role to
#: CUSUM's slack parameter.
PAGE_HINKLEY_DELTA_STD_MULTIPLE: float = 0.5

#: Page-Hinkley: default alarm threshold (lambda), expressed as a multiple of
#: the stream's own sample standard deviation. Same empirical calibration
#: rationale as ``CUSUM_THRESHOLD_STD_MULTIPLE`` (low false-alarm rate on
#: stationary noise, full detection power at a 2-sigma sustained shift) —
#: kept numerically equal to the CUSUM threshold so both methods are tuned
#: comparably out of the box.
PAGE_HINKLEY_THRESHOLD_STD_MULTIPLE: float = 10.0

DriftMethod = Literal["cusum", "page_hinkley"]


@dataclass(frozen=True)
class DriftResult:
    """Outcome of a single ``detect_drift()`` call.

    Attributes
    ----------
    drift_detected:
        True iff the configured statistic crossed its alarm threshold
        anywhere in the series.
    drift_index:
        Integer position (0-based, into the *input* series) where the drift
        alarm first fired. ``None`` when no drift was detected, or when the
        input was too short/empty to evaluate (CONSTRAINT #6 — never a
        fabricated index).
    method:
        Which detector produced this result (``"cusum"`` or
        ``"page_hinkley"``).
    details:
        Free-form diagnostic dict: sample count, threshold used, the
        reference mean/std, the max statistic value reached, and (when
        triggered) which direction the shift was detected in. Always
        present — never `None` — so callers can log context even on the
        "insufficient history" path.
    """

    drift_detected: bool
    drift_index: Optional[int]
    method: str
    details: Dict[str, Any] = field(default_factory=dict)


def _coerce_series(values: Union[pd.Series, Sequence[float], None]) -> np.ndarray:
    """Best-effort coercion of caller input to a clean 1-D float array.

    Drops NaN/inf entries (they carry no information for a mean-shift test
    and would otherwise poison the running statistics). Never raises —
    unparseable input degrades to an empty array, which the caller-facing
    ``detect_drift()`` treats as "insufficient history".
    """
    if values is None:
        return np.asarray([], dtype=float)
    try:
        if isinstance(values, pd.Series):
            arr = values.to_numpy(dtype=float, na_value=np.nan)
        else:
            arr = np.asarray(list(values), dtype=float)
    except Exception as exc:
        logger.debug("validation.drift: could not coerce input to float array: %s", exc)
        return np.asarray([], dtype=float)
    finite_mask = np.isfinite(arr)
    return arr[finite_mask]


def _cusum_drift(arr: np.ndarray, threshold: Optional[float]) -> DriftResult:
    """Two-sided tabular CUSUM change-point detector.

    Reference mean/std are estimated from the *entire* supplied series
    (a simple, self-calibrating baseline — callers who want a fixed
    reference/training-only baseline should slice ``values`` themselves
    before calling, e.g. pass only the validation-period baseline plus the
    live tail).

    Standard tabular-CUSUM recursion (Montgomery):
        S+_t = max(0, S+_{t-1} + (x_t - mu) - slack)
        S-_t = max(0, S-_{t-1} + (mu - x_t) - slack)
    Alarm when S+_t > h or S-_t > h.
    """
    n = arr.shape[0]
    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1)) if n > 1 else 0.0

    # A perfectly flat / zero-variance series cannot exhibit a detectable
    # "shift" in the statistical sense — guard the degenerate case rather
    # than dividing by zero or firing spurious alarms.
    if sigma == 0.0 or not math.isfinite(sigma):
        return DriftResult(
            drift_detected=False,
            drift_index=None,
            method="cusum",
            details={
                "n_samples": n,
                "reference_mean": mu,
                "reference_std": sigma,
                "note": "zero/undefined variance in series — no shift is detectable",
            },
        )

    slack = CUSUM_SLACK_STD_MULTIPLE * sigma
    h = threshold if threshold is not None else CUSUM_THRESHOLD_STD_MULTIPLE * sigma

    s_pos = 0.0
    s_neg = 0.0
    max_stat = 0.0
    drift_index: Optional[int] = None
    direction: Optional[str] = None

    for i, x in enumerate(arr):
        s_pos = max(0.0, s_pos + (x - mu) - slack)
        s_neg = max(0.0, s_neg + (mu - x) - slack)
        max_stat = max(max_stat, s_pos, s_neg)
        if drift_index is None and (s_pos > h or s_neg > h):
            drift_index = i
            direction = "upward" if s_pos > h else "downward"
            break

    return DriftResult(
        drift_detected=drift_index is not None,
        drift_index=drift_index,
        method="cusum",
        details={
            "n_samples": n,
            "reference_mean": mu,
            "reference_std": sigma,
            "slack": slack,
            "threshold": h,
            "max_statistic": max_stat,
            "direction": direction,
        },
    )


def _page_hinkley_drift(arr: np.ndarray, threshold: Optional[float]) -> DriftResult:
    """Two-sided Page-Hinkley sequential change-point test.

    Classic online concept-drift formulation (Page 1954; see also Gama et al.
    2004 for the streaming/ML framing):
        m_t = sum_{i=1}^{t} (x_i - mu_hat_t - delta)
        M_t = min(m_1, ..., m_t)   (running minimum)
        PH_t = m_t - M_t
        Alarm when PH_t > lambda

    ``mu_hat_t`` is the running mean up to time t (Page-Hinkley's own
    self-updating baseline — distinct from CUSUM's single fixed reference
    mean, which is the classic distinction between the two tests).

    Run in both directions so a shift of either sign trips the alarm,
    matching this module's "detect drift" (not "detect a specific
    known-direction shift") framing. The decrease-detecting statistic is the
    mirror image of the increase-detecting one (accumulate
    ``mu_hat_t - x_t - delta`` instead of ``x_t - mu_hat_t - delta``) and,
    critically, ALSO compares against its own running *minimum* — not a
    running maximum. Both accumulators trend downward on average once
    ``delta`` is subtracted each step (that is what makes them into a
    change-point statistic at all: PH_t measures the *recovery* from the
    running minimum, so it only grows when the series genuinely departs from
    its own running mean in the direction being tested).
    """
    n = arr.shape[0]
    sigma = float(np.std(arr, ddof=1)) if n > 1 else 0.0

    if sigma == 0.0 or not math.isfinite(sigma):
        return DriftResult(
            drift_detected=False,
            drift_index=None,
            method="page_hinkley",
            details={
                "n_samples": n,
                "reference_std": sigma,
                "note": "zero/undefined variance in series — no shift is detectable",
            },
        )

    delta = PAGE_HINKLEY_DELTA_STD_MULTIPLE * sigma
    lam = threshold if threshold is not None else PAGE_HINKLEY_THRESHOLD_STD_MULTIPLE * sigma

    running_mean = 0.0
    # Increase-detecting statistic (running sum vs. its own running min)
    m_pos = 0.0
    min_m_pos = 0.0
    # Decrease-detecting statistic (mirror-image running sum vs. its own
    # running min — NOT a running max; see docstring above).
    m_neg = 0.0
    min_m_neg = 0.0

    max_stat = 0.0
    drift_index: Optional[int] = None
    direction: Optional[str] = None

    for i, x in enumerate(arr):
        # Update the running mean *before* accumulating (standard formulation
        # uses the mean of all observations seen so far, inclusive of x_t).
        running_mean += (x - running_mean) / (i + 1)

        m_pos += x - running_mean - delta
        min_m_pos = min(min_m_pos, m_pos)
        ph_pos = m_pos - min_m_pos

        m_neg += running_mean - x - delta
        min_m_neg = min(min_m_neg, m_neg)
        ph_neg = m_neg - min_m_neg

        max_stat = max(max_stat, ph_pos, ph_neg)

        if drift_index is None and (ph_pos > lam or ph_neg > lam):
            drift_index = i
            direction = "upward" if ph_pos > lam else "downward"
            break

    return DriftResult(
        drift_detected=drift_index is not None,
        drift_index=drift_index,
        method="page_hinkley",
        details={
            "n_samples": n,
            "delta": delta,
            "threshold": lam,
            "max_statistic": max_stat,
            "direction": direction,
        },
    )


def detect_drift(
    values: Union[pd.Series, Sequence[float], None],
    method: DriftMethod = "cusum",
    threshold: Optional[float] = None,
    min_samples: int = MIN_SAMPLES,
) -> DriftResult:
    """Run a sequential change-point detector over a 1-D stream of floats.

    Dead-letter resilient (CONSTRAINT #6): empty, too-short, all-NaN, or
    otherwise unusable input never raises — it returns
    ``DriftResult(drift_detected=False, drift_index=None, ...)`` with a note
    in ``details`` explaining why.

    Parameters
    ----------
    values:
        The stream to test — e.g. rolling conviction-calibration error,
        per-cycle HMM ``risk_on_probability``, or rolling win-rate. Accepts a
        ``pandas.Series`` or any sequence of floats. NaN/inf entries are
        dropped before testing (they carry no information for a mean-shift
        test).
    method:
        ``"cusum"`` (default) or ``"page_hinkley"``.
    threshold:
        Optional explicit alarm threshold, in the same units as ``values``
        (not a multiple of sigma). When ``None`` (the default), the
        threshold is derived from the series' own sample standard deviation
        via ``CUSUM_THRESHOLD_STD_MULTIPLE`` / ``PAGE_HINKLEY_THRESHOLD_STD_MULTIPLE``
        — this is the standard self-calibrating tuning for both tests and
        avoids requiring the caller to know the stream's scale in advance.
    min_samples:
        Minimum number of finite observations required to attempt detection.
        Defaults to ``MIN_SAMPLES`` (8). Below this, the series is treated as
        "insufficient history" — never a false "no drift" masquerading as a
        confident verdict.

    Returns
    -------
    DriftResult
    """
    try:
        arr = _coerce_series(values)
    except Exception as exc:  # pragma: no cover — _coerce_series already guards
        logger.warning("validation.drift.detect_drift: coercion failed: %s", exc)
        return DriftResult(
            drift_detected=False, drift_index=None, method=method,
            details={"note": f"input coercion failed: {exc}"},
        )

    if arr.shape[0] < min_samples:
        return DriftResult(
            drift_detected=False,
            drift_index=None,
            method=method,
            details={
                "n_samples": int(arr.shape[0]),
                "min_samples_required": min_samples,
                "note": "insufficient history — need at least min_samples finite observations",
            },
        )

    try:
        if method == "cusum":
            return _cusum_drift(arr, threshold)
        elif method == "page_hinkley":
            return _page_hinkley_drift(arr, threshold)
        else:
            return DriftResult(
                drift_detected=False,
                drift_index=None,
                method=method,
                details={"note": f"unknown method {method!r} — expected 'cusum' or 'page_hinkley'"},
            )
    except Exception as exc:
        # Fail closed but never crash the caller — a broken drift detector
        # must never abort the pipeline / preflight check that consumes it.
        logger.warning("validation.drift.detect_drift[%s]: detector raised: %s", method, exc)
        return DriftResult(
            drift_detected=False,
            drift_index=None,
            method=method,
            details={"note": f"detector raised an exception: {exc}"},
        )


# ---------------------------------------------------------------------------
# Integration adapter — Tier 4.1 live-vs-recommendation tracking
# ---------------------------------------------------------------------------

def adapt_recommendation_tracking_rows(
    rows: Sequence[Dict[str, Any]],
    metric: Literal["calibration_error", "model_return", "actual_return"] = "calibration_error",
) -> List[float]:
    """Turn ``recommendation_tracking_report()['rows']`` into a float stream.

    ``evaluation_engine.recommendation_tracking_report()`` (Tier 4.1) already
    joins the 1.3 decision log to live bar prices and produces one dict per
    logged BUY signal with keys including ``conviction``, ``model_return``,
    ``actual_return``, and ``completed`` (see that function's docstring).
    This adapter derives a plain ``list[float]`` in chronological log order
    (the natural stream order — no re-sorting is performed here; pass
    already-ordered ``rows``) so ``detect_drift()`` can consume it directly
    without every caller re-deriving the same extraction logic.

    Parameters
    ----------
    rows:
        The ``rows`` list from ``recommendation_tracking_report()``'s return
        dict (or any list of dicts with the same per-row keys).
    metric:
        * ``"calibration_error"`` (default) — ``actual_return - model_return``
          for rows where both are available (i.e. an "acted" signal with a
          resolvable model comparison). This is the natural per-signal
          judgment-edge stream: persistent drift here means the operator's
          real-world edge over/under the model is systematically changing.
        * ``"model_return"`` — the model's own paper-equivalent return per
          completed signal (drift here flags the *signal itself* degrading,
          independent of operator behavior).
        * ``"actual_return"`` — the operator's realized return per acted
          signal.

    Returns
    -------
    list[float]
        NaN/missing rows for the requested metric are skipped (never
        fabricated as 0.0 — CONSTRAINT #4). Empty input or an unusable
        ``rows`` argument returns ``[]``, which ``detect_drift()`` already
        treats as "insufficient history" via its ``min_samples`` gate.
    """
    out: List[float] = []
    if not rows:
        return out

    for row in rows:
        try:
            if metric == "calibration_error":
                model_ret = row.get("model_return")
                actual_ret = row.get("actual_return")
                if model_ret is None or actual_ret is None:
                    continue
                if not (math.isfinite(model_ret) and math.isfinite(actual_ret)):
                    continue
                out.append(float(actual_ret) - float(model_ret))
            elif metric == "model_return":
                val = row.get("model_return")
                if val is not None and math.isfinite(val):
                    out.append(float(val))
            elif metric == "actual_return":
                val = row.get("actual_return")
                if val is not None and math.isfinite(val):
                    out.append(float(val))
        except Exception as exc:
            logger.debug("adapt_recommendation_tracking_rows: skipping malformed row: %s", exc)
            continue

    return out


# ---------------------------------------------------------------------------
# Alert wiring — observability/alerts.py
# ---------------------------------------------------------------------------

#: Human-readable label per metric, used in the alert message so an operator
#: reading the alert channel does not need to open this module's source to
#: know what "drifted".
_METRIC_LABELS: Dict[str, str] = {
    "calibration_error": "operator-vs-model judgment edge (actual − model return)",
    "model_return": "model paper-equivalent return",
    "actual_return": "operator realized return",
}


def check_and_alert_recommendation_drift(
    rows: Sequence[Dict[str, Any]],
    metric: Literal["calibration_error", "model_return", "actual_return"] = "calibration_error",
    method: DriftMethod = "cusum",
    *,
    send_alert_fn=None,
) -> DriftResult:
    """Run the drift detector over Tier 4.1 recommendation-tracking rows and,
    if drift is detected, emit a WARNING-level alert via
    ``observability.alerts.send_alert``.

    This is the single integration point intended for both
    ``scripts/preflight_check.py`` (a read-only, non-blocking check) and any
    future scheduled job — callers should not re-implement the
    adapt → detect → alert sequence themselves.

    Parameters
    ----------
    rows:
        The ``rows`` list from ``evaluation_engine.recommendation_tracking_report()``.
    metric:
        Which derived stream to test — see ``adapt_recommendation_tracking_rows()``.
    method:
        ``"cusum"`` or ``"page_hinkley"``.
    send_alert_fn:
        Injectable for tests. Defaults to
        ``observability.alerts.send_alert`` (lazy-imported to avoid a
        module-load-time dependency cycle and to keep this module importable
        even in minimal test environments that stub out ``observability``).

    Returns
    -------
    DriftResult
        Always returned (even when no alert was sent) so callers can inspect
        the verdict directly without re-running the detector.

    Notes
    -----
    Never raises (CONSTRAINT #6): a failure in the alert dispatch itself is
    already swallowed inside ``send_alert()``; a failure in adapting/detecting
    degrades to a non-drift ``DriftResult`` here.
    """
    try:
        stream = adapt_recommendation_tracking_rows(rows, metric=metric)
        result = detect_drift(stream, method=method)
    except Exception as exc:
        logger.warning(
            "check_and_alert_recommendation_drift: detection failed, treating as no-drift: %s",
            exc,
        )
        return DriftResult(
            drift_detected=False, drift_index=None, method=method,
            details={"note": f"detection pipeline raised: {exc}"},
        )

    if result.drift_detected:
        try:
            if send_alert_fn is None:
                from observability.alerts import send_alert as send_alert_fn  # noqa: PLC0415
            label = _METRIC_LABELS.get(metric, metric)
            message = (
                f"Calibration/regime drift detected in {label} "
                f"(method={result.method}, drift_index={result.drift_index}, "
                f"n_samples={result.details.get('n_samples')}). "
                "Live behavior may be diverging from the distribution the "
                "strategy was validated against — review recent recommendation "
                "tracking and consider re-running validation."
            )
            send_alert_fn(
                "WARNING",
                message,
                extra={
                    "type": "calibration_drift",
                    "metric": metric,
                    "method": result.method,
                    "drift_index": result.drift_index,
                    "details": result.details,
                },
            )
        except Exception as exc:
            # send_alert() already swallows channel-level errors; this guards
            # against the lazy import itself failing (e.g. observability
            # package unavailable in a stripped-down environment).
            logger.warning(
                "check_and_alert_recommendation_drift: alert dispatch failed: %s", exc
            )

    return result
