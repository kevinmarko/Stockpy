"""Tests for pilots/bridge_completeness.py — the empirical, cross-process-safe
paper-trade -> transactions_store bridge completeness metric.

Retrospective Learning Loop audit item #3: "Force a real bridge-write
failure in a test and confirm the completeness metric actually moves, not
just that it exists." ``TestBridgeFailureMovesTheMetric`` below is that
proof — it snapshots the metric BEFORE inducing a failure, again right
AFTER, and again after a subsequent successful bridge, so the transition
is unambiguous rather than merely "the function ran and returned some
number".

Every test constructs ``PaperAccountStore``/``TransactionsStore`` with an
EXPLICIT ``tmp_path``-backed file ``db_url`` (never ``:memory:`` for a pair
that must see each other's writes — SQLite gives each ``:memory:``
connection its own private database even with an identical URL string,
per ``conftest.py``'s own ``_isolate_paper_and_transactions_db_in_tests``
docstring) and passes that same ``db_url`` straight through to
``bridge_completeness_summary(db_url=...)`` — so these tests are fully
self-contained and never depend on (or need to extend) that session-wide
autouse isolation fixture. Mirrors the house style in
``tests/test_paper_account_store.py``'s own transactions_store-bridge
tests (``test_transactions_store_bridge_lands_row_fast_and_correctly`` /
``test_transactions_store_bridge_failure_is_non_fatal_and_visible``).
"""
from __future__ import annotations

import pytest

from data.paper_account_store import PaperAccountStore
from pilots.bridge_completeness import bridge_completeness_summary
from settings import settings


def _open_and_close(store: PaperAccountStore, symbol: str, strategy_id: str = "bridge_test") -> None:
    """Open then fully close a 10-share long on ``symbol`` via two real
    ``apply_fill`` calls — the exact production call shape that drives
    ``_record_closed_trade`` (and, when the flag is on, its
    transactions_store bridge)."""
    assert store.apply_fill(
        f"{symbol}_buy", symbol, "buy", 10.0, 100.0, strategy_id=strategy_id
    ) is True
    assert store.apply_fill(
        f"{symbol}_sell_{id(object())}", symbol, "sell", 10.0, 110.0, strategy_id=strategy_id
    ) is True


# ---------------------------------------------------------------------------
# 1. Bridge disabled + closed trades exist.
# ---------------------------------------------------------------------------


def test_disabled_bridge_reports_real_zero_never_a_fabricated_number(tmp_path):
    """Default settings (bridge OFF) + real closed paper trades in the
    window: the function must still empirically check transactions_store
    (which genuinely has nothing for these trades, since the bridge never
    ran) rather than special-casing "disabled" into a canned value.

    Chosen behavior, documented here per the task's "justify your choice":
    ``completeness_pct`` is a REAL, MEASURED ``0.0`` — not a fabricated
    placeholder — because the empirical check genuinely finds zero matching
    ``transactions_store`` rows for these symbols (the bridge never wrote
    any). This is preferred over collapsing straight to ``None`` because it
    is honestly derived from a real query, not a guess; either choice would
    satisfy "never a number implying real bridging happened" in isolation,
    but a *measured* 0.0 carries more information than an unconditional
    None would here. ``reason`` must still be populated because ``enabled``
    is False, per the function's documented contract.
    """
    assert settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED is False

    db_url = f"sqlite:///{tmp_path / 'disabled.db'}"
    store = PaperAccountStore(db_url=db_url)
    assert store._transactions_store is None  # bridge companion never constructed

    _open_and_close(store, "DIS1")
    _open_and_close(store, "DIS2")

    result = bridge_completeness_summary(window=200, db_url=db_url)

    assert result["enabled"] is False
    assert result["window"] == 200
    assert result["n_trades_checked"] == 2
    assert result["n_bridged"] == 0
    assert result["completeness_pct"] == pytest.approx(0.0)
    assert result["reason"] is not None
    assert "disabled" in result["reason"].lower() or "PAPER_TRADES_BRIDGE" in result["reason"]


