import glob
import numpy as np
import pandas as pd
from execution.cost_model import TieredCostModel
import validation.harness as harness_module
from validation.harness import StrategyValidationHarness


def test_validation_harness_renders_cpcv_report(tmp_path, monkeypatch):
    """
    StrategyValidationHarness.run() must render BOTH the general validation
    report AND the dedicated CPCV/overfitting-audit report
    (reports/cpcv_report.html.j2) — the per-path Sharpe table and
    distribution histogram that validation_report_template.html.j2 does not
    surface even though it already receives report.paths/.distribution.
    """
    # run() reads the module-level get_universe_with_survivorship_warning
    # binding directly, not the constructor's universe_fn kwarg below (which
    # StrategyValidationHarness.run() never calls) -- see
    # tests/test_harness_oos_gate.py's identical fixture for the established
    # pattern this mirrors. Keeps this test fully offline, no live Wikipedia/
    # FMP network call.
    monkeypatch.setattr(
        harness_module, "get_universe_with_survivorship_warning",
        lambda _d: (["MOCK"], {"n_current": 1, "n_at_date": 1,
                                "n_delisted_in_period": 0, "estimated_bias_pct": 0.5}),
    )

    np.random.seed(7)
    dates = pd.date_range("2020-01-01", periods=200)
    X = pd.DataFrame(np.random.randn(200, 2), index=dates)
    y = pd.Series(np.random.randn(200) * 0.01, index=dates)

    def random_strategy_fn(X_train, y_train, X_test, y_test):
        return [
            {
                "params": f"config_{i}",
                "train_returns": pd.Series(np.random.normal(0, 0.01, len(y_train)), index=y_train.index),
                "test_returns": pd.Series(np.random.normal(0, 0.01, len(y_test)), index=y_test.index),
            }
            for i in range(3)
        ]

    def mock_universe_fn(as_of_date):
        return ["MOCK"]

    harness = StrategyValidationHarness(
        strategy_fn=random_strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=TieredCostModel(),
        n_cpcv_splits=5,
        n_test_splits=1,
        reports_dir=str(tmp_path),
    )

    report = harness.run(
        start_date="2020-01-01",
        end_date="2020-10-01",
        X=X,
        y=y,
        strategy_name="CPCV_Report_Render_Test",
    )

    # The harness's own CPCV evaluation must have produced at least one
    # combinatorial path, and mean_oos_sharpe must be threaded onto the report.
    assert len(report.paths) > 0
    assert isinstance(report.mean_oos_sharpe, float)

    cpcv_files = glob.glob(str(tmp_path / "cpcv_cpcv_report_render_test_*.html"))
    assert len(cpcv_files) == 1, f"expected exactly one rendered CPCV report, found {cpcv_files}"

    html = open(cpcv_files[0], encoding="utf-8").read()
    assert "Combinatorial Purged CV Report" in html
    assert f"{'%.2f' % (report.dsr * 100)}%" in html
    assert f"{'%.2f' % (report.pbo * 100)}%" in html
    # Every path's ID must appear in the rendered per-path table.
    for path in report.paths:
        assert str(path["path_id"]) in html

    # The general validation report must still be rendered unchanged.
    validation_files = glob.glob(str(tmp_path / "validation_cpcv_report_render_test_*.html"))
    assert len(validation_files) == 1
