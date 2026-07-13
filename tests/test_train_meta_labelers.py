"""
tests/test_train_meta_labelers.py
==================================
Offline tests for the meta-labeler training + runtime-registration wiring:

  scripts/train_meta_labelers.py   — trains + persists + updates registry
  ml/meta_bootstrap.py             — bootstrap_meta_registry() runtime wiring

Coverage
--------
1. train_signal() trains, persists a pickle, and populates the registry row
   (both signals; synthetic offline panel — no network). The registry row
   carries the wired-through CPCV metrics and a gate-derived ``deployable``.
2. bootstrap_meta_registry() registers a saved model so
   global_meta_registry.has(signal_id) is True afterward.
3. bootstrap_meta_registry() is a strict no-op when no model exists (registry
   stays empty — current-behavior preservation).
4. bootstrap_meta_registry() respects settings.META_LABELING_ENABLED=False.
5. With a registered LOW-confidence labeler, SignalAggregator.aggregate() fires
   the meta_hard_gate and forces meta_label_composite to 0.0 (reuses the
   pattern from tests/test_meta_labeler_uplift.py).
6. A REAL CPCV run over the synthetic panel populates non-null numeric
   cpcv_dsr/pbo and ``deployable`` reflects the honest gate
   (registry_io.compute_deployable).
7. HONESTY: a genuinely-bad model (failing metrics) stays deployable:false, and
   the gate is genuinely applied (good metrics CAN flip it true) — it is never
   spoofed or hardcoded.

Most tests monkeypatch ``compute_cpcv_metrics`` to a fast deterministic stub
(``fast_cpcv`` fixture) so the suite stays quick; the dedicated CPCV test
(``test_real_cpcv_populates_metrics_and_gate``) runs the real evaluation once.

All tests reset the global registry between runs (autouse fixture) so state
never bleeds across tests.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import ml.meta_labeling as meta_labeling
from ml.meta_labeling import MetaLabeler, MetaLabelerRegistry
from ml.meta_bootstrap import bootstrap_meta_registry, META_LABELED_SIGNAL_IDS
import scripts.train_meta_labelers as trainer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the global meta-registry before and after each test."""
    meta_labeling.global_meta_registry = MetaLabelerRegistry()
    yield
    meta_labeling.global_meta_registry = MetaLabelerRegistry()


@pytest.fixture
def tmp_models_dir(tmp_path, monkeypatch):
    """Redirect all meta-labeler persistence to a temp dir so the real
    ml/models/ tree is never touched by the test suite."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    # MetaLabeler.save()/load_latest() read the module-level _MODELS_DIR.
    monkeypatch.setattr(meta_labeling, "_MODELS_DIR", models_dir)
    return models_dir


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    """Point the trainer's registry write at a temp copy of registry.yaml."""
    src = Path(trainer._REGISTRY_PATH)
    dst = tmp_path / "registry.yaml"
    dst.write_text(src.read_text())
    monkeypatch.setattr(trainer, "_REGISTRY_PATH", dst)
    return dst


# Deterministic non-null metrics used by the fast stub. DSR < 0.95 so the gate
# resolves deployable=False — matching the pre-existing "unvalidated" assertion
# while still exercising the real metric-wiring path (values flow through into
# the registry row instead of being hardcoded null).
_STUB_CPCV = {"dsr": 0.30, "pbo": 0.70, "mean_oos_sharpe": 0.42}


@pytest.fixture
def fast_cpcv(monkeypatch):
    """Replace the real (slow) CPCV run with a fast deterministic stub.

    Used by the persistence / bootstrap tests that care about WIRING, not the
    exact metric values, so the suite doesn't pay for a full CPCV sweep in every
    train_signal() call. The dedicated CPCV test does NOT use this fixture.
    """
    monkeypatch.setattr(trainer, "compute_cpcv_metrics", lambda *a, **k: dict(_STUB_CPCV))
    return _STUB_CPCV


# ---------------------------------------------------------------------------
# 1. Training + persistence + registry population
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("signal_id", list(META_LABELED_SIGNAL_IDS))
def test_train_signal_persists_and_updates_registry(signal_id, tmp_models_dir, tmp_registry, fast_cpcv):
    """train_signal() trains, saves a pickle, and updates the registry row.

    The CPCV metrics are wired through from ``compute_cpcv_metrics`` (stubbed
    here for speed) into the registry row, and ``deployable`` is derived from
    them by the shared registry writer — never hardcoded.
    """
    path = trainer.train_signal(signal_id, force_synthetic=True, seed=3)

    assert path is not None, f"{signal_id} should have trained on the synthetic panel"
    assert path.exists(), "saved pickle should exist on disk"
    assert path.parent == tmp_models_dir, "must save into the temp models dir"

    # The saved object round-trips as a trained MetaLabeler.
    reloaded = MetaLabeler.load(path)
    assert reloaded.signal_id == signal_id
    assert reloaded._model is not None
    assert reloaded._n_train_samples >= 30

    # Registry row populated (trained_date + n_train) and the CPCV metrics from
    # the stub are wired through; deployable is gate-derived (dsr 0.30 < 0.95).
    import yaml
    from ml.registry_io import compute_deployable
    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"][f"meta_labeler_{signal_id}"]
    assert row["trained_date"] is not None
    assert isinstance(row["n_train"], int) and row["n_train"] >= 30
    assert row["cpcv_dsr"] == fast_cpcv["dsr"], "CPCV DSR must be wired into the row"
    assert row["pbo"] == fast_cpcv["pbo"], "CPCV PBO must be wired into the row"
    assert row["deployable"] is compute_deployable(fast_cpcv["dsr"], fast_cpcv["pbo"])
    assert row["deployable"] is False, "dsr 0.30 fails the >0.95 gate → not deployable"


