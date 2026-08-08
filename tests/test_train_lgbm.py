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

# xdist_group pins every test in this module to the same worker under
# `--dist loadgroup` (CI/Makefile) -- without it, the default `--dist load`
# distribution can split these tests across workers, silently rebuilding the
# module-scoped `trained_model_fixture`/`tmp_registry_module` fixtures per
# worker and eating the whole consolidation win below.
pytestmark = pytest.mark.xdist_group("train_lgbm")


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


def test_update_model_metrics_cpcv_oos_fields_round_trip(tmp_registry):
    """cpcv_mean_oos_sharpe / cpcv_mean_oos_max_dd persist verbatim into the
    written YAML — the new provenance fields this PR adds."""
    entry = registry_io.update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-08-03",
        cpcv_dsr=0.98,
        pbo=0.20,
        n_train=123,
        path=tmp_registry,
        cpcv_mean_oos_sharpe=1.23,
        cpcv_mean_oos_max_dd=-0.08,
    )
    assert entry["cpcv_mean_oos_sharpe"] == pytest.approx(1.23)
    assert entry["cpcv_mean_oos_max_dd"] == pytest.approx(-0.08)

    data = yaml.safe_load(tmp_registry.read_text())
    row = data["models"]["lgbm_ranker"]
    assert row["cpcv_mean_oos_sharpe"] == pytest.approx(1.23)
    assert row["cpcv_mean_oos_max_dd"] == pytest.approx(-0.08)


def test_update_model_metrics_cpcv_oos_fields_default_none(tmp_registry):
    """Omitting the two new kwargs stores None (backward-compatible default)."""
    entry = registry_io.update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-08-03",
        cpcv_dsr=0.98,
        pbo=0.20,
        n_train=123,
        path=tmp_registry,
    )
    assert entry["cpcv_mean_oos_sharpe"] is None
    assert entry["cpcv_mean_oos_max_dd"] is None


def test_update_model_metrics_cpcv_oos_fields_never_spoof_deployable(tmp_registry):
    """Anti-spoofing guard (mirrors the existing provenance round-trip test):
    a high cpcv_mean_oos_sharpe / a shallow cpcv_mean_oos_max_dd -- values
    that would look 'good' -- must NOT rescue a failing DSR/PBO gate. The
    deployable flag is derived from cpcv_dsr/pbo alone, exactly as before
    this PR."""
    entry = registry_io.update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-08-03",
        cpcv_dsr=0.10,   # fails the >0.95 gate
        pbo=0.60,        # fails the <0.5 gate
        n_train=123,
        path=tmp_registry,
        cpcv_mean_oos_sharpe=5.0,     # deliberately "great looking"
        cpcv_mean_oos_max_dd=-0.001,  # deliberately "great looking"
    )
    assert entry["deployable"] is False
    assert entry["deployable"] == registry_io.compute_deployable(0.10, 0.60)

    # Conversely: a genuinely passing gate stays deployable regardless of what
    # the two new fields are set to (including deliberately "bad-looking"
    # values) -- proves the gate is never coupled to these fields either way.
    entry2 = registry_io.update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-08-03",
        cpcv_dsr=0.99,
        pbo=0.10,
        n_train=123,
        path=tmp_registry,
        cpcv_mean_oos_sharpe=-3.0,
        cpcv_mean_oos_max_dd=-0.90,
    )
    assert entry2["deployable"] is True
    assert entry2["deployable"] == registry_io.compute_deployable(0.99, 0.10)


# ──────────────────────────────────────────────────────────────────────────────
# Training-job end-to-end tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def tmp_registry_module(tmp_path_factory) -> Path:
    """Module-scoped -- instantiates before the function-scoped autouse
    GDELT/FMP throttle resets in conftest.py; safe here because this fixture
    does not touch either. A writable copy of the real registry.yaml, shared
    by ``trained_model_fixture`` below."""
    tmp_path = tmp_path_factory.mktemp("tmp_registry_module")
    src = _REPO_ROOT / "ml" / "registry.yaml"
    dst = tmp_path / "registry.yaml"
    shutil.copyfile(src, dst)
    return dst


@pytest.fixture(scope="module")
def trained_model_fixture(tmp_path_factory, tmp_registry_module):
    """Module-scoped -- see tmp_registry_module's note above; same rationale.

    Runs ONE run_training() call against the shared _DistinctEngine() panel,
    consumed by the three tests below that each assert on a different
    property of the result -- avoids each one paying for its own full
    training run."""
    tmp_path = tmp_path_factory.mktemp("trained_model")
    save_path = tmp_path / "lgbm_latest.pkl"
    summary = train_lgbm.run_training(
        _TICKERS,
        data_engine=_DistinctEngine(),
        save_path=save_path,
        registry_path=tmp_registry_module,
        historical_store=False,
    )
    return {
        "summary": summary,
        "save_path": save_path,
        "registry_path": tmp_registry_module,
    }


