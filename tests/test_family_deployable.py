"""Tests for the ADVISORY family-wise deployability flag on ValidationReport.

`family_deployable` / `family_bh_significant` surface whether a strategy survives
family-wise (Benjamini-Hochberg) multiple-testing correction, in addition to
passing the single-strategy gates. They are ADVISORY ONLY and must never change
the hard `deployable` decision (the family correction depends on which sibling
summaries happen to be on disk, so it is unsuitable as a hard gate).
"""
import numpy as np

from validation.harness import ValidationReport


def _make_report(name="strat_a", *, dsr=0.99, pbo=0.0, sharpe=1.0, max_dd=0.10,
                 family_multiple_testing=None):
    """A ValidationReport that PASSES every single-strategy gate by default."""
    return ValidationReport(
        name=name,
        start_date="2010-01-01",
        end_date="2020-01-01",
        sharpe=sharpe,
        sortino=1.0,
        calmar=1.0,
        max_dd=max_dd,
        turnover=0.1,
        hit_rate=0.55,
        avg_trade_pct=0.01,
        dsr=dsr,
        pbo=pbo,
        bias_report={},
        walk_forward_60_40=1.0,
        walk_forward_70_30=1.0,
        walk_forward_80_20=1.0,
        distribution=np.array([0.1, 0.2]),
        paths=[],
        n_trials=1,
        family_multiple_testing=family_multiple_testing,
    )


def _fmt(ids, rejected):
    return {"strategy_ids": ids, "bh_rejected": rejected}


def test_family_flags_none_before_sweep():
    """Until the family sweep runs, both advisory flags are None (unknown),
    NOT False — an unknown family verdict must not read as a rejection."""
    r = _make_report(family_multiple_testing=None)
    assert r.family_bh_significant is None
    assert r.family_deployable is None
    # The hard gate is unaffected and still passes.
    assert r.deployable is True
    s = r.to_summary_dict()
    assert s["family_deployable"] is None
    assert s["family_bh_significant"] is None
    assert s["deployable"] is True


def test_family_deployable_true_when_significant_and_deployable():
    r = _make_report(name="strat_a",
                     family_multiple_testing=_fmt(["strat_a", "strat_b"], [True, False]))
    assert r.family_bh_significant is True
    assert r.deployable is True
    assert r.family_deployable is True


def test_family_deployable_false_when_not_significant():
    """A strategy that passes the single-strategy gates but is NOT family-wise
    significant is family_deployable=False, while `deployable` stays True."""
    r = _make_report(name="strat_b",
                     family_multiple_testing=_fmt(["strat_a", "strat_b"], [True, False]))
    assert r.family_bh_significant is False
    assert r.deployable is True          # hard gate unchanged
    assert r.family_deployable is False  # advisory is strictly more conservative


def test_family_deployable_false_when_not_deployable_even_if_significant():
    """family_deployable requires BOTH deployable AND family-significant."""
    r = _make_report(name="strat_a", dsr=0.50,  # fails DSR gate -> not deployable
                     family_multiple_testing=_fmt(["strat_a"], [True]))
    assert r.deployable is False
    assert r.family_bh_significant is True
    assert r.family_deployable is False


def test_family_flags_none_when_name_absent():
    """If this strategy isn't in the family lists, the verdict is unknown (None)."""
    r = _make_report(name="strat_missing",
                     family_multiple_testing=_fmt(["strat_a", "strat_b"], [True, True]))
    assert r.family_bh_significant is None
    assert r.family_deployable is None


def test_family_flags_none_on_malformed_lists():
    """Parallel-list length mismatch -> unknown, never a crash or a fabricated bool."""
    r = _make_report(name="strat_b",
                     family_multiple_testing=_fmt(["strat_a", "strat_b"], [True]))  # short
    assert r.family_bh_significant is None
    assert r.family_deployable is None


def test_deployable_never_influenced_by_family():
    """Sanity: the hard gate value is identical with and without family data."""
    without = _make_report(family_multiple_testing=None).deployable
    with_sig = _make_report(
        family_multiple_testing=_fmt(["strat_a"], [False])).deployable
    assert without == with_sig is True