def test_train_signal_no_registry_flag_skips_yaml(tmp_models_dir, tmp_registry, fast_cpcv):
    """update_registry=False trains + saves but leaves the YAML untouched."""
    before = tmp_registry.read_text()
    path = trainer.train_signal(
        "timeseries_momentum", force_synthetic=True, update_registry=False, seed=5
    )
    assert path is not None and path.exists()
    assert tmp_registry.read_text() == before, "registry should be unchanged"


# ---------------------------------------------------------------------------
# 2. Runtime registration wires the model into the global registry
# ---------------------------------------------------------------------------

def test_bootstrap_registers_saved_model(tmp_models_dir, tmp_registry, fast_cpcv):
    """After a model is saved, bootstrap_meta_registry() registers it."""
    # Train + save into the temp models dir.
    labeler = trainer.train_signal("timeseries_momentum", force_synthetic=True, seed=1)
    assert labeler is not None

    # Registry starts empty (autouse fixture reset).
    assert not meta_labeling.global_meta_registry.has("timeseries_momentum")

    registered = bootstrap_meta_registry(signal_ids=("timeseries_momentum",))

    assert registered == ["timeseries_momentum"]
    assert meta_labeling.global_meta_registry.has("timeseries_momentum")


def test_bootstrap_noop_when_no_model(tmp_models_dir):
    """Strict no-op: with no saved model, nothing is registered (current behavior)."""
    assert list(tmp_models_dir.glob("*.pkl")) == [], "temp dir must be empty"

    registered = bootstrap_meta_registry()

    assert registered == [], "no models → nothing registered"
    for sid in META_LABELED_SIGNAL_IDS:
        assert not meta_labeling.global_meta_registry.has(sid), (
            f"{sid} must NOT be registered when no model exists"
        )


def test_bootstrap_respects_disabled_setting(tmp_models_dir, tmp_registry, monkeypatch, fast_cpcv):
    """META_LABELING_ENABLED=False disables registration even with a saved model."""
    trainer.train_signal("timeseries_momentum", force_synthetic=True, seed=1)

    from settings import settings
    monkeypatch.setattr(settings, "META_LABELING_ENABLED", False)

    registered = bootstrap_meta_registry(signal_ids=("timeseries_momentum",))

    assert registered == []
    assert not meta_labeling.global_meta_registry.has("timeseries_momentum")


# ---------------------------------------------------------------------------
# 3. End-to-end: registered LOW-confidence labeler fires the aggregator gate
# ---------------------------------------------------------------------------