def test_training_produces_model_and_real_metrics(trained_model_fixture):
    summary = trained_model_fixture["summary"]
    save_path = trained_model_fixture["save_path"]
    registry_path = trained_model_fixture["registry_path"]

    # Model artifact exists.
    assert save_path.exists(), "model pickle was not written"
    assert summary["model_path"] == str(save_path)
    assert summary["n_train"] > 0

    # Real (non-null) metrics.
    assert summary["dsr"] is not None
    assert summary["pbo"] is not None
    assert 0.0 <= summary["pbo"] <= 1.0

    # Registry row got the real metrics.
    data = yaml.safe_load(registry_path.read_text())
    row = data["models"]["lgbm_ranker"]
    assert row["trained_date"] is not None
    assert row["cpcv_dsr"] is not None
    assert row["pbo"] is not None
    assert row["n_train"] == summary["n_train"]
    # The real CPCV out-of-sample Sharpe / max drawdown flow all the way
    # through compute_cpcv_metrics -> update_model_metrics -> the YAML row
    # (previously silently discarded before reaching the registry).
    assert row["cpcv_mean_oos_sharpe"] is not None
    assert row["cpcv_mean_oos_max_dd"] is not None
    assert row["cpcv_mean_oos_sharpe"] == pytest.approx(summary["mean_oos_sharpe"])


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
        historical_store=False,
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


def test_deployable_flag_matches_gate_exactly(trained_model_fixture):
    summary = trained_model_fixture["summary"]
    registry_path = trained_model_fixture["registry_path"]
    expected = registry_io.compute_deployable(summary["dsr"], summary["pbo"])
    assert summary["deployable"] == expected

    data = yaml.safe_load(registry_path.read_text())
    assert data["models"]["lgbm_ranker"]["deployable"] == expected


def test_empty_panel_no_crash_and_not_deployable(tmp_path, tmp_registry):
    save_path = tmp_path / "lgbm_latest.pkl"
    summary = train_lgbm.run_training(
        _TICKERS,
        data_engine=_EmptyEngine(),
        save_path=save_path,
        registry_path=tmp_registry,
        historical_store=False,
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


def test_trained_model_is_loadable_and_non_neutral(trained_model_fixture):
    """A freshly trained+saved model round-trips through LGBMCrossSectionalRanker.load
    and produces non-neutral (not all 0.5) cross-sectional ranks."""
    from ml.lgbm_ranker import LGBMCrossSectionalRanker
    from ml.feature_engineering import build_pit_feature_matrix

    save_path = trained_model_fixture["save_path"]
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


# ──────────────────────────────────────────────────────────────────────────────
# settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED -- t1 threading through
# compute_cpcv_metrics() (PR #648 / #650's remaining scope)
# ──────────────────────────────────────────────────────────────────────────────
#
# These tests stub run_cpcv_evaluation / LGBMCrossSectionalRanker.train to
# capture *what compute_cpcv_metrics passes*, rather than running the full
# real CPCV + LightGBM fit -- fast, deterministic, and isolates exactly the
# threading contract the task requires:
#   * flag on  -> run_cpcv_evaluation gets a real (non-None) t1, and the
#                 MultiIndex panel is handed through un-flattened;
#   * flag off -> run_cpcv_evaluation gets t1=None and a flattened,
#                 date-indexed X carrying the legacy "_ticker" column --
#                 byte-identical to the pre-existing behavior;
#   * flag on  -> the fold-level strategy_fn's inner ranker.train() call
#                 receives a non-None t1 and use_native_multiindex_cv=True;
#   * flag off -> the inner ranker.train() call receives no t1 / no
#                 use_native_multiindex_cv kwarg at all (matches the
#                 pre-existing `ranker.train(X_tr_mi, y_tr_al)` call shape).


def _make_multiindex_training_panel(
    n_dates: int = 10, n_tickers: int = 6, seed: int = 0
) -> "train_lgbm.TrainingPanel":
    """Small synthetic (date, ticker) TrainingPanel with a real forward-window t1."""
    from ml.feature_engineering import FEATURE_COLUMNS

    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_dates, freq="B")
    tickers = [f"T{i}" for i in range(n_tickers)]

    rows, y_rows, t1_rows = [], [], []
    for i, dt in enumerate(dates):
        idx = pd.MultiIndex.from_tuples([(dt, t) for t in tickers], names=["date", "ticker"])
        feat = pd.DataFrame(
            rng.normal(0, 1, size=(n_tickers, len(FEATURE_COLUMNS))),
            index=idx, columns=FEATURE_COLUMNS,
        )
        rows.append(feat)
        y_rows.append(pd.Series(rng.uniform(0, 1, n_tickers), index=idx))
        # Real forward-window end time, not a synthesized "next row".
        end_dt = dates[min(i + 3, n_dates - 1)]
        t1_rows.append(pd.Series([end_dt] * n_tickers, index=idx))

    X = pd.concat(rows)
    y = pd.concat(y_rows)
    t1 = pd.concat(t1_rows)
    return train_lgbm.TrainingPanel(X=X, y=y, t1=t1, n_dates=n_dates)


