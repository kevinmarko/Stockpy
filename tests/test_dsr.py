import numpy as np
import pytest
from validation.metrics import deflated_sharpe_ratio

def test_bailey_lopez_de_prado_worked_example():
    """
    Verify that our DSR implementation matches Bailey & Lopez de Prado's worked
    example from the SSRN paper appendix:
    - Annualized SR_observed = 2.5
    - Annualized SR_variance = 0.5
    - n_trials = 100
    - n_observations = 1250 (daily, freq=252)
    - skew = -3.0
    - kurtosis = 10.0 (non-excess)

    The expected DSR is approximately 0.90 (within 0.01 tolerance).
    """
    dsr = deflated_sharpe_ratio(
        sr_observed=2.5,
        n_trials=100,
        sr_variance=0.5,
        skew=-3.0,
        kurtosis=10.0,
        n_observations=1250,
        freq=252
    )

    # Assert that DSR is within 0.01 of 0.90 (0.89 to 0.91)
    assert abs(dsr - 0.90) <= 0.01


class TestSingleTrialCorrectionFlag:
    """settings.VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED (opt-in,
    default False -- see settings.py). Bug: deflated_sharpe_ratio's
    n_trials<=1 shortcut unconditionally ``return 1.0`` regardless of how
    weak sr_observed/skew/kurtosis actually are, so a single-trial strategy
    always passes the DSR > 0.95 deployability gate. Directly relied on
    today by 5 STRATEGY_REGISTRY strategies (multifactor_lowvol_size,
    garch_vol_target, cross_sectional_momentum, relative_strength_xsec,
    timeseries_momentum) currently recorded deployable=True -- see
    docs/VALIDATION_STRATEGY_FIX_LOG.md."""

    def test_flag_off_is_byte_identical_to_legacy_shortcut(self, monkeypatch):
        """Default (flag unset/False): n_trials<=1 must still return exactly
        1.0 regardless of sr_observed/skew/kurtosis -- proves the fix ships
        opt-in and does not silently change any currently-recorded verdict."""
        from settings import settings as live_settings
        monkeypatch.setattr(
            live_settings, "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", False, raising=False
        )
        for sr_observed, skew, kurtosis in [
            (-5.0, 0.0, 3.0),   # strongly negative -- would fail if corrected
            (2.5, 0.0, 3.0),    # excellent
            (0.0, -3.0, 10.0),  # degenerate/fat-tailed
        ]:
            dsr = deflated_sharpe_ratio(
                sr_observed=sr_observed,
                n_trials=1,
                sr_variance=0.1,
                skew=skew,
                kurtosis=kurtosis,
                n_observations=756,
                freq=252,
            )
            assert dsr == 1.0

        # n_trials=0 also hits the <=1 shortcut (defensive edge case).
        dsr_zero_trials = deflated_sharpe_ratio(
            sr_observed=-5.0, n_trials=0, sr_variance=0.1, skew=0.0,
            kurtosis=3.0, n_observations=756, freq=252,
        )
        assert dsr_zero_trials == 1.0

    def test_flag_on_strongly_negative_single_trial_fails_gate(self, monkeypatch):
        """Corrected math: a single trial with a strongly negative observed
        Sharpe must land well below the DSR > 0.95 deployability threshold,
        not the hardcoded perfect-pass 1.0."""
        from settings import settings as live_settings
        monkeypatch.setattr(
            live_settings, "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", True, raising=False
        )
        dsr = deflated_sharpe_ratio(
            sr_observed=-1.5,
            n_trials=1,
            sr_variance=0.1,
            skew=0.0,
            kurtosis=3.0,
            n_observations=756,
            freq=252,
        )
        assert dsr < 0.95
        assert dsr == pytest.approx(0.00479, abs=1e-3)

    def test_flag_on_excellent_single_trial_still_passes_gate(self, monkeypatch):
        """Corrected math is not just 'always fail single-trial strategies' --
        a genuinely excellent single-trial observed Sharpe still clears the
        DSR > 0.95 bar."""
        from settings import settings as live_settings
        monkeypatch.setattr(
            live_settings, "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", True, raising=False
        )
        dsr = deflated_sharpe_ratio(
            sr_observed=2.5,
            n_trials=1,
            sr_variance=0.1,
            skew=0.0,
            kurtosis=3.0,
            n_observations=756,
            freq=252,
        )
        assert dsr > 0.95
        assert dsr == pytest.approx(0.99999, abs=1e-3)

    def test_flag_on_still_uses_legacy_shortcut_for_multi_trial(self, monkeypatch):
        """The flag only changes n_trials<=1 behavior -- multi-trial DSR
        (n_trials > 1) must be completely unaffected."""
        from settings import settings as live_settings
        monkeypatch.setattr(
            live_settings, "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", True, raising=False
        )
        dsr = deflated_sharpe_ratio(
            sr_observed=2.5, n_trials=100, sr_variance=0.5, skew=-3.0,
            kurtosis=10.0, n_observations=1250, freq=252,
        )
        assert abs(dsr - 0.90) <= 0.01