# ---------------------------------------------------------------------------
# 2. Bridge enabled, every trade bridges successfully.
# ---------------------------------------------------------------------------


def test_enabled_bridge_all_trades_land_reports_100_percent(tmp_path, monkeypatch):
    """Bridge ON for the whole test; N trades opened+closed normally (no
    forced failures) must all land in transactions_store, and the metric
    must report a genuine 100%."""
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    db_url = f"sqlite:///{tmp_path / 'all_bridge.db'}"
    store = PaperAccountStore(db_url=db_url)
    assert store._transactions_store is not None

    for symbol in ("ALL1", "ALL2", "ALL3"):
        _open_and_close(store, symbol)

    assert store._transactions_bridge_failures == 0

    result = bridge_completeness_summary(window=200, db_url=db_url)

    assert result["enabled"] is True
    assert result["n_trades_checked"] == 3
    assert result["n_bridged"] == 3
    assert result["completeness_pct"] == pytest.approx(100.0)
    assert result["reason"] is None


# ---------------------------------------------------------------------------
# 3. THE AUDIT-CRITICAL CASE: force a real bridge-write failure and prove
#    the metric actually moves.
# ---------------------------------------------------------------------------


def test_forced_bridge_failure_measurably_drops_completeness(tmp_path, monkeypatch):
    """Snapshots the metric at three points -- before any failure, right
    after a forced failure, and after a subsequent successful bridge -- to
    PROVE the metric responds to a real, induced bridge-write failure
    rather than merely existing/returning a plausible-looking number.

    The failure is forced exactly like
    ``test_transactions_store_bridge_failure_is_non_fatal_and_visible`` in
    ``tests/test_paper_account_store.py`` (monkeypatch
    ``TransactionsStore.record_trade`` to raise) -- except scoped to a
    SPECIFIC symbol via a conditional wrapper, so the same test can also
    exercise a later, real successful bridge without needing to
    reconstruct the store or unpatch mid-test.
    """
    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)

    db_url = f"sqlite:///{tmp_path / 'forced_failure.db'}"
    store = PaperAccountStore(db_url=db_url)
    assert store._transactions_store is not None

    original_record_trade = store._transactions_store.record_trade

    def _fail_only_for_forced_symbol(*args, **kwargs):
        if kwargs.get("symbol") == "FORCED_FAIL":
            raise RuntimeError("forced bridge failure for test (audit item #3)")
        return original_record_trade(*args, **kwargs)

    monkeypatch.setattr(store._transactions_store, "record_trade", _fail_only_for_forced_symbol)

    # --- Snapshot 1: one clean trade bridges -- baseline is a real 100%. ---
    _open_and_close(store, "CLEAN_A")
    baseline = bridge_completeness_summary(window=200, db_url=db_url)
    assert baseline["n_trades_checked"] == 1
    assert baseline["n_bridged"] == 1
    assert baseline["completeness_pct"] == pytest.approx(100.0)

    # --- Snapshot 2: force a real bridge-write failure -- paper close must
    #     still succeed (fails OPEN), but the bridge write is lost. ---
    assert store.apply_fill("forced_fail_buy", "FORCED_FAIL", "buy", 10.0, 100.0, strategy_id="bridge_test") is True
    assert store.apply_fill("forced_fail_sell", "FORCED_FAIL", "sell", 10.0, 90.0, strategy_id="bridge_test") is True
    assert store._transactions_bridge_failures == 1  # sanity check on the induced failure itself

    after_failure = bridge_completeness_summary(window=200, db_url=db_url)
    assert after_failure["n_trades_checked"] == 2
    assert after_failure["n_bridged"] == 1
    assert after_failure["completeness_pct"] == pytest.approx(50.0)
    # THE PROOF: the metric genuinely moved, and in the correct direction.
    assert after_failure["completeness_pct"] < baseline["completeness_pct"]
    assert after_failure["n_bridged"] < after_failure["n_trades_checked"]

    # --- Snapshot 3: one more clean trade closes after the failure -- the
    #     metric must reflect the new mix (2 of 3 bridged), not get stuck. ---
    _open_and_close(store, "CLEAN_B")
    after_recovery = bridge_completeness_summary(window=200, db_url=db_url)
    assert after_recovery["n_trades_checked"] == 3
    assert after_recovery["n_bridged"] == 2
    assert after_recovery["completeness_pct"] == pytest.approx(200.0 / 3.0)
    assert after_recovery["completeness_pct"] < 100.0
    assert after_recovery["n_bridged"] < after_recovery["n_trades_checked"]
    assert after_recovery["reason"] is None  # enabled=True and n_trades_checked > 0


