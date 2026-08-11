"""
tests/test_multiple_testing.py
===============================
Unit tests for ``validation/multiple_testing.py`` — family-wise (across the
~17 ``signals/`` modules) multiple-testing correction: Benjamini-Hochberg
FDR control and family-corrected Deflated Sharpe Ratio.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from validation.metrics import deflated_sharpe_ratio
from validation.multiple_testing import (
    FamilyDSRResult,
    benjamini_hochberg,
    deflated_sharpe_family,
    format_multiple_testing_summary,
)


# ─────────────────────────────────────────────────────────────────────────────
# Benjamini-Hochberg
# ─────────────────────────────────────────────────────────────────────────────

class TestBenjaminiHochberg:
    def test_hand_computed_worked_example(self):
        """p = [0.001, 0.01, 0.02, 0.04, 0.5], alpha=0.05, m=5.

        BH thresholds (k/m)*alpha for sorted p-values (already ascending):
          k=1: 0.001 <= (1/5)*0.05 = 0.010  -> True
          k=2: 0.01  <= (2/5)*0.05 = 0.020  -> True
          k=3: 0.02  <= (3/5)*0.05 = 0.030  -> True
          k=4: 0.04  <= (4/5)*0.05 = 0.040  -> True (boundary, equality counts)
          k=5: 0.5   <= (5/5)*0.05 = 0.050  -> False

        Largest k satisfying the threshold is k=4 -> critical p-value = 0.04.
        Reject every p-value <= 0.04: indices 0,1,2,3 rejected; index 4 (0.5)
        not rejected.
        """
        pvalues = [0.001, 0.01, 0.02, 0.04, 0.5]
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        assert rejected == [True, True, True, True, False]

    def test_hand_computed_example_unsorted_input(self):
        """Same worked example, but with an unsorted / permuted input order —
        the output must be aligned with the ORIGINAL input order, not the
        sorted order."""
        pvalues = [0.5, 0.001, 0.04, 0.02, 0.01]
        # Original order: [0.5, 0.001, 0.04, 0.02, 0.01]
        # Expected rejections (same critical value 0.04 from the sorted case):
        #   0.5   -> False
        #   0.001 -> True
        #   0.04  -> True
        #   0.02  -> True
        #   0.01  -> True
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        assert rejected == [False, True, True, True, True]

    def test_empty_list(self):
        assert benjamini_hochberg([], alpha=0.05) == []

    def test_all_pvalues_far_above_alpha_nothing_rejected(self):
        pvalues = [0.9, 0.8, 0.95, 0.99]
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        assert rejected == [False, False, False, False]

    def test_all_pvalues_far_below_alpha_everything_rejected(self):
        pvalues = [1e-6, 1e-5, 1e-4, 1e-3]
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        assert rejected == [True, True, True, True]

    def test_single_pvalue_below_alpha_rejected(self):
        assert benjamini_hochberg([0.01], alpha=0.05) == [True]

    def test_single_pvalue_above_alpha_not_rejected(self):
        assert benjamini_hochberg([0.5], alpha=0.05) == [False]

    def test_nan_pvalue_never_rejected(self):
        """A NaN p-value (e.g. DSR could not be computed) must never be
        declared significant — treated as non-significant, not fabricated."""
        pvalues = [0.001, float("nan"), 0.02]
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        # Index 1 (NaN) must be False regardless of the other values.
        assert rejected[1] is False

    def test_more_conservative_than_uncorrected_alpha(self):
        """BH-adjusted rejection set should be a subset of what a naive
        uncorrected alpha=0.05 threshold would reject, when there are many
        hypotheses (illustrates the FDR-control tightening)."""
        rng = np.random.default_rng(42)
        pvalues = sorted(rng.uniform(0.0, 1.0, size=100).tolist())
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        naive_rejected = [p <= 0.05 for p in pvalues]
        n_bh = sum(rejected)
        n_naive = sum(naive_rejected)
        assert n_bh <= n_naive

    def test_return_type_and_length(self):
        pvalues = [0.01, 0.2, 0.03]
        rejected = benjamini_hochberg(pvalues, alpha=0.05)
        assert len(rejected) == len(pvalues)
        assert all(isinstance(r, (bool, np.bool_)) for r in rejected)

    def test_custom_alpha_changes_result(self):
        """A stricter alpha should reject no more than a looser one."""
        pvalues = [0.001, 0.01, 0.02, 0.04, 0.5]
        rejected_strict = benjamini_hochberg(pvalues, alpha=0.01)
        rejected_loose = benjamini_hochberg(pvalues, alpha=0.10)
        assert sum(rejected_strict) <= sum(rejected_loose)


# ─────────────────────────────────────────────────────────────────────────────
# Family-wise Deflated Sharpe Ratio
# ─────────────────────────────────────────────────────────────────────────────

class TestDeflatedSharpeFamily:
    def test_reuses_existing_dsr_function_for_single_strategy_value(self):
        """The single-strategy DSR computed inside deflated_sharpe_family
        must exactly match calling validation.metrics.deflated_sharpe_ratio
        directly with the same n_trials — confirms no reimplementation of
        the math, per the task's explicit instruction."""
        sr = 1.5
        n_trials = 10
        expected = deflated_sharpe_ratio(
            sr_observed=sr, n_trials=n_trials, sr_variance=0.5,
            skew=0.0, kurtosis=3.0, n_observations=500, freq=252,
        )
        results = deflated_sharpe_family(
            [sr], [n_trials], sr_variance=0.5, skew=0.0, kurtosis=3.0,
            n_observations=500, freq=252,
        )
        assert len(results) == 1
        assert results[0].dsr_single_strategy == pytest.approx(expected)

    def test_family_correction_is_more_conservative_with_many_trials(self):
        """The family-corrected DSR must be <= the single-strategy DSR for
        the same nominal Sharpe, when the family's total trial count is
        much larger than any one strategy's own trial count — this is the
        core claim of the task (family correction is stricter)."""
        sharpes = [2.0, 1.8, 1.5]
        n_trials_per_strategy = [10, 10, 10]  # own trials are small
        results = deflated_sharpe_family(
            sharpes, n_trials_per_strategy,
            sr_variance=0.5, skew=0.0, kurtosis=3.0,
            n_observations=1000, freq=252,
        )
        assert len(results) == 3
        for r in results:
            # family total = 30, much larger than any single strategy's own 10
            assert r.n_trials_family == 30
            assert r.dsr_family_corrected <= r.dsr_single_strategy + 1e-9

    def test_family_correction_stricter_scales_with_family_size(self):
        """Adding more sibling strategies (each with their own trials) to the
        family should only ever make the family-corrected DSR for a FIXED
        strategy more conservative (lower or equal), never higher."""
        sr = 2.0
        own_trials = 10

        small_family = deflated_sharpe_family(
            [sr], [own_trials], sr_variance=0.5, skew=0.0, kurtosis=3.0,
            n_observations=1000, freq=252,
        )[0]

        large_family = deflated_sharpe_family(
            [sr, 1.0, 1.0, 1.0, 1.0],
            [own_trials, 50, 50, 50, 50],
            sr_variance=0.5, skew=0.0, kurtosis=3.0,
            n_observations=1000, freq=252,
        )[0]

        assert large_family.n_trials_family > small_family.n_trials_family
        assert large_family.dsr_family_corrected <= small_family.dsr_family_corrected + 1e-9

    def test_strategy_ids_default_when_omitted(self):
        results = deflated_sharpe_family([1.0, 2.0], [5, 5])
        assert [r.strategy_id for r in results] == ["strategy_0", "strategy_1"]

    def test_strategy_ids_used_when_provided(self):
        results = deflated_sharpe_family(
            [1.0, 2.0], [5, 5], strategy_ids=["rsi2_mean_reversion", "multifactor"],
        )
        assert [r.strategy_id for r in results] == ["rsi2_mean_reversion", "multifactor"]

    def test_empty_inputs_return_empty_list(self):
        assert deflated_sharpe_family([], []) == []

    def test_mismatched_lengths_raises_value_error(self):
        with pytest.raises(ValueError):
            deflated_sharpe_family([1.0, 2.0], [5])

    def test_mismatched_strategy_ids_length_raises_value_error(self):
        with pytest.raises(ValueError):
            deflated_sharpe_family([1.0, 2.0], [5, 5], strategy_ids=["only_one"])

    def test_n_trials_family_is_sum_across_family(self):
        results = deflated_sharpe_family([1.0, 1.0, 1.0], [10, 20, 30])
        for r in results:
            assert r.n_trials_family == 60

    def test_result_dataclass_fields(self):
        results = deflated_sharpe_family([1.5], [10])
        r = results[0]
        assert isinstance(r, FamilyDSRResult)
        assert r.sharpe_observed == 1.5
        assert r.n_trials_own == 10
        assert r.n_trials_family == 10
        assert r.family_correction_applied is True


