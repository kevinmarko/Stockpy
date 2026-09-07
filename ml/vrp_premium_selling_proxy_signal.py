"""Forecast Backfill screen ONLY -- a quarantined, realized-vol-derived proxy
for VRPPremiumSellingSignal's gate, used to give that signal's meta-labeler
something non-degenerate to train on when real per-ticker historical implied
volatility is unavailable (which is always, today -- see
``docs/known_issues/vrp_premium_selling_no_historical_iv.md`` for the full,
verified explanation of why True_IVR/VRP are structurally unreconstructable
from this repo's permitted FMP/Yahoo data sources).

**Deliberately NOT auto-registered into ``signals.registry.global_registry``**
-- unlike every real ``SignalModule`` file's ``# Auto-register with global
signal registry`` convention (see the bottom of e.g.
``signals/vrp_premium_selling.py``), this module has no such call. That is
the structural mechanism keeping it out of live production trading scoring
entirely: it is only ever instantiated directly by
``ml/forecast_backfill.py``, gated behind
``settings.FORECAST_BACKFILL_VRP_PROXY_ENABLED`` (opt-in, default False), and
its model_type name (``vrp_premium_selling_proxy``) is deliberately absent
from ``ml/forecast_backfill_registry_bridge.py::BACKFILL_ELIGIBLE_SIGNAL_IDS``
so ``bootstrap_meta_registry()`` can never promote a model trained on it into
``ml.meta_labeling.global_meta_registry`` -- a model trained on this proxy
must never reach live inference.

A model trained on this is a REALIZED-VOL-REGIME model, not a
volatility-risk-premium model. It contains no options-market information and
supports no claim about the actual VRP the real ``vrp_premium_selling``
signal's thesis rests on.

Gate logic is a byte-for-byte port of
``signals/vrp_premium_selling.py::VRPPremiumSellingSignal.compute_vectorized``,
substituting ``IVR_Proxy``/``VRP_Proxy`` (computed in
``ml/forecast_backfill.py::step_2_calculate_technical_features()``, OHLCV-only,
no network) for ``True_IVR``/``VRP`` -- same thresholds (imported, not
re-duplicated, from ``signals.vrp_premium_selling`` so the two gates can never
silently drift apart), same score/confidence/explanation shape, same
``meta_label_features``/``meta_label_horizons`` (standard technical context
columns that reference neither the real nor the proxy IV/VRP columns, so the
meta-labeler's own context features are identical either way).
"""

import pandas as pd

from signals.base import SignalModule, SignalContext
from signals.vrp_premium_selling import (
    IVR_SELL_THRESHOLD,
    VRP_MIN_THRESHOLD,
    VRP_SATURATION,
)


class VrpPremiumSellingProxySignal(SignalModule):
    name = "vrp_premium_selling_proxy"
    required_features = []
    meta_label_features = ["GARCH_Vol", "Vol_20", "Vol_50", "RSI_14", "SMA_200", "Vol_Ratio"]
    meta_label_horizons = [10, 30, 60, 90]

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        ivr_proxy = df.get("IVR_Proxy")
        ivr_proxy = (
            pd.to_numeric(ivr_proxy, errors="coerce")
            if ivr_proxy is not None
            else pd.Series(float("nan"), index=df.index)
        )
        vrp_proxy = df.get("VRP_Proxy")
        vrp_proxy = (
            pd.to_numeric(vrp_proxy, errors="coerce")
            if vrp_proxy is not None
            else pd.Series(float("nan"), index=df.index)
        )

        has_data = ivr_proxy.notna() & vrp_proxy.notna()
        gate = has_data & (ivr_proxy > IVR_SELL_THRESHOLD) & (vrp_proxy > VRP_MIN_THRESHOLD)

        ivr_excess = ((ivr_proxy - IVR_SELL_THRESHOLD) / IVR_SELL_THRESHOLD).clip(lower=0.0, upper=1.0)
        vrp_excess = (vrp_proxy / VRP_SATURATION).clip(lower=0.0, upper=1.0)
        raw_score = (0.5 * ivr_excess + 0.5 * vrp_excess).clip(lower=0.0, upper=1.0)

        score = pd.Series(0.0, index=df.index)
        score[gate] = raw_score[gate]
        confidence = pd.Series(0.0, index=df.index)
        confidence[gate] = 1.0

        exps = pd.Series("", index=df.index)
        exps[~has_data] = "Proxy Cash/Wait: IVR_Proxy/VRP_Proxy not available"
        exps[gate] = "Proxy VRP regime favors selling premium (realized-vol-derived, not real IV)"
        exps[has_data & ~gate] = "Proxy VRP regime gate not met (realized-vol-derived, not real IV)"

        return pd.DataFrame(
            {
                "score": score,
                "confidence": confidence,
                "explanation": exps,
                "meta_label_proba": 1.0,
            },
            index=df.index,
        )
