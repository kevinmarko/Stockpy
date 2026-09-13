"""Tests for pilots/retrospective_insights.py -- batch/pattern insights
(calibration reuse + manual/signal-driven/unknown cohort breakdown) for the
Retrospective Learning Loop (Trade Journal).

Uses ``tmp_path``-backed real file SQLite URLs for store construction
(matching ``tests/test_trade_decision_snapshot_store.py``'s existing
convention) precisely because cross-store consistency across
``PaperAccountStore`` and ``TradeDecisionSnapshotStore`` (same physical
file) matters here -- ``batch_insights()`` itself takes no arguments and
constructs both stores via their own default ``resolve_database_url()``
resolution, so each test monkeypatches BOTH modules' resolvers to the same
tmp_path file, overriding the session-wide autouse isolation fixtures
(``_isolate_paper_and_transactions_db_in_tests`` /
``_isolate_trade_decision_snapshot_db_in_tests`` in conftest.py) which
otherwise point them at two DIFFERENT default locations (a per-test
temp-file db vs. a private in-memory db) that would not see each other's
writes.
"""

from __future__ import annotations

from unittest.mock import patch

import data.paper_account_store as paper_account_store_module
import data.trade_decision_snapshot_store as trade_decision_snapshot_store_module
import pilots.retrospective_insights as retrospective_insights
from data.paper_account_store import PaperAccountStore
from pilots.retrospective_insights import batch_insights, classify_decision_state


def _wire_default_db_to(monkeypatch, db_url: str) -> None:
    """Overrides the session-wide autouse isolation fixtures so both stores'
    DEFAULT (no explicit db_url) resolution lands on the SAME physical
    tmp_path file for the duration of one test."""
    monkeypatch.setattr(paper_account_store_module, "resolve_database_url", lambda: db_url)
    monkeypatch.setattr(trade_decision_snapshot_store_module, "resolve_database_url", lambda: db_url)


def _seed_round_trip(store, *, tag, symbol, strategy_id, qty, entry_price, exit_price, decision_context=None):
    """Opens then fully closes one paper position via the REAL apply_fill
    write path -- this is what actually creates a matching
    (symbol, strategy_id, entry_ts) natural key across paper_closed_trades
    and trade_decision_snapshots (both stamped from the SAME ``now_ts``
    inside one apply_fill call), rather than hand-constructing rows that
    could silently drift out of sync with the real schema."""
    opened = store.apply_fill(
        f"{tag}-open", symbol, "buy", qty, entry_price, 0.0,
        strategy_id=strategy_id, decision_context=decision_context,
    )
    assert opened is True
    closed = store.apply_fill(f"{tag}-close", symbol, "sell", qty, exit_price, 0.0, strategy_id=strategy_id)
    assert closed is True


# ---------------------------------------------------------------------------
# classify_decision_state -- direct unit coverage of the 3-state rule
# ---------------------------------------------------------------------------


def test_classify_decision_state_no_snapshot_is_unknown():
    assert classify_decision_state(None) == "unknown"


def test_classify_decision_state_manual_provenance():
    assert classify_decision_state({"provenance": "manual"}) == "manual"


def test_classify_decision_state_automated_prefix_is_signal_driven():
    assert classify_decision_state({"provenance": "automated:options_auto_scan"}) == "signal_driven"


def test_classify_decision_state_unrecognized_provenance_is_unknown():
    assert classify_decision_state({"provenance": "something_else"}) == "unknown"


# ---------------------------------------------------------------------------
# Calibration is reused verbatim, never re-derived
# ---------------------------------------------------------------------------


def test_calibration_section_reused_verbatim_from_calibration_view():
    sentinel = {
        "bins": [{"bin_low": 0.0, "bin_high": 0.1, "win_rate": 0.5, "count": 7}],
        "total": 7,
        "overall_win_rate": 0.5,
        "calibration_error": 0.01,
        "n_scored_bins": 1,
        "n_bins": 10,
        "min_trades_per_bin": 5,
        "reason": None,
    }
    with patch("pilots.calibration.calibration_view", return_value=sentinel) as mocked:
        result = batch_insights()
    mocked.assert_called_once()
    assert result["calibration"] == sentinel


def test_calibration_section_degrades_honestly_when_calibration_view_raises():
    with patch("pilots.calibration.calibration_view", side_effect=RuntimeError("boom")):
        result = batch_insights()
    calibration = result["calibration"]
    assert calibration["bins"] == []
    assert calibration["total"] == 0
    assert calibration["overall_win_rate"] is None
    assert calibration["reason"] is not None


