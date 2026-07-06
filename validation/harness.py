"""
InvestYo Quant Platform - Strategy Validation Harness
======================================================
Acts as a master gatekeeper for quantitative trading strategies.
Performs Combinatorial Purged CV (CPCV), walk-forward stability checks,
computes DSR/PBO, and enforces strict deployability gates.
"""

import os
import argparse
import logging
from datetime import datetime, date
from typing import Callable, List, Dict, Any, Tuple, Optional
import numpy as np
import pandas as pd
import jinja2
import yfinance as yf

from universe_engine import get_universe_with_survivorship_warning
from execution.cost_model import TieredCostModel
from validation.metrics import run_cpcv_evaluation, sharpe_ratio, deflated_sharpe_ratio, probability_of_backtest_overfitting
from validation.stress_scenarios import (
    StressResult,
    run_stress_tests,
    passes_stress_gate,
    format_stress_summary,
)
from validation.thresholds import (
    PBO_MAX,
    DSR_MIN,
    NET_SHARPE_MIN,
    MAX_DRAWDOWN_MAX,
    FAMILY_WISE_ALPHA,
)
from validation.multiple_testing import (
    benjamini_hochberg,
    deflated_sharpe_family,
    format_multiple_testing_summary,
)

# Per-strategy cap on reports/history/<strategy>_validation_history.jsonl —
# bounds on-disk growth from repeated dev/CI harness runs while still keeping
# a useful multi-run trend window.
MAX_VALIDATION_HISTORY_ROWS = 1000

# Configure module logger
logger = logging.getLogger("Validation_Harness")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

