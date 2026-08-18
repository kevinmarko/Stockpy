import numpy as np
from validation.metrics import probability_of_backtest_overfitting

def test_pbo_random_data():
    """
    With 100 random strategies on random data, PBO should be ~0.5
    due to lack of relationship between IS and OOS performance.
    """
    np.random.seed(42)
    # 45 paths, 100 strategies
    n_paths = 45
    n_strategies = 100
    
    is_sharpes = np.random.randn(n_paths, n_strategies)
    oos_sharpes = np.random.randn(n_paths, n_strategies)
    
    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    # Check that PBO is around 0.5 (within standard range 0.3 to 0.7)
    assert 0.3 <= pbo <= 0.7

def test_pbo_perfect_strategy():
    """
    With 1 strategy that perfectly predicts the test set, PBO should be exactly 0.
    """
    n_paths = 45
    n_strategies = 10

    is_sharpes = np.random.randn(n_paths, n_strategies)
    oos_sharpes = np.random.randn(n_paths, n_strategies)

    # Strategy 0 is perfect: always highest IS and OOS
    is_sharpes[:, 0] = 5.0
    oos_sharpes[:, 0] = 5.0

    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    assert pbo == 0.0


def test_pbo_all_nan_is_row_excluded_from_measurable_paths():
    """
    A path where every trial's in-sample Sharpe is NaN (e.g. a
    degenerate/constant-returns trial set for that path) has no
    "best in-sample" strategy to evaluate at all -- np.nanargmax raises,
    and the path must be excluded from the denominator entirely rather
    than fabricating a "best" index (CONSTRAINT #4). Appending such a row
    must not change the computed PBO versus the same matrix without it.
    """
    np.random.seed(7)
    is_sharpes = np.random.randn(10, 5)
    oos_sharpes = np.random.randn(10, 5)
    pbo_without_degenerate_row = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)

    degenerate_is_row = np.full((1, 5), np.nan)
    degenerate_oos_row = np.random.randn(1, 5)  # irrelevant -- the IS row is unmeasurable
    is_with_degenerate = np.vstack([is_sharpes, degenerate_is_row])
    oos_with_degenerate = np.vstack([oos_sharpes, degenerate_oos_row])

    pbo_with_degenerate = probability_of_backtest_overfitting(is_with_degenerate, oos_with_degenerate)
    assert pbo_with_degenerate == pbo_without_degenerate_row


def test_pbo_nan_oos_for_is_winner_excluded_not_miscounted_as_not_overfit():
    """
    When the in-sample-best strategy's own OOS Sharpe is individually NaN
    (a degenerate/constant test window for just that one trial, while other
    strategies on the same path have real OOS values), the path must be
    excluded from the denominator -- NOT silently counted as "not overfit"
    via `NaN < median` always evaluating False (the bug this test pins).
    """
    is_sharpes = np.array([
        [1.0, 0.5, 0.2],   # best-IS = strategy 0
        [0.1, 0.9, 0.5],   # best-IS = strategy 1
    ])
    oos_sharpes = np.array([
        [np.nan, 0.1, 0.9],   # strategy 0 (the IS winner on path 0)'s OOS is unmeasurable
        [0.2, 0.05, 0.3],     # strategy 1 (the IS winner on path 1)'s OOS = 0.05, median = 0.2
    ])

    pbo = probability_of_backtest_overfitting(is_sharpes, oos_sharpes)
    # Only path 1 is measurable; its IS-winner's OOS (0.05) is below the
    # path's median OOS (0.2), so it counts as overfit -> PBO = 1.0. If path 0
    # were wrongly counted as measurable-and-not-overfit (the pre-fix bug),
    # this would come out to 0.5 instead.
    assert pbo == 1.0
