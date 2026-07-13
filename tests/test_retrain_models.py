"""
Tests for scripts/retrain_models.py — the automated walk-forward retraining
orchestrator.

The heavy trainers (``run_training`` / ``train_signal``) are mocked so these
tests are fast, fully offline, and don't mutate the real ml/registry.yaml. We
assert the orchestrator's contract:

  (a) it calls BOTH trainers — the LGBM ranker once and the meta-labeler once
      per signal in META_LABELED_SIGNAL_IDS;
  (b) it survives one trainer raising (records the crash + continues, the other
      models still get trained);
  (c) it exits 0 when models are merely non-deployable, and non-zero only on a
      hard crash;
  (d) it prints a per-model summary.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from ml.meta_bootstrap import META_LABELED_SIGNAL_IDS
from scripts import retrain_models


# A fake registry the summary reader can load — mixed deployable states.
def _fake_registry() -> dict:
    models = {
        "lgbm_ranker": {
            "trained_date": "2026-07-13",
            "cpcv_dsr": 0.1,
            "pbo": 0.3,
            "deployable": False,
        },
    }
    for sig in META_LABELED_SIGNAL_IDS:
        models[f"meta_labeler_{sig}"] = {
            "trained_date": "2026-07-13",
            "cpcv_dsr": None,
            "pbo": None,
            "deployable": False,
        }
    return {"models": models}


@pytest.fixture
def patched_trainers():
    """Patch both trainers with success-returning mocks."""
    lgbm_summary = {"dsr": 0.1, "pbo": 0.3, "n_train": 100, "deployable": False}
    with mock.patch.object(
        retrain_models, "run_training", return_value=lgbm_summary
    ) as m_lgbm, mock.patch.object(
        retrain_models, "train_signal", return_value=Path("ml/models/fake.pkl")
    ) as m_meta, mock.patch.object(
        retrain_models, "load_registry", return_value=_fake_registry()
    ):
        yield m_lgbm, m_meta


# ---------------------------------------------------------------------------
# (a) calls both trainers, once each per signal
# ---------------------------------------------------------------------------

def test_calls_both_trainers(patched_trainers):
    m_lgbm, m_meta = patched_trainers

    report = retrain_models.retrain_all(offline=True)

    # LGBM ranker trained exactly once.
    assert m_lgbm.call_count == 1
    # Meta-labeler trained once per configured signal.
    assert m_meta.call_count == len(META_LABELED_SIGNAL_IDS)
    trained_signals = {c.args[0] for c in m_meta.call_args_list}
    assert trained_signals == set(META_LABELED_SIGNAL_IDS)

    # Every model recorded a successful (non-crash) result.
    assert all(r.ok for r in report.results)
    assert not report.any_crash
    # One lgbm + one row per signal.
    assert len(report.results) == 1 + len(META_LABELED_SIGNAL_IDS)


def test_offline_flag_threaded_to_trainers(patched_trainers):
    m_lgbm, m_meta = patched_trainers

    retrain_models.retrain_all(offline=True)

    # offline propagates as offline= to LGBM and force_synthetic= to meta.
    assert m_lgbm.call_args.kwargs.get("offline") is True
    assert m_meta.call_args.kwargs.get("force_synthetic") is True


def test_signals_filter(patched_trainers):
    _, m_meta = patched_trainers
    only = [META_LABELED_SIGNAL_IDS[0]]

    retrain_models.retrain_all(offline=True, signals=only)

    assert m_meta.call_count == 1
    assert m_meta.call_args.args[0] == only[0]


def test_tickers_filter(patched_trainers):
    m_lgbm, _ = patched_trainers

    retrain_models.retrain_all(offline=True, tickers=["AAA", "BBB"])

    assert m_lgbm.call_args.args[0] == ["AAA", "BBB"]


# ---------------------------------------------------------------------------
# (b) survives one trainer raising — records + continues
# ---------------------------------------------------------------------------

def test_meta_trainer_raises_others_still_trained():
    first_signal = META_LABELED_SIGNAL_IDS[0]

    def _flaky(signal_id, **kwargs):
        if signal_id == first_signal:
            raise RuntimeError("boom in meta training")
        return Path("ml/models/ok.pkl")

    with mock.patch.object(
        retrain_models, "run_training",
        return_value={"dsr": 0.1, "pbo": 0.3, "n_train": 10, "deployable": False},
    ) as m_lgbm, mock.patch.object(
        retrain_models, "train_signal", side_effect=_flaky
    ) as m_meta, mock.patch.object(
        retrain_models, "load_registry", return_value=_fake_registry()
    ):
        report = retrain_models.retrain_all(offline=True)

    # LGBM still ran; every meta signal was still attempted.
    assert m_lgbm.call_count == 1
    assert m_meta.call_count == len(META_LABELED_SIGNAL_IDS)

    # The flaky one is recorded as a crash; the rest are ok.
    crashed_keys = {r.model_key for r in report.crashed}
    assert crashed_keys == {f"meta_labeler_{first_signal}"}
    assert report.any_crash is True
    # The other signals + lgbm are ok.
    ok_keys = {r.model_key for r in report.results if r.ok}
    assert "lgbm_ranker" in ok_keys
    if len(META_LABELED_SIGNAL_IDS) > 1:
        assert f"meta_labeler_{META_LABELED_SIGNAL_IDS[1]}" in ok_keys


def test_lgbm_trainer_raises_meta_still_trained():
    with mock.patch.object(
        retrain_models, "run_training", side_effect=RuntimeError("lgbm crash")
    ) as m_lgbm, mock.patch.object(
        retrain_models, "train_signal", return_value=Path("ml/models/ok.pkl")
    ) as m_meta, mock.patch.object(
        retrain_models, "load_registry", return_value=_fake_registry()
    ):
        report = retrain_models.retrain_all(offline=True)

    assert m_lgbm.call_count == 1
    # Meta-labelers still trained despite the LGBM crash.
    assert m_meta.call_count == len(META_LABELED_SIGNAL_IDS)
    assert {r.model_key for r in report.crashed} == {"lgbm_ranker"}
    assert report.any_crash is True


def test_meta_skip_returns_none_is_not_a_crash():
    # train_signal returning None (insufficient data) is a skip, not an error.
    with mock.patch.object(
        retrain_models, "run_training",
        return_value={"dsr": 0.1, "pbo": 0.3, "n_train": 10, "deployable": False},
    ), mock.patch.object(
        retrain_models, "train_signal", return_value=None
    ), mock.patch.object(
        retrain_models, "load_registry", return_value=_fake_registry()
    ):
        report = retrain_models.retrain_all(offline=True)

    assert not report.any_crash
    assert all(r.ok for r in report.results)


# ---------------------------------------------------------------------------
# (c) exit codes: 0 when non-deployable, non-zero on a hard crash
# ---------------------------------------------------------------------------

def test_main_exit_zero_when_non_deployable(patched_trainers):
    # All trainers succeed; models are non-deployable per the fake registry.
    rc = retrain_models.main(["--offline"])
    assert rc == 0


def test_main_exit_nonzero_on_hard_crash():
    with mock.patch.object(
        retrain_models, "run_training", side_effect=RuntimeError("kaboom")
    ), mock.patch.object(
        retrain_models, "train_signal", return_value=Path("ml/models/ok.pkl")
    ), mock.patch.object(
        retrain_models, "load_registry", return_value=_fake_registry()
    ):
        rc = retrain_models.main(["--offline"])
    assert rc != 0


# ---------------------------------------------------------------------------
# (d) prints a per-model summary
# ---------------------------------------------------------------------------

def test_prints_per_model_summary(patched_trainers, capsys):
    retrain_models.main(["--offline"])
    out = capsys.readouterr().out

    assert "Retraining Summary" in out
    assert "lgbm_ranker" in out
    for sig in META_LABELED_SIGNAL_IDS:
        assert f"meta_labeler_{sig}" in out
    # Deployability is surfaced honestly in the summary.
    assert "deployable=False" in out


def test_summary_marks_crash(capsys):
    with mock.patch.object(
        retrain_models, "run_training", side_effect=RuntimeError("kaboom")
    ), mock.patch.object(
        retrain_models, "train_signal", return_value=Path("ml/models/ok.pkl")
    ), mock.patch.object(
        retrain_models, "load_registry", return_value=_fake_registry()
    ):
        retrain_models.main(["--offline"])
    out = capsys.readouterr().out
    assert "CRASHED" in out