class TestNativeMultiIndexT1Threading:
    def test_flag_on_passes_real_t1_to_run_cpcv_evaluation(self, monkeypatch):
        panel = _make_multiindex_training_panel()
        monkeypatch.setattr(
            "settings.settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", True
        )
        captured = {}

        def _fake_run_cpcv_evaluation(*, strategy_fn, X, y, t1, n_splits, n_test_splits):
            captured["X"], captured["y"], captured["t1"] = X, y, t1
            return {"paths": []}

        monkeypatch.setattr(train_lgbm, "run_cpcv_evaluation", _fake_run_cpcv_evaluation)
        result = train_lgbm.compute_cpcv_metrics(panel)

        assert result == {
            "dsr": None, "pbo": None, "mean_oos_sharpe": None, "mean_oos_max_dd": None,
        }  # empty "paths" -> honest null, unrelated to what we're asserting below
        assert captured["t1"] is not None
        assert isinstance(captured["t1"], pd.Series)
        assert isinstance(captured["X"].index, pd.MultiIndex)
        assert "_ticker" not in captured["X"].columns
        # t1 is exactly panel.t1, realigned/sorted to X's (date, ticker) index
        # -- not a synthesized default.
        pd.testing.assert_series_equal(
            captured["t1"].sort_index(), panel.t1.sort_index(), check_names=False,
        )

    def test_flag_off_passes_t1_none_to_run_cpcv_evaluation(self, monkeypatch):
        panel = _make_multiindex_training_panel()
        monkeypatch.setattr(
            "settings.settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", False
        )
        captured = {}

        def _fake_run_cpcv_evaluation(*, strategy_fn, X, y, t1, n_splits, n_test_splits):
            captured["X"], captured["t1"] = X, t1
            return {"paths": []}

        monkeypatch.setattr(train_lgbm, "run_cpcv_evaluation", _fake_run_cpcv_evaluation)
        train_lgbm.compute_cpcv_metrics(panel)

        assert captured["t1"] is None
        # Flatten path -- date-only index, legacy "_ticker" column present,
        # exactly the pre-existing construction.
        assert not isinstance(captured["X"].index, pd.MultiIndex)
        assert "_ticker" in captured["X"].columns

    def test_flag_on_threads_t1_into_inner_ranker_train_call(self, monkeypatch):
        """The fold-level strategy_fn's inner ranker.train() call receives a
        non-None t1 and use_native_multiindex_cv=True when the flag is on."""
        panel = _make_multiindex_training_panel()
        monkeypatch.setattr(
            "settings.settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", True
        )
        captured = {}

        def _fake_run_cpcv_evaluation(*, strategy_fn, X, y, t1, n_splits, n_test_splits):
            captured["fn"], captured["X"], captured["y"] = strategy_fn, X, y
            return {"paths": []}

        monkeypatch.setattr(train_lgbm, "run_cpcv_evaluation", _fake_run_cpcv_evaluation)
        train_lgbm.compute_cpcv_metrics(panel)

        X_for_cv, y_for_cv = captured["X"], captured["y"]
        split = len(X_for_cv) // 2
        X_tr, y_tr = X_for_cv.iloc[:split], y_for_cv.iloc[:split]
        X_te, y_te = X_for_cv.iloc[split:], y_for_cv.iloc[split:]

        train_calls = []

        def _spy_train(self, X, y, t1=None, use_native_multiindex_cv=None):
            train_calls.append({"t1": t1, "use_native_multiindex_cv": use_native_multiindex_cv})
            self._model = None  # skip real LightGBM fit -- only the call shape matters here
            return self

        monkeypatch.setattr(train_lgbm.LGBMCrossSectionalRanker, "train", _spy_train)
        captured["fn"](X_tr, y_tr, X_te, y_te)

        assert train_calls, "strategy_fn never invoked ranker.train()"
        for call in train_calls:
            assert call["t1"] is not None
            assert call["use_native_multiindex_cv"] is True

    def test_flag_off_inner_ranker_train_call_has_no_t1(self, monkeypatch):
        """Byte-identical-when-off contract: the inner ranker.train() call
        keeps the exact pre-existing shape -- no t1, no
        use_native_multiindex_cv kwarg -- when the flag is off."""
        panel = _make_multiindex_training_panel()
        monkeypatch.setattr(
            "settings.settings.LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", False
        )
        captured = {}

        def _fake_run_cpcv_evaluation(*, strategy_fn, X, y, t1, n_splits, n_test_splits):
            captured["fn"], captured["X"], captured["y"] = strategy_fn, X, y
            return {"paths": []}

        monkeypatch.setattr(train_lgbm, "run_cpcv_evaluation", _fake_run_cpcv_evaluation)
        train_lgbm.compute_cpcv_metrics(panel)

        X_for_cv, y_for_cv = captured["X"], captured["y"]
        split = len(X_for_cv) // 2
        X_tr, y_tr = X_for_cv.iloc[:split], y_for_cv.iloc[:split]
        X_te, y_te = X_for_cv.iloc[split:], y_for_cv.iloc[split:]

        train_calls = []

        def _spy_train(self, X, y, t1=None, use_native_multiindex_cv=None):
            train_calls.append({"t1": t1, "use_native_multiindex_cv": use_native_multiindex_cv})
            self._model = None
            return self

        monkeypatch.setattr(train_lgbm.LGBMCrossSectionalRanker, "train", _spy_train)
        captured["fn"](X_tr, y_tr, X_te, y_te)

        assert train_calls, "strategy_fn never invoked ranker.train()"
        for call in train_calls:
            assert call["t1"] is None
            assert call["use_native_multiindex_cv"] is None