# ---------------------------------------------------------------------------
# 4. No closed trades at all.
# ---------------------------------------------------------------------------


def test_no_closed_trades_reports_honest_empty_shape(tmp_path):
    """A real store with the schema created but genuinely zero closed
    trades must report ``completeness_pct is None`` (never a fabricated
    percentage of nothing) plus a populated ``reason``."""
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    PaperAccountStore(db_url=db_url)  # creates the schema; no trades made

    result = bridge_completeness_summary(window=200, db_url=db_url)

    assert result["n_trades_checked"] == 0
    assert result["n_bridged"] == 0
    assert result["completeness_pct"] is None
    assert result["reason"] is not None


def test_no_closed_trades_at_all_never_fabricates_even_on_a_never_touched_db(tmp_path):
    """Same contract against a database file that was never created by any
    store at all (the coldest possible start)."""
    db_url = f"sqlite:///{tmp_path / 'never_touched.db'}"

    result = bridge_completeness_summary(window=200, db_url=db_url)

    assert result["n_trades_checked"] == 0
    assert result["completeness_pct"] is None
    assert result["reason"] is not None


# ---------------------------------------------------------------------------
# 5. Never raises, under multiple distinct failure modes.
# ---------------------------------------------------------------------------


def test_never_raises_on_in_memory_db_url():
    """``db_config.create_readonly_db_engine`` RAISES ``ValueError`` for
    ``sqlite:///:memory:`` (a read-only engine is not meaningful for a
    private, per-connection in-memory db) -- a real, naturally-occurring
    failure mode this function must degrade through rather than propagate."""
    result = bridge_completeness_summary(window=50, db_url="sqlite:///:memory:")

    assert isinstance(result, dict)
    assert result["n_trades_checked"] == 0
    assert result["completeness_pct"] is None
    assert result["reason"] is not None


def test_never_raises_when_paper_account_store_read_is_broken(tmp_path, monkeypatch):
    """A broken ``get_full_closed_trades`` (e.g. a corrupt DB driver error)
    must degrade honestly, never propagate."""
    import data.paper_account_store as pas_module

    db_url = f"sqlite:///{tmp_path / 'broken_paper_read.db'}"
    PaperAccountStore(db_url=db_url)  # real schema so construction itself is fine

    def _boom(self, symbol=None, limit=100):
        raise RuntimeError("simulated corrupt paper_closed_trades read")

    monkeypatch.setattr(pas_module.PaperAccountStore, "get_full_closed_trades", _boom)

    result = bridge_completeness_summary(window=200, db_url=db_url)

    assert isinstance(result, dict)
    assert result["n_trades_checked"] == 0
    assert result["completeness_pct"] is None
    assert result["reason"] is not None


def test_never_raises_when_transactions_store_read_is_broken(tmp_path, monkeypatch):
    """Real closed paper trades exist, but the transactions_store side is
    broken (e.g. a corrupt DB driver error on the historical-match query)
    -- must degrade honestly, never propagate, and never claim a
    completeness number computed from a matching step that never ran."""
    import transactions_store as ts_module

    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", False)

    db_url = f"sqlite:///{tmp_path / 'broken_tx_read.db'}"
    store = PaperAccountStore(db_url=db_url)
    _open_and_close(store, "BROKEN1")

    def _boom(self, symbols):
        raise RuntimeError("simulated corrupt transactions_store read")

    monkeypatch.setattr(ts_module.TransactionsStore, "get_trade_histories_batch", _boom)

    result = bridge_completeness_summary(window=200, db_url=db_url)

    assert isinstance(result, dict)
    assert result["n_trades_checked"] == 0
    assert result["n_bridged"] == 0
    assert result["completeness_pct"] is None
    assert result["reason"] is not None