class TestDenominatorDegenerateGuard:
    """Bug: the DSR test-statistic denominator was guarded with an exact
    ``denominator == 0`` check instead of this repo's documented
    degenerate-std ``< 1e-12`` convention. A near-zero-but-nonzero
    denominator would otherwise explode z_stat into an absurd, unbounded
    value instead of the honest NaN (CONSTRAINT #4)."""

    def test_exactly_zero_denominator_is_nan(self):
        """Baseline: bit-identical zero was already handled pre-fix."""
        import validation.metrics as metrics_module
        result = {}

        def fake_sqrt(x, _orig=np.sqrt):
            if isinstance(x, float) and x == 1.0:
                result["denominator_input_seen"] = True
                return 0.0
            return _orig(x)

        import unittest.mock as mock
        with mock.patch.object(metrics_module.np, "sqrt", side_effect=fake_sqrt):
            dsr = deflated_sharpe_ratio(
                sr_observed=1.0, n_trials=10, sr_variance=0.5, skew=0.0,
                kurtosis=1.0, n_observations=252, freq=252,
            )
        assert result.get("denominator_input_seen")
        assert np.isnan(dsr)

    def test_near_zero_nonzero_denominator_is_nan_not_an_exploded_z_stat(self):
        """Forces the denominator's own np.sqrt() call (distinguished from
        the sr_0 computation's np.sqrt(var_sr) call by its known input value
        of 1.0, from skew=0.0/kurtosis=1.0 below) to return a near-zero
        magnitude the same order as the reported real-world std() noise
        incident (~1e-16) -- below the 1e-12 floor, but not exactly 0.0.
        Same monkeypatch-a-controlled-internal-value pattern as
        tests/test_harness_calmar_degenerate_guard.py uses for
        compute_max_drawdown."""
        import validation.metrics as metrics_module

        def fake_sqrt(x, _orig=np.sqrt):
            if isinstance(x, float) and x == 1.0:
                return 9.9e-17
            return _orig(x)

        import unittest.mock as mock
        with mock.patch.object(metrics_module.np, "sqrt", side_effect=fake_sqrt):
            dsr = deflated_sharpe_ratio(
                sr_observed=1.0, n_trials=10, sr_variance=0.5, skew=0.0,
                kurtosis=1.0, n_observations=252, freq=252,
            )
        assert np.isnan(dsr), f"expected NaN, got an absurd value: {dsr}"

    def test_genuinely_small_but_real_denominator_is_not_treated_as_degenerate(self):
        """A real (not artificially forced) small-but-nonzero denominator
        must still compute a finite DSR -- the guard must not misfire on
        legitimate inputs."""
        dsr = deflated_sharpe_ratio(
            sr_observed=0.05, n_trials=5, sr_variance=0.01, skew=0.1,
            kurtosis=3.0, n_observations=500, freq=252,
        )
        assert np.isfinite(dsr)