class TestLongShortReturnsMultiIndexDateGrouping:
    """_long_short_returns must group by the MultiIndex's "date" level, not
    the raw (date, ticker) tuples, when handed a still-MultiIndex slice
    (the native-CV path) -- otherwise every group collapses to size 1 and
    every date is silently skipped (< 2 rows per group)."""

    def test_multiindex_slice_groups_by_date_level(self):
        from ml.feature_engineering import FEATURE_COLUMNS

        n_dates, n_tickers = 4, 6
        dates = pd.date_range("2022-02-01", periods=n_dates, freq="B")
        tickers = [f"T{i}" for i in range(n_tickers)]
        idx = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
        feat_cols = list(FEATURE_COLUMNS)[:2]
        X_slice = pd.DataFrame(
            np.random.default_rng(1).normal(size=(len(idx), len(feat_cols))),
            index=idx, columns=feat_cols,
        )
        y_slice = pd.Series(
            np.random.default_rng(2).uniform(size=len(idx)), index=idx,
        )

        class _StubRanker:
            def predict(self, X):
                return np.arange(len(X), dtype=float)

        result = train_lgbm._long_short_returns(_StubRanker(), X_slice, y_slice, feat_cols)

        # One return per date -- proves grouping collapsed to n_dates groups,
        # not n_dates * n_tickers groups of size 1 (which would yield an
        # empty Series since every group would be skipped as len(grp) < 2).
        assert len(result) == n_dates
        assert not result.empty

    def test_flat_index_slice_still_groups_by_date(self):
        """Regression guard: the pre-existing flat-index behavior (used by
        both the flag-off path here and scripts/refresh_validations.py's
        _build_lgbm_ranker_adapter) is unaffected."""
        from ml.feature_engineering import FEATURE_COLUMNS

        n_dates, n_tickers = 4, 6
        dates = pd.date_range("2022-02-01", periods=n_dates, freq="B")
        flat_dates = np.repeat(dates.values, n_tickers)
        feat_cols = list(FEATURE_COLUMNS)[:2]
        X_slice = pd.DataFrame(
            np.random.default_rng(3).normal(size=(len(flat_dates), len(feat_cols))),
            index=pd.Index(flat_dates), columns=feat_cols,
        )
        y_slice = pd.Series(
            np.random.default_rng(4).uniform(size=len(flat_dates)), index=X_slice.index,
        )

        class _StubRanker:
            def predict(self, X):
                return np.arange(len(X), dtype=float)

        result = train_lgbm._long_short_returns(_StubRanker(), X_slice, y_slice, feat_cols)
        assert len(result) == n_dates
        assert not result.empty