def test_never_raises_when_transactions_store_import_itself_fails(tmp_path, monkeypatch):
    """A totally missing/broken transactions_store MODULE (import failure,
    not just a broken method) must also degrade honestly -- this exercises
    the function's own lazy-import try/except, distinct from the
    already-broken-instance case above."""
    import pilots.bridge_completeness as bc_module

    db_url = f"sqlite:///{tmp_path / 'missing_tx_module.db'}"
    store = PaperAccountStore(db_url=db_url)
    _open_and_close(store, "NOMODULE1")

    # Simulate "import transactions_store" raising by making the name
    # unresolvable at the point bridge_completeness_summary lazily imports
    # it -- patch builtins.__import__ narrowly scoped to this one module name
    # so nothing else in the test session is disturbed.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "transactions_store":
            raise ImportError("simulated missing transactions_store module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    result = bc_module.bridge_completeness_summary(window=200, db_url=db_url)

    assert isinstance(result, dict)
    assert result["n_trades_checked"] == 0
    assert result["completeness_pct"] is None
    assert result["reason"] is not None


# ---------------------------------------------------------------------------
# Code-review finding: the per-trade matching loop used to re-scan (and
# re-parse via _to_naive_utc) a symbol's FULL transactions_store history
# once per trade sharing that symbol -- O(n_trades x history_size). Fixed to
# normalize each symbol's history ONCE. This test proves the fix, not just
# that matching still works: with 5 trades sharing one symbol against a
# 4-row history, _to_naive_utc must be called on the history rows' 8
# timestamps (4 rows x 2 fields) exactly ONCE each -- 8 total -- never 5x8.
# ---------------------------------------------------------------------------


def test_history_normalization_happens_once_per_symbol_not_once_per_trade(tmp_path, monkeypatch):
    import pilots.bridge_completeness as bc_module

    monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
    db_url = f"sqlite:///{tmp_path / 'normalize_once.db'}"
    store = PaperAccountStore(db_url=db_url)

    # 5 real closed round-trips on the SAME symbol, all genuinely bridged.
    for i in range(5):
        assert store.apply_fill(f"SHARED_buy_{i}", "SHARED", "buy", 10.0, 100.0, strategy_id="s1") is True
        assert store.apply_fill(f"SHARED_sell_{i}", "SHARED", "sell", 10.0, 110.0, strategy_id="s1") is True

    real_to_naive_utc = bc_module._to_naive_utc
    call_count = {"n": 0}

    def _counting_to_naive_utc(value):
        call_count["n"] += 1
        return real_to_naive_utc(value)

    monkeypatch.setattr(bc_module, "_to_naive_utc", _counting_to_naive_utc)

    result = bc_module.bridge_completeness_summary(window=200, db_url=db_url)

    assert result["n_trades_checked"] == 5
    assert result["n_bridged"] == 5

    # History normalization: 5 rows x 2 fields (entry_ts, exit_ts) = 10
    # calls, done ONCE regardless of how many trades share the symbol.
    # Per-trade calls: entry_ts + exit_ts for each of the 5 checked trades
    # = 10 more. Total = 20 -- NOT 5 (trades) x 10 (history calls) = 50,
    # which is what the pre-fix O(n_trades x history_size) re-scan would
    # have produced (10 history calls repeated once per trade, plus the
    # same 10 per-trade calls = 60). Asserting the exact bounded total
    # proves the history pass happens once, not once per trade.
    assert call_count["n"] == 20, (
        f"expected exactly 20 _to_naive_utc calls (10 one-time history-normalization "
        f"+ 10 per-trade), got {call_count['n']} -- history is being re-normalized "
        f"per trade instead of once per symbol"
    )