# ---------------------------------------------------------------------------
# Cohort separation is structural -- no combined/overall figure, ever
# ---------------------------------------------------------------------------


def test_cohorts_are_structurally_separate_with_no_overall_key(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'insights.db'}"
    _wire_default_db_to(monkeypatch, db_url)

    store = PaperAccountStore(db_url=db_url)
    store.get_account()  # seeds the account row with starting cash

    with patch(
        "data.paper_account_store.fmp_client.batch_quote",
        return_value=[{"symbol": "AAPL", "price": 100.0}],
    ):
        # Signal-driven cohort: two clean wins.
        _seed_round_trip(
            store, tag="sd1", symbol="AAPL", strategy_id="sig-1", qty=10.0,
            entry_price=100.0, exit_price=110.0,
            decision_context={"provenance": "automated:options_auto_scan", "conviction": 0.8},
        )
        _seed_round_trip(
            store, tag="sd2", symbol="MSFT", strategy_id="sig-2", qty=5.0,
            entry_price=200.0, exit_price=220.0,
            decision_context={"provenance": "automated:zero_dte_engine", "conviction": 0.65},
        )

        # Manual cohort: two clean losses.
        _seed_round_trip(
            store, tag="man1", symbol="TSLA", strategy_id="man-1", qty=10.0,
            entry_price=100.0, exit_price=90.0,
            decision_context={"provenance": "manual"},
        )
        _seed_round_trip(
            store, tag="man2", symbol="GOOG", strategy_id="man-2", qty=5.0,
            entry_price=50.0, exit_price=45.0,
            decision_context={"provenance": "manual"},
        )

        # Unknown cohort: no decision_context captured at all.
        _seed_round_trip(
            store, tag="unk1", symbol="NFLX", strategy_id="unk-1", qty=10.0,
            entry_price=300.0, exit_price=310.0,
            decision_context=None,
        )

    with patch("pilots.calibration.calibration_view", return_value={"reason": None}):
        result = batch_insights()

    cohorts = result["cohorts"]

    # Structural separation: exactly the three named cohorts, no "overall"/
    # "all" key anywhere at the top level or inside "cohorts".
    assert set(result.keys()) == {"calibration", "cohorts"}
    assert set(cohorts.keys()) == {"signal_driven", "manual", "unknown"}
    assert "overall" not in result
    assert "all" not in result
    assert "overall" not in cohorts
    assert "all" not in cohorts

    # Each cohort reflects ONLY its own trades.
    assert cohorts["signal_driven"]["n_trades"] == 2
    assert cohorts["signal_driven"]["win_rate"] == 1.0  # both wins
    assert cohorts["signal_driven"]["mean_realized_pnl_pct"] == pytest_approx_positive()

    assert cohorts["manual"]["n_trades"] == 2
    assert cohorts["manual"]["win_rate"] == 0.0  # both losses
    assert cohorts["manual"]["mean_realized_pnl_pct"] < 0

    assert cohorts["unknown"]["n_trades"] == 1
    assert cohorts["unknown"]["win_rate"] == 1.0


def pytest_approx_positive():
    """Small helper predicate object usable in an equality-style assert:
    any positive number satisfies ``== pytest_approx_positive()``."""

    class _Positive:
        def __eq__(self, other):
            return isinstance(other, (int, float)) and other > 0

        def __repr__(self):  # pragma: no cover - only used on assertion failure
            return "<a positive number>"

    return _Positive()


