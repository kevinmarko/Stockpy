"""
Tests for scripts/train_lgbm.py and ml/registry_io.py
=====================================================
Fully offline: uses a small synthetic/injected data engine (no network) and a
temp registry + model path so nothing touches the tracked ml/registry.yaml or
writes into the real ml/models/ directory.

Coverage
--------
- Happy path: training on a small synthetic panel produces a persisted model
  file AND a registry row with real (non-null) metrics.
- Gate exactness: the persisted `deployable` flag matches the
  (cpcv_dsr > 0.95 AND pbo < 0.5) gate exactly.
- registry_io.compute_deployable truth table (including None-metric honesty).
- Empty panel: no crash, no artifact written, metrics null, deployable=false.
- Runtime load: a freshly-trained model is picked up by
  LGBMCrossSectionalRanker.load_latest() and yields non-neutral scores.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts import train_lgbm
from ml import registry_io

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_registry(tmp_path) -> Path:
    """A writable copy of the real registry.yaml in a temp dir."""
    src = _REPO_ROOT / "ml" / "registry.yaml"
    dst = tmp_path / "registry.yaml"
    shutil.copyfile(src, dst)
    return dst


class _DistinctEngine:
    """Offline engine: distinct random-walk price series per ticker."""

    def __init__(self, n_days: int = 400, seed: int = 3):
        self.n_days = n_days
        self.seed = seed

    def fetch_technical_raw(self, tickers):
        out = {}
        dates = pd.date_range(end=datetime.now(), periods=self.n_days, freq="B")
        for i, sym in enumerate(tickers):
            rng = np.random.RandomState(self.seed + i * 97)
            rets = rng.normal(rng.normal(0.0005, 0.0004), 0.012, self.n_days)
            closes = 100.0 * np.exp(np.cumsum(rets))
            out[sym] = pd.DataFrame(
                {
                    "Open": closes, "High": closes * 1.01,
                    "Low": closes * 0.99, "Close": closes,
                    "Volume": [1_000_000] * self.n_days,
                },
                index=dates,
            )
        return out


class _EmptyEngine:
    """Offline engine that returns no bars — exercises the empty-panel path."""

    def fetch_technical_raw(self, tickers):
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# registry_io unit tests
# ──────────────────────────────────────────────────────────────────────────────


def test_compute_deployable_truth_table():
    assert registry_io.compute_deployable(0.99, 0.10) is True   # both pass
    assert registry_io.compute_deployable(0.90, 0.10) is False  # DSR fails
    assert registry_io.compute_deployable(0.99, 0.60) is False  # PBO fails
    assert registry_io.compute_deployable(0.951, 0.499) is True # just inside
    assert registry_io.compute_deployable(0.95, 0.49) is False  # DSR not strictly >
    assert registry_io.compute_deployable(0.99, 0.5) is False   # PBO not strictly <
    # Honesty: a None metric can never deploy.
    assert registry_io.compute_deployable(None, 0.10) is False
    assert registry_io.compute_deployable(0.99, None) is False
    assert registry_io.compute_deployable(None, None) is False


def test_update_model_metrics_writes_and_derives_gate(tmp_registry):
    entry = registry_io.update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-07-05",
        cpcv_dsr=0.98,
        pbo=0.20,
        n_train=123,
        path=tmp_registry,
    )
    assert entry["deployable"] is True
    # Round-trip: re-load and confirm persisted.
    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"]["lgbm_ranker"]
    assert row["trained_date"] == "2026-07-05"
    assert row["cpcv_dsr"] == 0.98
    assert row["pbo"] == 0.20
    assert row["n_train"] == 123
    assert row["deployable"] is True
    # Other models untouched.
    assert data["models"]["meta_labeler_timeseries_momentum"]["deployable"] is False


def test_update_model_metrics_provenance_round_trip(tmp_registry):
    """Optional provenance survives the YAML round-trip, the banner header is
    preserved, and provenance never influences the deployable gate."""
    entry = registry_io.update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-07-06",
        cpcv_dsr=0.10,   # deliberately failing → not deployable despite rich provenance
        pbo=0.10,
        n_train=260,
        path=tmp_registry,
        hyperparameters={"num_leaves": 31},
        train_window={"start": "2020-01-01", "end": "2026-01-01", "n_dates": 260},
        features=["a", "b"],
        artifact_file="lgbm_20260706.pkl",
    )
    # Provenance did NOT rescue a failing gate.
    assert entry["deployable"] is False

    # Re-load through the public API and confirm every field survived.
    data = registry_io.load_registry(tmp_registry)
    row = data["models"]["lgbm_ranker"]
    assert row["hyperparameters"] == {"num_leaves": 31}
    assert row["train_window"] == {"start": "2020-01-01", "end": "2026-01-01", "n_dates": 260}
    assert row["features"] == ["a", "b"]
    assert row["artifact_file"] == "lgbm_20260706.pkl"
    assert row["deployable"] is False

    # The banner header comment block is re-emitted verbatim on write.
    text = tmp_registry.read_text()
    assert text.startswith("# InvestYo ML Model Registry")
    assert "# artifact_file:" in text
    assert "# hyperparameters:" in text
    assert "# train_window:" in text
    assert "# features:" in text


def test_update_model_metrics_null_is_not_deployable(tmp_registry):
    entry = registry_io.update_model_metrics(
        "lgbm_ranker", trained_date=None, cpcv_dsr=None, pbo=None,
        n_train=None, path=tmp_registry,
    )
    assert entry["deployable"] is False
    data = yaml.safe_load(tmp_registry.read_text())
    assert data["models"]["lgbm_ranker"]["cpcv_dsr"] is None
    assert data["models"]["lgbm_ranker"]["deployable"] is False


def test_update_unknown_key_raises(tmp_registry):
    with pytest.raises(KeyError):
        registry_io.update_model_metrics("does_not_exist", path=tmp_registry)


# ──────────────────────────────────────────────────────────────────────────────
# Training-job end-to-end tests
# ──────────────────────────────────────────────────────────────────────────────


def test_training_produces_model_and_real_metrics(tmp_path, tmp_registry):
    save_path = tmp_path / "lgbm_latest.pkl"
    summary = train_lgbm.run_training(
        _TICKERS,
        data_engine=_DistinctEngine(),
        save_path=save_path,
        registry_path=tmp_registry,
    )

    # Model artifact exists.
    assert save_path.exists(), "model pickle was not written"
    assert summary["model_path"] == str(save_path)
    assert summary["n_train"] > 0

    # Real (non-null) metrics.
    assert summary["dsr"] is not None
    assert summary["pbo"] is not None
    assert 0.0 <= summary["pbo"] <= 1.0

    # Registry row got the real metrics.
    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"]["lgbm_ranker"]
    assert row["trained_date"] is not None
    assert row["cpcv_dsr"] is not None
    assert row["pbo"] is not None
    assert row["n_train"] == summary["n_train"]


def test_default_save_path_is_dated_not_mutable_latest(tmp_path, tmp_registry, monkeypatch):
    """run_training(save_path=None) must NOT force a mutable *_latest.pkl name.

    Regression test: train_lgbm.py used to default save_path to a hardcoded
    ml/models/lgbm_latest.pkl, which meant every retraining overwrote the same
    binary and registry.artifact_file could never name a unique artifact. The
    fix removes that hardcoded default so None flows through to
    LGBMCrossSectionalRanker.save(None), which auto-dates to
    lgbm_<YYYYMMDD>.pkl. This test stubs .save() (never touching the real
    ml/models/ directory) and asserts (a) it is invoked with path=None when the
    caller supplies no save_path, and (b) run_training's returned model_path /
    registry artifact_file reflect whatever path .save() actually returns.
    """
    fake_dated_path = tmp_path / "lgbm_20260706.pkl"
    received_args = {}

    def _fake_save(self, path=None):
        received_args["path"] = path
        fake_dated_path.write_bytes(b"fake-pickle")
        return fake_dated_path

    monkeypatch.setattr(train_lgbm.LGBMCrossSectionalRanker, "save", _fake_save)

    summary = train_lgbm.run_training(
        _TICKERS,
        data_engine=_DistinctEngine(),
        save_path=None,
        registry_path=tmp_registry,
    )

    assert received_args["path"] is None, (
        "run_training must pass path=None through to ranker.save() by default "
        "so it self-dates, instead of forcing a mutable *_latest.pkl name"
    )
    assert summary["model_path"] == str(fake_dated_path)

    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"]["lgbm_ranker"]
    assert row["artifact_file"] == "lgbm_20260706.pkl"
    assert "latest" not in row["artifact_file"]


def test_deployable_flag_matches_gate_exactly(tmp_path, tmp_registry):
    save_path = tmp_path / "lgbm_latest.pkl"
    summary = train_lgbm.run_training(
        _TICKERS,
        data_engine=_DistinctEngine(),
        save_path=save_path,
        registry_path=tmp_registry,
    )
    expected = registry_io.compute_deployable(summary["dsr"], summary["pbo"])
    assert summary["deployable"] == expected

    data = yaml.safe_load(tmp_registry.read_text())
    assert data["models"]["lgbm_ranker"]["deployable"] == expected


def test_empty_panel_no_crash_and_not_deployable(tmp_path, tmp_registry):
    save_path = tmp_path / "lgbm_latest.pkl"
    summary = train_lgbm.run_training(
        _TICKERS,
        data_engine=_EmptyEngine(),
        save_path=save_path,
        registry_path=tmp_registry,
    )
    # No artifact written, honest null metrics, not deployable.
    assert not save_path.exists()
    assert summary["model_path"] is None
    assert summary["n_train"] == 0
    assert summary["dsr"] is None
    assert summary["pbo"] is None
    assert summary["deployable"] is False

    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"]["lgbm_ranker"]
    assert row["deployable"] is False
    assert row["cpcv_dsr"] is None


def test_trained_model_is_loadable_and_non_neutral(tmp_path, tmp_registry):
    """A freshly trained+saved model round-trips through LGBMCrossSectionalRanker.load
    and produces non-neutral (not all 0.5) cross-sectional ranks."""
    from ml.lgbm_ranker import LGBMCrossSectionalRanker
    from ml.feature_engineering import build_pit_feature_matrix

    save_path = tmp_path / "lgbm_latest.pkl"
    train_lgbm.run_training(
        _TICKERS,
        data_engine=_DistinctEngine(),
        save_path=save_path,
        registry_path=tmp_registry,
    )
    ranker = LGBMCrossSectionalRanker.load(save_path)
    assert ranker._model is not None

    udf = pd.DataFrame(
        {
            "ROC_12M": [0.2, -0.1, 0.05], "ROC_6M": [0.1, -0.05, 0.02],
            "GARCH_Vol": [0.2, 0.3, 0.25], "RSI": [60, 40, 50],
            "RSI_2": [80, 20, 50],
        },
        index=["AAA", "BBB", "CCC"],
    )
    feat = build_pit_feature_matrix(udf, macro_vix=18.0)
    scores = ranker.predict_score(feat)
    assert len(scores) == 3
    assert scores.notna().all()
    # Not all neutral 0.5 — the model is actually discriminating.
    assert (scores - 0.5).abs().max() > 1e-6