class TestDeflatedSharpeFamilySingleTrialNoOp:
    """Finding 9: the family-wide correction inherits
    deflated_sharpe_ratio's own n_trials<=1 legacy shortcut -- this must be
    surfaced explicitly (a warning + an honest flag), never silently
    reported as if a real correction had been applied."""

    def test_family_total_le_1_flags_correction_not_applied_by_default(self):
        # A single strategy with a single trial -> n_trials_family_total == 1.
        results = deflated_sharpe_family([1.5], [1])
        assert len(results) == 1
        r = results[0]
        assert r.n_trials_family == 1
        # Legacy no-op shortcut value, reproduced byte-for-byte.
        assert r.dsr_family_corrected == 1.0
        assert r.family_correction_applied is False

    def test_family_total_le_1_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="validation.multiple_testing"):
            deflated_sharpe_family([1.5], [1])
        assert any(
            "n_trials_family_total" in rec.message and "not a genuine" in rec.message.lower()
            for rec in caplog.records
        )

    def test_family_total_gt_1_flags_correction_applied(self):
        results = deflated_sharpe_family([1.5, 1.2], [1, 1])
        assert results[0].n_trials_family == 2
        for r in results:
            assert r.family_correction_applied is True

    def test_single_trial_correction_setting_flips_flag_true(self, monkeypatch):
        """Mirrors validation/metrics.py's own
        VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED precedent: when the
        setting is on, deflated_sharpe_ratio performs a REAL computation
        even at n_trials<=1 (sr_0=0.0 branch), so this is no longer the
        no-op case."""
        import settings as settings_module
        monkeypatch.setattr(
            settings_module.settings, "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", True,
        )
        results = deflated_sharpe_family([1.5], [1])
        r = results[0]
        assert r.n_trials_family == 1
        assert r.family_correction_applied is True
        # With the correction enabled, the single-trial branch runs a real
        # computation (sr_0=0.0) rather than the flat 1.0 shortcut.
        assert r.dsr_family_corrected != 1.0

    def test_zero_total_trials_also_flagged_not_applied(self):
        """n_trials_family_total == 0 (e.g. every strategy reports 0 trials)
        is likewise <= 1 and must not be misrepresented as corrected."""
        results = deflated_sharpe_family([1.0, 1.0], [0, 0])
        for r in results:
            assert r.n_trials_family == 0
            assert r.family_correction_applied is False
            assert r.dsr_family_corrected == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Summary formatting
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatSummary:
    def test_empty_returns_no_results_message(self):
        out = format_multiple_testing_summary([], [], [])
        assert "NO RESULTS" in out

    def test_non_empty_includes_rejection_counts(self):
        rejected = [True, False, True]
        ids = ["a", "b", "c"]
        out = format_multiple_testing_summary(rejected, ids)
        assert "2/3" in out
        assert "a" in out and "b" in out and "c" in out

    def test_family_dsr_rows_rendered(self):
        family = deflated_sharpe_family([1.5, 2.0], [10, 20], strategy_ids=["a", "b"])
        out = format_multiple_testing_summary([True, False], ["a", "b"], family)
        assert "a" in out and "b" in out