def test_registered_low_confidence_fires_hard_gate(tmp_models_dir):
    """A registered MetaLabeler returning P < 0.4 forces meta_label_composite=0.

    Reuses the aggregator wiring pattern from tests/test_meta_labeler_uplift.py.
    We register the low-confidence labeler directly into the (real) global
    registry — the bootstrap→save→load round-trip is exercised separately in
    ``test_bootstrap_registers_saved_model``; a locally-defined MetaLabeler
    subclass cannot be pickled, so we register the instance directly here while
    still driving the REAL global registry the aggregator queries.
    """
    from datetime import datetime as _dt
    from signals.aggregator import SignalAggregator
    from signals.registry import global_registry
    from signals.base import SignalContext
    from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO

    # A MetaLabeler subclass that always returns P=0.1 (< META_LABEL_MIN_CONFIDENCE).
    class AlwaysLowMetaLabeler(MetaLabeler):
        def predict_proba_scalar(self, X):  # noqa: D401
            return 0.1

    low = AlwaysLowMetaLabeler(signal_id="timeseries_momentum")
    low._model = object()  # mark as "trained" so predict paths engage
    low._n_train_samples = 100
    meta_labeling.global_meta_registry.register(low)
    assert meta_labeling.global_meta_registry.has("timeseries_momentum")

    aggregator = SignalAggregator(global_registry)

    bar = MarketBarDTO(
        date=_dt(2024, 1, 1), ticker="AAPL",
        open_price=149.0, high_price=151.0, low_price=148.0,
        close_price=150.0, volume=1_000_000,
    )
    fundamentals = FundamentalDataDTO(
        ticker="AAPL", pe_ratio=25.0, pb_ratio=5.0, dividend_yield=0.01,
        book_value=30.0, eps_trailing=6.0, dividend_growth_rate=0.05,
        payout_ratio=0.3, sector="Technology", company_name="Apple Inc",
        market_cap=2_500_000_000_000.0,
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, vix_value=15.0, sahm_rule_indicator=0.1,
        high_yield_oas=300.0, inflation_rate=0.03,
    )
    context = SignalContext(bar=bar, fundamentals=fundamentals, macro=macro)
    row = pd.Series({
        "current_price": 150.0, "Close": 150.0, "RSI_2": 50.0, "SMA_5": 149.0,
        "SMA_200": 140.0, "ROC_12M": 0.1, "GARCH_Vol": 0.15, "garch_vol": 0.15,
        "sector": "Technology", "ticker": "AAPL",
        "forecast_price": 155.0, "trend_strength": 60.0, "atr": 2.0,
        "macd_line": 0.5, "macd_signal": 0.3, "aroon_osc": 40.0,
        "rsi": 55.0, "sortino_ratio": 1.0, "max_drawdown": 0.1,
        "relative_strength": 0.8, "edge_ratio": 1.2,
        "chandelier_long": 145.0, "chandelier_short": 155.0,
    })

    _, _, _, _, _, composite = aggregator.aggregate(row, context)

    assert composite == 0.0, (
        f"Expected meta_label_composite=0.0 when registered MetaLabeler P=0.1 "
        f"< 0.4, got {composite}"
    )


# ---------------------------------------------------------------------------
# 4. Real CPCV populates non-null numeric metrics + honest deployability gate
# ---------------------------------------------------------------------------

def test_real_cpcv_populates_metrics_and_gate(tmp_models_dir, tmp_registry):
    """A REAL CPCV run writes non-null numeric cpcv_dsr/pbo and a gate-derived
    deployable flag (NOT the fast stub — this is the end-to-end honesty check).

    Uses a small universe to keep the single real CPCV sweep bounded.
    """
    import yaml
    from ml.registry_io import compute_deployable

    small_universe = ("AAPL", "MSFT", "SPY", "JPM")
    path = trainer.train_signal(
        "timeseries_momentum",
        force_synthetic=True,
        seed=3,
        universe=small_universe,
    )
    assert path is not None and path.exists()

    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"]["meta_labeler_timeseries_momentum"]

    # Non-null numeric metrics (no fabrication, but no longer structurally null).
    assert row["cpcv_dsr"] is not None, "CPCV DSR must be populated by a real run"
    assert row["pbo"] is not None, "CPCV PBO must be populated by a real run"
    assert isinstance(row["cpcv_dsr"], float)
    assert isinstance(row["pbo"], float)
    assert 0.0 <= row["pbo"] <= 1.0, "PBO is a probability in [0, 1]"

    # deployable is the HONEST gate applied to the real metrics — never spoofed.
    assert row["deployable"] is compute_deployable(row["cpcv_dsr"], row["pbo"])


def test_bad_model_stays_non_deployable_gate_is_genuine(tmp_models_dir, tmp_registry, monkeypatch):
    """HONESTY: metrics failing the gate → deployable:false; passing → true.

    Proves ``deployable`` is derived from the CPCV metrics (via
    registry_io.compute_deployable), not hardcoded — a genuinely-bad model is
    honestly non-deployable, and the gate CAN flip true only when the metrics
    actually clear DSR>0.95 AND PBO<0.5.
    """
    import yaml

    # (dsr, pbo, expected_deployable)
    cases = [
        (0.40, 0.90, False),   # bad DSR + bad PBO
        (0.99, 0.90, False),   # good DSR but overfit (PBO ≥ 0.5)
        (0.40, 0.10, False),   # low overfit but DSR fails
        (0.99, 0.10, True),    # only this clears BOTH gates
    ]
    for i, (dsr, pbo, expected) in enumerate(cases):
        monkeypatch.setattr(
            trainer, "compute_cpcv_metrics",
            lambda *a, _d=dsr, _p=pbo, **k: {"dsr": _d, "pbo": _p, "mean_oos_sharpe": 0.5},
        )
        path = trainer.train_signal("timeseries_momentum", force_synthetic=True, seed=10 + i)
        assert path is not None

        data = yaml.safe_load(tmp_registry.read_text())
        row = data["models"]["meta_labeler_timeseries_momentum"]
        assert row["cpcv_dsr"] == dsr and row["pbo"] == pbo
        assert row["deployable"] is expected, (
            f"dsr={dsr}, pbo={pbo} should give deployable={expected}, "
            f"got {row['deployable']}"
        )