def test_signal_driven_and_manual_win_rates_never_bleed_into_each_other(tmp_path, monkeypatch):
    """A second, more pointed proof: signal-driven is ALL wins and manual is
    ALL losses in this fixture -- if either cohort's win_rate leaked into
    the other (or into a combined figure), this would catch it directly."""
    db_url = f"sqlite:///{tmp_path / 'insights2.db'}"
    _wire_default_db_to(monkeypatch, db_url)

    store = PaperAccountStore(db_url=db_url)
    store.get_account()

    with patch(
        "data.paper_account_store.fmp_client.batch_quote",
        return_value=[{"symbol": "AAPL", "price": 100.0}],
    ):
        _seed_round_trip(
            store, tag="sd1", symbol="AAPL", strategy_id="sig-1", qty=10.0,
            entry_price=100.0, exit_price=150.0,
            decision_context={"provenance": "automated:options_auto_scan", "conviction": 0.9},
        )
        _seed_round_trip(
            store, tag="man1", symbol="TSLA", strategy_id="man-1", qty=10.0,
            entry_price=100.0, exit_price=50.0,
            decision_context={"provenance": "manual"},
        )

    with patch("pilots.calibration.calibration_view", return_value={"reason": None}):
        result = batch_insights()

    cohorts = result["cohorts"]
    assert cohorts["signal_driven"]["win_rate"] == 1.0
    assert cohorts["manual"]["win_rate"] == 0.0
    assert cohorts["unknown"]["n_trades"] == 0
    assert cohorts["unknown"]["win_rate"] is None
    assert cohorts["unknown"]["mean_realized_pnl_pct"] is None


# ---------------------------------------------------------------------------
# Empty / no-trades case -- honest zero/null shape, no exception
# ---------------------------------------------------------------------------


def test_batch_insights_empty_db_returns_honest_zero_null_shape(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    _wire_default_db_to(monkeypatch, db_url)

    # Construct once (write mode) so the tables exist, but seed no trades.
    PaperAccountStore(db_url=db_url)

    result = batch_insights()

    for name in ("signal_driven", "manual", "unknown"):
        cohort = result["cohorts"][name]
        assert cohort["n_trades"] == 0
        assert cohort["win_rate"] is None
        assert cohort["mean_realized_pnl_pct"] is None

    # Calibration degrades to its own honest empty shape too (no closed
    # trades with conviction annotations exist either).
    assert result["calibration"]["total"] == 0
    assert result["calibration"]["overall_win_rate"] is None


def test_batch_insights_never_raises_when_db_has_never_been_written(tmp_path, monkeypatch):
    """A readonly construction against a db file that was never written at
    all (no write-mode store ever constructed) must degrade to the empty
    shape, not raise (CONSTRAINT #6)."""
    db_url = f"sqlite:///{tmp_path / 'never_written.db'}"
    _wire_default_db_to(monkeypatch, db_url)

    result = batch_insights()

    assert result["cohorts"]["unknown"]["n_trades"] == 0
    assert result["calibration"]["reason"] is not None


# ---------------------------------------------------------------------------
# Anti-shortcut rule: a name that LOOKS manual but has no captured snapshot
# must land in "unknown", never "manual" (decision.state is never inferred
# from strategy_id).
# ---------------------------------------------------------------------------


def test_strategy_id_named_manual_trade_with_no_snapshot_lands_in_unknown(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'anti_shortcut.db'}"
    _wire_default_db_to(monkeypatch, db_url)

    store = PaperAccountStore(db_url=db_url)
    store.get_account()

    with patch(
        "data.paper_account_store.fmp_client.batch_quote",
        return_value=[{"symbol": "SPY", "price": 400.0}],
    ):
        _seed_round_trip(
            store, tag="mt1", symbol="SPY", strategy_id="Manual Trade", qty=10.0,
            entry_price=400.0, exit_price=390.0,
            decision_context=None,  # deliberately NOT captured
        )

    with patch("pilots.calibration.calibration_view", return_value={"reason": None}):
        result = batch_insights()

    cohorts = result["cohorts"]
    assert cohorts["manual"]["n_trades"] == 0
    assert cohorts["unknown"]["n_trades"] == 1


def test_retrospective_insights_never_imports_heavy_engines_at_top_level():
    """Dependency-light convention check (mirrors pilots/calibration.py's
    documented convention): the module itself must not import
    data.paper_account_store / data.trade_decision_snapshot_store /
    pilots.calibration at module scope -- only lazily, inside function
    bodies, so a broken dependency degrades gracefully instead of breaking
    import of this module at process start."""
    import ast
    import inspect

    source = inspect.getsource(retrospective_insights)
    tree = ast.parse(source)
    module_level_imports = []
    for node in tree.body:  # only true top-level statements, not nested in functions
        if isinstance(node, ast.ImportFrom) and node.module:
            module_level_imports.append(node.module)
        elif isinstance(node, ast.Import):
            module_level_imports.extend(alias.name for alias in node.names)

    forbidden = {"data.paper_account_store", "data.trade_decision_snapshot_store", "pilots.calibration"}
    assert not (forbidden & set(module_level_imports))
