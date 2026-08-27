# Gap 1: Fabricated-OHLCV backtest deployability bug fix

## Problem Description
Autonomous backtests falling back to synthetic OHLCV data were incorrectly allowed to report `is_deployable: true`. A synthetic data run can never certify real-market deployability. This exposes a risk of deploying paper-broker code based on hallucinated data.

## Proposed Changes

### `validation/autonomous_backtest_runner.py`
- Add `data_source: str = "unknown"` and `is_synthetic_data: bool = False` to `AutonomousBacktestResult`.
- Update `to_dict()` to include these fields.
- Update `run()` signature to accept `data_source: str = "unknown"`.
- Force `is_deployable = False` with a `failure_reason` if `data_source != "real_historical_bars"`.

### `api/pilots_api.py`
- Pass `data_source="real_historical_bars"` when using real fetched data.
- Pass `data_source="synthetic_demo_data"` when falling back to generated data.

### `webapp/src/api/types.ts`
- Add `data_source: string;` and `is_synthetic_data: boolean;` to `AutonomousBacktestResponse`.

### `webapp/src/api/mock.ts`
- Update literal mocks for `AutonomousBacktestResponse` to include the new fields.

### `webapp/src/components/ai/ResearchCopilotView.tsx`
- Conditionally render a warning banner if `is_synthetic_data` is true.
- Hide the "Deploy to Paper Broker" button if `is_synthetic_data` is true.

### `webapp/src/components/ai/ResearchCopilotView.test.tsx`
- Update mocked response types.
- Add tests to assert that the banner renders and the button does not appear for synthetic data.

### Python Tests
- Update `tests/test_pilots_paper_broker.py::TestPilotsAIResearchBacktest` to assert `data_source` and deployability overrides.
- Update `tests/test_autonomous_backtest_runner.py` to cover `is_synthetic` logic.

### Docs
- Create `docs/known_issues/autonomous_backtest_synthetic_data_undisclosed.md`.
- Update `docs/architecture/ml-and-reports.md`.