class ValidationReport:
    """Standardized validation report output by the harness."""
    def __init__(
        self,
        name: str,
        start_date: str,
        end_date: str,
        sharpe: float,
        sortino: float,
        calmar: float,
        max_dd: float,
        turnover: float,
        hit_rate: float,
        avg_trade_pct: float,
        dsr: float,
        pbo: float,
        bias_report: Dict[str, Any],
        walk_forward_60_40: float,
        walk_forward_70_30: float,
        walk_forward_80_20: float,
        distribution: np.ndarray,
        paths: List[Dict[str, Any]],
        n_trials: int,
        is_options_selling: bool = False,
        stress_test_results: Optional[Dict[str, "StressResult"]] = None,
        family_multiple_testing: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.start_date = start_date
        self.end_date = end_date
        self.sharpe = sharpe
        self.sortino = sortino
        self.calmar = calmar
        self.max_dd = max_dd
        self.turnover = turnover
        self.hit_rate = hit_rate
        self.avg_trade_pct = avg_trade_pct
        self.dsr = dsr
        self.pbo = pbo
        self.bias_report = bias_report
        self.walk_forward_60_40 = walk_forward_60_40
        self.walk_forward_70_30 = walk_forward_70_30
        self.walk_forward_80_20 = walk_forward_80_20
        self.distribution = distribution
        self.paths = paths
        self.n_trials = n_trials
        # Tail-scenario stress testing (validation/stress_scenarios.py). Only
        # populated/enforced for options-selling strategies, whose negatively
        # skewed payoff is not protected by the full-sample MaxDD gate below.
        self.is_options_selling = is_options_selling
        self.stress_test_results = stress_test_results
        # Family-wise multiple-testing correction (validation/multiple_testing.py),
        # computed opportunistically ACROSS every strategy's persisted
        # *_validation_summary.json on disk — see
        # compute_family_multiple_testing_report(). None until that function has
        # been called at least once for this report (e.g. by run()'s final step).
        self.family_multiple_testing = family_multiple_testing

    @property
    def stress_gate_passed(self) -> bool:
        """Whether the tail-scenario stress gate passed. Always True for
        non-options-selling strategies (the gate does not apply). For
        options-selling strategies, delegates to passes_stress_gate(), which
        fails closed when results are missing."""
        if not self.is_options_selling:
            return True
        return passes_stress_gate(self.stress_test_results)

    @property
    def deployable(self) -> bool:
        """
        True iff all conservative validation criteria are satisfied:
        1. PBO < 50%
        2. DSR > 95%
        3. Net-of-cost Sharpe > 0.5
        4. Max Drawdown < 30%
        5. (options-selling only) Tail-scenario stress gate: max drawdown
           < 50% AND account survives in EVERY dated shock window
           (validation/stress_scenarios.py). Fails closed if an
           options-selling strategy was never stress-tested.

        Thresholds are imported from :mod:`validation.thresholds` so the GUI
        and harness always share the same values.
        """
        pbo_pass = self.pbo < PBO_MAX
        dsr_pass = self.dsr > DSR_MIN
        sharpe_pass = (not np.isnan(self.sharpe)) and (self.sharpe > NET_SHARPE_MIN)
        max_dd_pass = (not np.isnan(self.max_dd)) and (self.max_dd < MAX_DRAWDOWN_MAX)
        return bool(pbo_pass and dsr_pass and sharpe_pass and max_dd_pass and self.stress_gate_passed)

    def to_summary_dict(self) -> dict:
        """Return a JSON-serialisable summary suitable for the preflight check and dashboard."""
        from datetime import datetime, timezone
        import dataclasses

        # family_multiple_testing["family_dsr"] holds FamilyDSRResult
        # dataclass instances (validation/multiple_testing.py), which
        # json.dumps cannot serialize on its own — convert to plain dicts so
        # this summary round-trips through JSON (used by both the
        # overwritten *_validation_summary.json snapshot and the appended
        # validation-history JSONL rows).
        family_mt = self.family_multiple_testing
        if family_mt is not None:
            family_mt = dict(family_mt)
            family_dsr = family_mt.get("family_dsr")
            if family_dsr is not None:
                family_mt["family_dsr"] = [
                    dataclasses.asdict(r) if dataclasses.is_dataclass(r) else r
                    for r in family_dsr
                ]

        return {
            "strategy_id": self.name,
            "deployable": self.deployable,
            "pbo": float(self.pbo),
            "dsr": float(self.dsr),
            "sharpe": float(self.sharpe) if not np.isnan(self.sharpe) else None,
            "max_drawdown": float(self.max_dd) if not np.isnan(self.max_dd) else None,
            "is_options_selling": self.is_options_selling,
            "stress_gate_passed": self.stress_gate_passed,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "report_date": datetime.now(timezone.utc).date().isoformat(),
            # n_trials is persisted so validation.multiple_testing's family-wise
            # DSR correction can be computed opportunistically across every
            # strategy's *_validation_summary.json on disk, without needing to
            # re-run any backtest (see compute_family_multiple_testing_report).
            "n_trials": int(self.n_trials),
            "family_multiple_testing": family_mt,
        }


def compute_family_multiple_testing_report(
    reports_dir: str = "reports",
    *,
    alpha: float = FAMILY_WISE_ALPHA,
    p_from_dsr: bool = True,
) -> Dict[str, Any]:
    """Opportunistically compute the family-wise multiple-testing correction
    across EVERY strategy validation summary currently on disk, without
    requiring the caller to know in advance which strategies belong to a
    "family" — every ``*_validation_summary.json`` in *reports_dir*
    (written by ``StrategyValidationHarness._write_json_summary`` via
    ``ValidationReport.to_summary_dict()``) is treated as one member of the
    signal family (see ``signals/registry.py`` for the ~17 registered
    modules this typically corresponds to).

    Two independent corrections are surfaced:
      1. Benjamini-Hochberg FDR control over p-values derived from each
         strategy's DSR (``p = 1 - dsr``, since DSR is itself already a
         one-sided probability that the true Sharpe is > 0 — see
         ``p_from_dsr``).
      2. Family-wise Deflated Sharpe Ratio, substituting the TOTAL trial
         count summed across every strategy for each one's own trial count.

    Dead-letter resilient (CONSTRAINT #6): a missing/malformed
    ``reports_dir``, an unreadable JSON file, or a strategy summary lacking
    the fields needed for one of the two corrections is logged and skipped
    (or contributes NaN for that one strategy) rather than aborting the
    whole aggregate report.

    Parameters
    ----------
    reports_dir:
        Directory to scan for ``*_validation_summary.json`` files.
    alpha:
        Target false discovery rate for Benjamini-Hochberg (default
        ``validation.thresholds.FAMILY_WISE_ALPHA``).
    p_from_dsr:
        When True (default), derives each strategy's p-value as
        ``1 - dsr`` — a strategy's DSR already IS the (one minus) p-value of
        the null "true Sharpe <= 0" hypothesis under Bailey & Lopez de
        Prado's framework, so this reuses it rather than inventing a new
        statistic. When a strategy's DSR is missing/NaN, its p-value is
        NaN and it will never be BH-rejected (see
        ``multiple_testing.benjamini_hochberg``'s NaN handling).

    Returns
    -------
    Dict[str, Any]
        ``{"strategy_ids": [...], "bh_rejected": [...], "family_dsr":
        [FamilyDSRResult, ...], "n_strategies": int, "summary_text": str}``.
        ``{"strategy_ids": [], "bh_rejected": [], "family_dsr": [],
        "n_strategies": 0, "summary_text": "..."}`` if no summaries are
        found or the directory can't be read (never raises).
    """
    import json
    from pathlib import Path

    empty_result: Dict[str, Any] = {
        "strategy_ids": [],
        "bh_rejected": [],
        "family_dsr": [],
        "n_strategies": 0,
        "summary_text": format_multiple_testing_summary([], [], []),
    }

    try:
        dir_path = Path(reports_dir)
        if not dir_path.is_dir():
            logger.info(
                "compute_family_multiple_testing_report: reports_dir %r does not "
                "exist; nothing to aggregate.", reports_dir,
            )
            return empty_result

        summary_files = sorted(dir_path.glob("*_validation_summary.json"))
    except Exception as exc:
        logger.warning(
            "compute_family_multiple_testing_report: failed to scan %r: %s",
            reports_dir, exc,
        )
        return empty_result

    strategy_ids: List[str] = []
    sharpes: List[float] = []
    n_trials_list: List[int] = []
    dsr_list: List[float] = []

    for f in summary_files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            strategy_ids.append(str(payload.get("strategy_id", f.stem)))
            sharpes.append(float(payload.get("sharpe")) if payload.get("sharpe") is not None else float("nan"))
            n_trials_list.append(int(payload.get("n_trials", 1)))
            dsr_list.append(float(payload.get("dsr", float("nan"))))
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the sweep
            logger.warning(
                "compute_family_multiple_testing_report: skipping unreadable "
                "summary %s: %s", f, exc,
            )
            continue

    if not strategy_ids:
        return empty_result

    try:
        pvalues = [
            (1.0 - dsr) if not np.isnan(dsr) else float("nan")
            for dsr in dsr_list
        ] if p_from_dsr else [float("nan")] * len(strategy_ids)
        bh_rejected = benjamini_hochberg(pvalues, alpha=alpha)
    except Exception as exc:
        logger.warning(
            "compute_family_multiple_testing_report: benjamini_hochberg failed: %s", exc,
        )
        bh_rejected = [False] * len(strategy_ids)

    try:
        family_dsr = deflated_sharpe_family(
            sharpes, n_trials_list, strategy_ids=strategy_ids,
        )
    except Exception as exc:
        logger.warning(
            "compute_family_multiple_testing_report: deflated_sharpe_family failed: %s", exc,
        )
        family_dsr = []

    summary_text = format_multiple_testing_summary(bh_rejected, strategy_ids, family_dsr)

    return {
        "strategy_ids": strategy_ids,
        "bh_rejected": bh_rejected,
        "family_dsr": family_dsr,
        "n_strategies": len(strategy_ids),
        "summary_text": summary_text,
    }


def read_validation_history(
    strategy_id: str,
    history_dir: str = "reports/history",
) -> List[Dict[str, Any]]:
    """Read the persisted run-over-run validation history for one strategy.

    Unlike ``reports/<strategy>_validation_summary.json`` (overwritten every
    harness run — the CURRENT snapshot only), ``StrategyValidationHarness``
    also appends one row per run to
    ``reports/history/<strategy>_validation_history.jsonl`` (see
    ``_append_validation_history``), so PBO/DSR/Sharpe/MaxDD can be plotted
    as a time series across runs.

    Dead-letter resilient (CONSTRAINT #6): a missing directory/file or a
    corrupt line is skipped rather than raising — returns ``[]`` (never
    fabricates rows, CONSTRAINT #4) when no history exists yet.

    Parameters
    ----------
    strategy_id:
        The strategy's ``name`` (as passed to ``StrategyValidationHarness.run``).
    history_dir:
        Directory containing the per-strategy ``*_validation_history.jsonl``
        files. Defaults to ``reports/history``.

    Returns
    -------
    list[dict]
        Chronological (oldest-first) list of ``ValidationReport.to_summary_dict()``
        snapshots, one per past run. Empty list if none exist or the file
        can't be read.
    """
    import json
    from pathlib import Path

    safe_name = strategy_id.replace(" ", "_").replace("/", "_")
    target = Path(history_dir) / f"{safe_name}_validation_history.jsonl"
    if not target.exists():
        return []

    rows: List[Dict[str, Any]] = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:  # noqa: BLE001 - one bad line must not abort the read
                logger.debug("read_validation_history(%s): skipping corrupt line: %s", strategy_id, exc)
                continue
    except Exception as exc:
        logger.warning("read_validation_history(%s): failed to read %s: %s", strategy_id, target, exc)
        return []
    return rows


class StrategyValidationHarness:
    """
    Validation harness that runs Walk-Forward and CPCV tests on strategies.
    Gates strategy deployment using DSR, PBO, Sharpe, and Drawdown.
    """
    def __init__(
        self,
        strategy_fn: Callable[[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series], List[Dict[str, Any]]],
        universe_fn: Callable[[date], List[str]],
        cost_model: TieredCostModel,
        n_cpcv_splits: int = 10,
        n_test_splits: int = 2,
        is_options_selling: bool = False,
        stress_returns_fn: Optional[Callable[[str, str], pd.Series]] = None,
        reports_dir: str = "reports",
    ):
        """
        Args:
            strategy_fn: Callable returning [ {"params": str/dict, "train_returns": pd.Series, "test_returns": pd.Series} ]
            universe_fn: Callable taking date and returning list of constituents.
            cost_model: TieredCostModel instance.
            is_options_selling: When True, the report enforces the tail-scenario
                stress gate (validation/stress_scenarios.py) in addition to the
                standard PBO/DSR/Sharpe/MaxDD gates.
            stress_returns_fn: Callable (start, end) -> daily strategy returns
                Series, used to replay the strategy across each dated shock
                window. REQUIRED for options-selling strategies — if omitted,
                the stress gate fails closed (strategy is not deployable).
            reports_dir: Directory for JSON summaries, run history, and
                rendered HTML reports (default "reports" — the real repo
                directory consumed by scripts/preflight_check.py and
                observability/dashboard.py). Tests and audit sandboxes MUST
                override this to an isolated tmp directory so smoke-test/
                negative-control runs never clobber real strategy reports.
        """
        self.strategy_fn = strategy_fn
        self.universe_fn = universe_fn
        self.cost_model = cost_model
        self.n_cpcv_splits = n_cpcv_splits
        self.n_test_splits = n_test_splits
        self.is_options_selling = is_options_selling
        self.stress_returns_fn = stress_returns_fn
        self.reports_dir = reports_dir

    def run(
        self,
        start_date: str,
        end_date: str,
        X: Optional[pd.DataFrame] = None,
        y: Optional[pd.Series] = None,
        strategy_name: str = "Strategy"
    ) -> ValidationReport:
        """
        Runs the full validation suite. If X/y are not provided, downloads data.
        """
        logger.info(f"Starting validation harness for {strategy_name}...")
        
        # 1. Load universe with survivorship report
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        _, bias_report = get_universe_with_survivorship_warning(start_dt)

        # 2. Get Data
        if X is None or y is None:
            logger.info("No input data provided. Fetching SPY benchmark data for validation...")
            # Default to SPY for CLI/benchmark validation
            df = yf.download("SPY", start=start_date, end=end_date, progress=False)
            if df.empty:
                raise RuntimeError("Failed to download validation data.")
            # Standardize index
            df.index = pd.to_datetime(df.index)
            
            # Squeeze columns to handle potential DataFrame structure from yfinance download
            close_col = df["Close"].squeeze()
            vol_col = df["Volume"].squeeze()
            
            # Create features
            X_df = pd.DataFrame(index=df.index)
            X_df["close_lag1"] = close_col.shift(1)
            X_df["vol_lag1"] = vol_col.shift(1)
            X_df = X_df.dropna()
            
            y_series = close_col.pct_change().loc[X_df.index]
            X = X_df
            y = y_series
            
        n_samples = len(X)
        
        # 3. Walk-Forward Stability Checks (60/40, 70/30, 80/20)
        wf_sharpes = {}
        for split_pct in [0.60, 0.70, 0.80]:
            split_idx = int(n_samples * split_pct)
            X_train, y_train = X.iloc[:split_idx], y.iloc[:split_idx]
            X_test, y_test = X.iloc[split_idx:], y.iloc[split_idx:]
            
            trials = self.strategy_fn(X_train, y_train, X_test, y_test)
            if trials:
                # Find best in-sample configuration
                is_sharpes = [sharpe_ratio(t["train_returns"]) for t in trials]
                # nanargmax raises ValueError on an all-NaN slice (e.g. constant
                # returns have zero std → NaN Sharpe); guard with any-valid check.
                has_valid = any(not np.isnan(s) for s in is_sharpes)
                best_idx = int(np.nanargmax(is_sharpes)) if has_valid else 0
                best_trial = trials[best_idx]

                # Apply transaction cost model to test returns
                turnover = best_trial.get("turnover", 0.05)
                net_test_returns = self._apply_cost_model(best_trial["test_returns"], turnover=turnover)
                wf_sr = sharpe_ratio(net_test_returns)
                wf_sharpes[split_pct] = wf_sr if not np.isnan(wf_sr) else 0.0
            else:
                wf_sharpes[split_pct] = 0.0

        # 4. CPCV across full sample
        cpcv_results = run_cpcv_evaluation(
            self.strategy_fn,
            X,
            y,
            t1=None,
            n_splits=self.n_cpcv_splits,
            n_test_splits=self.n_test_splits
        )
        
        # 5. Performance Metrics (over the full sample)
        # Evaluate strategy over full sample
        full_trials = self.strategy_fn(X, y, X, y)
        if full_trials:
            is_sharpes = [sharpe_ratio(t["train_returns"]) for t in full_trials]
            has_valid = any(not np.isnan(s) for s in is_sharpes)
            best_idx = int(np.nanargmax(is_sharpes)) if has_valid else 0
            best_trial = full_trials[best_idx]
            # Net returns over full sample
            turnover = best_trial.get("turnover", 0.05)
            full_returns = self._apply_cost_model(best_trial["test_returns"], turnover=turnover)
            n_trials = len(full_trials)
        else:
            full_returns = pd.Series(0.0, index=X.index)
            turnover = 0.0
            n_trials = 1

        # Standard Performance calculations
        sharpe = sharpe_ratio(full_returns)
        
        # Sortino
        downside_returns = full_returns[full_returns < 0]
        downside_std = downside_returns.std()
        sortino = (full_returns.mean() / downside_std * np.sqrt(252)) if downside_std > 0 else np.nan
        
        # Max Drawdown
        cum_returns = (1.0 + full_returns).cumprod()
        running_max = cum_returns.cummax()
        drawdowns = (cum_returns - running_max) / running_max
        max_dd = abs(drawdowns.min()) if not drawdowns.empty else 0.0
        
        # Calmar
        calmar = (full_returns.mean() * 252 / max_dd) if max_dd > 0 else np.nan
        
        # Turnover & Trade metrics
        trade_days = full_returns != 0
        hit_rate = float((full_returns[trade_days] > 0).mean()) if trade_days.any() else 0.0
        avg_trade_pct = float(full_returns[trade_days].mean()) if trade_days.any() else 0.0

        # 5b. Tail-scenario stress testing for options-selling strategies.
        # Replays the strategy across each dated shock window (Lehman, Volmageddon,
        # COVID, yen-unwind). Required for options-selling deployability; the gate
        # fails closed if is_options_selling but no stress_returns_fn was supplied.
        stress_test_results: Optional[Dict[str, StressResult]] = None
        if self.is_options_selling:
            if self.stress_returns_fn is not None:
                logger.info("Options-selling strategy: running tail-scenario stress tests...")
                stress_test_results = run_stress_tests(self.stress_returns_fn)
            else:
                logger.warning(
                    "Options-selling strategy '%s' has no stress_returns_fn; "
                    "stress gate will FAIL CLOSED (not deployable).", strategy_name
                )

        report = ValidationReport(
            name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            sharpe=sharpe,
            sortino=sortino,
            calmar=calmar,
            max_dd=max_dd,
            turnover=turnover,
            hit_rate=hit_rate,
            avg_trade_pct=avg_trade_pct,
            dsr=cpcv_results["dsr"],
            pbo=cpcv_results["pbo"],
            bias_report=bias_report,
            walk_forward_60_40=wf_sharpes.get(0.60, 0.0),
            walk_forward_70_30=wf_sharpes.get(0.70, 0.0),
            walk_forward_80_20=wf_sharpes.get(0.80, 0.0),
            distribution=cpcv_results["distribution"],
            paths=cpcv_results["paths"],
            n_trials=n_trials,
            is_options_selling=self.is_options_selling,
            stress_test_results=stress_test_results,
        )

        # Print the stress summary at the TOP of every options-selling report so
        # the tail risk is the first thing surfaced (task 3.3 requirement).
        if self.is_options_selling:
            print(format_stress_summary(report.stress_test_results))

        # 6. Write machine-readable JSON summary for preflight_check and dashboard
        # FIRST — the family-wise multiple-testing sweep below scans reports/
        # for every *_validation_summary.json on disk, so this strategy's own
        # summary must already be present for it to participate.
        self._write_json_summary(report)

        # 6b. Opportunistic family-wise multiple-testing correction (Benjamini-
        # Hochberg + family-corrected DSR) across every strategy validation
        # summary currently on disk — see validation/multiple_testing.py and
        # compute_family_multiple_testing_report()'s docstring for rationale.
        # Dead-letter resilient: any failure here must never abort an
        # otherwise-successful validation run.
        try:
            report.family_multiple_testing = compute_family_multiple_testing_report(
                reports_dir=self.reports_dir
            )
            # Re-write the JSON summary now that family_multiple_testing is
            # populated, so downstream consumers (preflight, dashboard) see it
            # without needing a second harness run.
            self._write_json_summary(report)
        except Exception as exc:
            logger.warning(
                "StrategyValidationHarness.run(%s): family multiple-testing "
                "correction failed (non-fatal): %s", strategy_name, exc,
            )

        # 6c. Append this run's snapshot to the per-strategy history file
        # (reports/history/<strategy>_validation_history.jsonl) so PBO/DSR/
        # Sharpe/MaxDD can be plotted as a time series across runs — unlike
        # the *_validation_summary.json above, this file is append-only
        # (capped), not overwritten. One call per run() regardless of
        # whether the family multiple-testing sweep above succeeded, so the
        # history reflects the report's final state either way.
        self._append_validation_history(report)

        # 7. Render HTML report (after family_multiple_testing is populated so
        # the template can surface it if desired).
        self._render_html_report(report)

        return report

    def _write_json_summary(self, report: "ValidationReport") -> None:
        """Write a compact JSON summary to reports/<strategy_id>_validation_summary.json.

        This is the CURRENT-snapshot file, overwritten on every run — see
        ``_append_validation_history`` for the accumulated multi-run trend.
        """
        import json
        from pathlib import Path
        try:
            reports_dir = Path(self.reports_dir)
            reports_dir.mkdir(parents=True, exist_ok=True)
            safe_name = report.name.replace(" ", "_").replace("/", "_")
            dest = reports_dir / f"{safe_name}_validation_summary.json"
            dest.write_text(
                json.dumps(report.to_summary_dict(), indent=2), encoding="utf-8"
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to write validation JSON summary: %s", exc
            )

    def _append_validation_history(self, report: "ValidationReport") -> None:
        """Append this run's summary as one row to
        reports/history/<strategy_id>_validation_history.jsonl.

        Unlike ``_write_json_summary``'s per-strategy snapshot (overwritten
        every run), this file accumulates one JSON line per historical run
        so PBO/DSR/Sharpe/MaxDD can be read back as a time series across
        runs (see ``read_validation_history``). Capped to the most recent
        ``MAX_VALIDATION_HISTORY_ROWS`` entries per strategy to bound on-disk
        growth. Dead-letter resilient (CONSTRAINT #6): a failure here must
        never abort an otherwise-successful validation run.
        """
        import json
        from pathlib import Path
        try:
            history_dir = Path(self.reports_dir) / "history"
            history_dir.mkdir(parents=True, exist_ok=True)
            safe_name = report.name.replace(" ", "_").replace("/", "_")
            dest = history_dir / f"{safe_name}_validation_history.jsonl"

            rows: List[Dict[str, Any]] = []
            if dest.exists():
                for line in dest.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue  # skip a corrupt line rather than aborting

            rows.append(report.to_summary_dict())
            rows = rows[-MAX_VALIDATION_HISTORY_ROWS:]

            dest.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(
                "Failed to append validation history row for %s: %s", report.name, exc
            )

    def _apply_cost_model(self, returns: pd.Series, turnover: float = 0.05) -> pd.Series:
        """Applies execution costs based on turnover to daily returns."""
        # Cost rate for large cap: ~11 bps round-trip
        # Daily cost = turnover * cost_rate
        cost_rate = 11.0 / 10000.0
        daily_cost = turnover * cost_rate
        net_returns = returns - daily_cost
        return net_returns

    def _render_html_report(self, report: ValidationReport) -> None:
        """Renders validation report via Jinja2.

        The Jinja2 template itself is a checked-in source asset that always
        lives in the real repo ``reports/`` directory regardless of
        ``self.reports_dir`` — only the rendered OUTPUT HTML is written to
        the (possibly isolated) ``self.reports_dir``.
        """
        template_dir = "reports"
        template_file = "validation_report_template.html.j2"
        
        # Load environment
        loader = jinja2.FileSystemLoader(searchpath=template_dir)
        env = jinja2.Environment(loader=loader)
        template = env.get_template(template_file)
        
        # Render HTML
        html_out = template.render(
            name=report.name,
            start_date=report.start_date,
            end_date=report.end_date,
            deployable=report.deployable,
            dsr=report.dsr,
            pbo=report.pbo,
            sharpe=report.sharpe,
            max_dd=report.max_dd,
            sortino=report.sortino,
            calmar=report.calmar,
            hit_rate=report.hit_rate,
            turnover=report.turnover,
            avg_trade_pct=report.avg_trade_pct,
            walk_forward_60_40=report.walk_forward_60_40,
            walk_forward_70_30=report.walk_forward_70_30,
            walk_forward_80_20=report.walk_forward_80_20,
            bias_report=report.bias_report,
            paths=report.paths,
            distribution=report.distribution.tolist(),
            n_trials=report.n_trials,
            is_options_selling=report.is_options_selling,
            stress_gate_passed=report.stress_gate_passed,
            stress_summary=format_stress_summary(report.stress_test_results) if report.is_options_selling else "",
            stress_results=[
                {
                    "scenario": r.scenario,
                    "start": r.start,
                    "end": r.end,
                    "max_drawdown": r.max_drawdown,
                    "final_return": r.final_return,
                    "survived": r.survived,
                    "passed": r.passed,
                    "expected_max_dd": r.expected_max_dd_for_short_vol,
                    "error": r.error,
                }
                for r in (report.stress_test_results or {}).values()
            ],
        )
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"{self.reports_dir}/validation_{report.name.lower()}_{timestamp}.html"

        os.makedirs(self.reports_dir, exist_ok=True)
        with open(report_filename, "w") as f:
            f.write(html_out)
        logger.info(f"Validation HTML report successfully written to {report_filename}")

def main() -> None:
    """CLI endpoint for strategy validation harness."""
    parser = argparse.ArgumentParser(description="InvestYo Strategy Validation Harness")
    parser.add_argument("--strategy", type=str, required=True, help="Name of the strategy to validate")
    parser.add_argument("--start", type=str, default="2020-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2023-12-31", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    # Define a default mock strategy (Buy-and-Hold SPY) for CLI invocation
    def default_spy_bh_strategy(X_train, y_train, X_test, y_test):
        # Return 1 configurations: Buy & Hold
        return [
            {
                "params": "SPY_Buy_and_Hold",
                "train_returns": y_train,
                "test_returns": y_test
            }
        ]

    cost_model = TieredCostModel()
    
    # We pass the default Constituents provider from universe_engine
    from universe_engine import get_sp500_constituents
    
    harness = StrategyValidationHarness(
        strategy_fn=default_spy_bh_strategy,
        universe_fn=get_sp500_constituents,
        cost_model=cost_model
    )
    
    report = harness.run(
        start_date=args.start,
        end_date=args.end,
        strategy_name=args.strategy
    )
    
    print("\n" + "=" * 60)
    print(f" STRATEGY VALIDATION COMPLETE: {args.strategy}")
    print("=" * 60)
    print(f" Deployability Status:  {'PASS (DEPLOYABLE)' if report.deployable else 'FAIL (REJECTED)'}")
    print(f" Net Sharpe Ratio:      {report.sharpe:.4f}")
    print(f" Max Drawdown:          {report.max_dd*100:.2f}%")
    print(f" Deflated Sharpe (DSR): {report.dsr*100:.2f}%")
    print(f" Overfitting Prob (PBO):{report.pbo*100:.2f}%")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
