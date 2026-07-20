"""
tests/test_pilots_strategy_matrix.py
====================================
Tests for ``pilots/strategy_matrix.py`` — the pure, dependency-light reader that
assembles the signal-module weight/enablement matrix for ``GET /strategy/matrix``.

Covers: the SIGNAL_WEIGHTS ∪ snapshot-score_components module union and per-row
``source`` provenance; graceful degradation on a missing / corrupt snapshot;
PARITY of the duplicated ``_resolve_effective_weights`` / ``_MAX_WEIGHT`` against
the real ``signals.aggregator`` originals (tests are NOT AST-guarded, so importing
``signals`` here is fine); and a dependency-light ALLOWLIST guard over
``pilots/strategy_matrix.py``, ``pilots/options.py``, and
``pilots/strategy_health.py`` (each promises a narrow, specific import surface —
see the guard test's own docstring for why ``import signals`` on the API import
path is the trap the guard exists for).
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from settings import settings
from pilots import strategy_matrix as sm


# ---------------------------------------------------------------------------
# Module union + provenance
# ---------------------------------------------------------------------------


def _write_snapshot(tmp_path, *, regime="RISK ON", signals):
    snap = {
        "timestamp": "2026-07-17T00:00:00+00:00",
        "market_regime": regime,
        "signals": signals,
    }
    p = tmp_path / "state_snapshot.json"
    p.write_text(json.dumps(snap), encoding="utf-8")
    return str(p)


def test_module_in_weights_and_snapshot_is_both(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"macd_momentum": 20.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    monkeypatch.setattr(settings, "REGIME_SIGNAL_WEIGHTS", {}, raising=False)
    path = _write_snapshot(
        tmp_path,
        signals=[{"symbol": "AAA", "score_components": {"macd_momentum": 2.0}}],
    )
    out = sm.strategy_matrix(snapshot_path=path)
    row = next(m for m in out["modules"] if m["name"] == "macd_momentum")
    assert row["source"] == "both"
    assert row["weight"] == 20.0
    assert row["effective_weight"] == 20.0  # no regime overrides -> effective == configured
    assert row["effective_weight_regime"] is None
    assert row["contributed_last_run"] is True
    assert row["symbols_scored"] == 1


def test_module_in_snapshot_only_is_snapshot_source_with_null_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    monkeypatch.setattr(settings, "REGIME_SIGNAL_WEIGHTS", {}, raising=False)
    path = _write_snapshot(
        tmp_path,
        signals=[{"symbol": "AAA", "score_components": {"orphan_module": 1.0}}],
    )
    out = sm.strategy_matrix(snapshot_path=path)
    row = next(m for m in out["modules"] if m["name"] == "orphan_module")
    assert row["source"] == "snapshot"
    assert row["weight"] is None  # never a fabricated 0.0 (CONSTRAINT #4)
    assert row["contributed_last_run"] is True


def test_module_in_weights_only_never_scored(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"typoed_key": 5.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    monkeypatch.setattr(settings, "REGIME_SIGNAL_WEIGHTS", {}, raising=False)
    path = _write_snapshot(
        tmp_path,
        signals=[{"symbol": "AAA", "score_components": {"other": 1.0}}],
    )
    out = sm.strategy_matrix(snapshot_path=path)
    row = next(m for m in out["modules"] if m["name"] == "typoed_key")
    assert row["source"] == "weights"
    assert row["contributed_last_run"] is False
    assert row["symbols_scored"] == 0


def test_disabled_reported_and_enabled_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"a": 1.0, "b": 2.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", ["b"], raising=False)
    monkeypatch.setattr(settings, "REGIME_SIGNAL_WEIGHTS", {}, raising=False)
    path = _write_snapshot(tmp_path, signals=[])
    out = sm.strategy_matrix(snapshot_path=path)
    assert out["disabled"] == ["b"]
    by = {m["name"]: m for m in out["modules"]}
    assert by["a"]["enabled"] is True
    assert by["b"]["enabled"] is False


def test_pinned_zero_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "SIGNAL_WEIGHTS", {"regime_multiplier": 0.0, "macd_momentum": 10.0}, raising=False
    )
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    path = _write_snapshot(tmp_path, signals=[])
    out = sm.strategy_matrix(snapshot_path=path)
    by = {m["name"]: m for m in out["modules"]}
    assert by["regime_multiplier"]["pinned_zero"] is True
    assert by["macd_momentum"]["pinned_zero"] is False


# ---------------------------------------------------------------------------
# Degradation (CONSTRAINT #6)
# ---------------------------------------------------------------------------


def test_missing_snapshot_degrades_to_weights_only_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"a": 1.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    out = sm.strategy_matrix(snapshot_path=str(tmp_path / "does_not_exist.json"))
    assert out["reason"] is not None
    assert out["as_of"] is None
    row = next(m for m in out["modules"] if m["name"] == "a")
    assert row["source"] == "weights"
    assert row["symbols_scored"] is None  # None, never a fabricated 0


def test_corrupt_snapshot_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"a": 1.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    p = tmp_path / "state_snapshot.json"
    p.write_text("{not valid json", encoding="utf-8")
    out = sm.strategy_matrix(snapshot_path=str(p))  # must not raise
    assert out["reason"] is not None


def test_effective_weight_null_when_overrides_active_but_regime_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"a": 10.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    monkeypatch.setattr(
        settings, "REGIME_SIGNAL_WEIGHTS", {"RECESSION": {"a": 0.0}}, raising=False
    )
    path = _write_snapshot(tmp_path, regime="UNKNOWN", signals=[])
    out = sm.strategy_matrix(snapshot_path=path)
    row = next(m for m in out["modules"] if m["name"] == "a")
    assert out["regime_overrides_active"] is True
    assert row["effective_weight"] is None  # can't resolve honestly -> None, not a guess
    assert row["effective_weight_regime"] is None


def test_effective_weight_resolved_when_overrides_active_and_regime_known(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {"a": 10.0, "b": 20.0}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    monkeypatch.setattr(
        settings, "REGIME_SIGNAL_WEIGHTS", {"RECESSION": {"a": 0.0}}, raising=False
    )
    path = _write_snapshot(tmp_path, regime="RECESSION", signals=[])
    out = sm.strategy_matrix(snapshot_path=path)
    by = {m["name"]: m for m in out["modules"]}
    assert by["a"]["effective_weight"] == 0.0
    assert by["a"]["effective_weight_regime"] == "RECESSION"
    assert by["b"]["effective_weight"] == 20.0  # unlisted -> inherits flat weight


# ---------------------------------------------------------------------------
# Meta-label confidence distribution (_meta_label_distribution / the
# "meta_label" field on strategy_matrix()'s return)
# ---------------------------------------------------------------------------


def _meta_label_signals(values):
    """Build a signals[] list, one entry per value. `None` means the key is
    OMITTED entirely (the "never computed" case, distinct from a coerce-to-
    None non-numeric value)."""
    out = []
    for i, v in enumerate(values):
        sig = {"symbol": f"SYM{i}"}
        if v is not None:
            sig["meta_label_composite"] = v
        out.append(sig)
    return out


def test_all_unity_distribution(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    path = _write_snapshot(tmp_path, signals=_meta_label_signals([1.0] * 5))
    ml = sm.strategy_matrix(snapshot_path=path)["meta_label"]
    assert ml["count"] == 5
    assert ml["missing"] == 0
    assert ml["n_gated"] == 0
    assert ml["all_unity"] is True
    assert ml["min"] == pytest.approx(1.0)
    assert ml["max"] == pytest.approx(1.0)
    assert ml["reason"] is None
    # All 5 land in the top bin ([0.95, 1.0]); every other bin is empty.
    assert ml["bins"][-1]["count"] == 5
    assert sum(b["count"] for b in ml["bins"][:-1]) == 0
    assert sum(b["count"] for b in ml["bins"]) == ml["count"]


def test_mixed_distribution_with_genuine_hard_gate(tmp_path, monkeypatch):
    """Regression test for the fixed `or 1.0` bug: a real 0.0 (a MetaLabeler
    hard-gate) must be COUNTED by n_gated, not silently rewritten away."""
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    path = _write_snapshot(
        tmp_path, signals=_meta_label_signals([1.0, 1.0, 0.0, 0.5])
    )
    ml = sm.strategy_matrix(snapshot_path=path)["meta_label"]
    assert ml["count"] == 4
    assert ml["n_gated"] == 1
    assert ml["all_unity"] is False
    assert ml["min"] == pytest.approx(0.0)
    assert ml["max"] == pytest.approx(1.0)


def test_missing_and_non_numeric_values_counted_as_missing_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    signals = _meta_label_signals([1.0, None])  # None -> key omitted entirely
    signals.append({"symbol": "SYM_NAN", "meta_label_composite": float("nan")})
    signals.append({"symbol": "SYM_INF", "meta_label_composite": float("inf")})
    signals.append({"symbol": "SYM_STR", "meta_label_composite": "not-a-number"})
    path = _write_snapshot(tmp_path, signals=signals)
    ml = sm.strategy_matrix(snapshot_path=path)["meta_label"]
    assert ml["count"] == 1  # only the real 1.0
    assert ml["missing"] == 4  # omitted key + NaN + inf + non-numeric
    assert ml["reason"] is None  # values is non-empty -> no "empty" reason


def test_empty_snapshot_gives_honest_reason_not_a_fabricated_chart(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    path = _write_snapshot(tmp_path, signals=[])
    ml = sm.strategy_matrix(snapshot_path=path)["meta_label"]
    assert ml["count"] == 0
    assert ml["all_unity"] is False  # never true on an empty set
    assert ml["min"] is None
    assert ml["max"] is None
    assert ml["reason"] is not None
    assert sum(b["count"] for b in ml["bins"]) == 0


def test_no_snapshot_at_all_gives_meta_label_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    out = sm.strategy_matrix(snapshot_path=str(tmp_path / "does_not_exist.json"))
    assert out["meta_label"]["count"] == 0
    assert out["meta_label"]["reason"] is not None


def test_min_confidence_sourced_from_settings_not_a_literal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SIGNAL_WEIGHTS", {}, raising=False)
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [], raising=False)
    monkeypatch.setattr(settings, "META_LABEL_MIN_CONFIDENCE", 0.55, raising=False)
    path = _write_snapshot(tmp_path, signals=[])
    ml = sm.strategy_matrix(snapshot_path=path)["meta_label"]
    assert ml["min_confidence"] == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# Parity with the real signals.aggregator (duplicated to stay off the API import path)
# ---------------------------------------------------------------------------


def test_max_weight_matches_aggregator():
    from signals.aggregator import MAX_SANE_SIGNAL_WEIGHT

    assert sm._MAX_WEIGHT == MAX_SANE_SIGNAL_WEIGHT


@pytest.mark.parametrize(
    "regime, overrides",
    [
        ("RISK ON", {}),  # empty overrides -> defaults
        ("RECESSION", {"RECESSION": {"a": 0.0, "b": 99.0}}),  # exact match
        ("RISK ON", {"_default": {"a": 5.0}}),  # _default catch-all
        ("RISK ON", {"RECESSION": {"a": 0.0}}),  # no match, no _default -> defaults
        ("CREDIT EVENT", {"_default": {"b": 1.0}, "CREDIT EVENT": {"a": 2.0}}),  # exact wins
        ("RISK ON", {"RISK ON": {"a": 7.0}}),  # partial merge (b inherits)
    ],
)
def test_resolve_effective_weights_matches_aggregator(regime, overrides):
    from signals.aggregator import resolve_regime_weights

    defaults = {"a": 10.0, "b": 20.0, "c": 30.0}
    assert sm._resolve_effective_weights(regime, overrides, defaults) == resolve_regime_weights(
        regime, overrides, defaults
    )


# ---------------------------------------------------------------------------
# Dependency-light allowlist guard (stronger than the AST denylist)
# ---------------------------------------------------------------------------


def _import_roots(source: str) -> set:
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    "module_name",
    ["strategy_matrix", "options", "strategy_health", "commands", "agentic", "discovery", "scan_config_store", "watchlist_writer"],
)
def test_pilots_read_helpers_stay_dependency_light(module_name):
    """api/pilots_api.py imports pilots.strategy_matrix, pilots.options, and
    pilots.strategy_health. The AST guard on pilots_api.py walks THAT file
    only, first-segment-only and NON-transitively — so ``import signals`` here
    would pass the guard while pulling ~700 modules and every signal module's
    own future imports onto the API import path (the trap the guard's
    ``desktop`` entry exists for). This is an ALLOWLIST (stronger than a
    denylist): each module's docstring promises a specific, narrow import
    surface, and nothing pinned that until now.

    ``pilots`` and ``validation`` are additionally allowed roots (beyond pure
    stdlib + ``settings``) for ``pilots.strategy_health`` specifically — it
    reuses ``pilots.catalog``/``pilots.performance`` (both independently
    confirmed dependency-light by their own docstrings) and
    ``validation.thresholds`` (a pure-constants module with zero imports of its
    own — confirmed by inspection, not just its docstring). Deliberately NOT
    ``validation.harness`` — that module's top-level imports (``yfinance``,
    ``universe_engine``, ``execution.cost_model``, ...) are far heavier, which
    is why ``pilots/strategy_health.py`` PORTS its tiny JSONL history-read
    logic locally instead of importing it (see that module's docstring).
    """
    path = pathlib.Path(__file__).resolve().parent.parent / "pilots" / f"{module_name}.py"
    roots = _import_roots(path.read_text(encoding="utf-8"))
    allowed = {"__future__", "json", "logging", "math", "pathlib", "typing", "settings"}
    if module_name == "strategy_health":
        allowed = allowed | {"pilots", "validation"}
    if module_name == "discovery":
        # pilots.discovery composes pilots.scan_config_store.ScanConfigStore
        # (itself independently confirmed dependency-light below) for the
        # scan_configs section of its payload.
        allowed = allowed | {"pilots"}
    if module_name == "scan_config_store":
        allowed = allowed | {"datetime"}
    if module_name == "watchlist_writer":
        # Stdlib-only append helper for watchlist.txt (no settings, no engines):
        # os (WATCHLIST env precedence check, mirroring main._load_watchlist),
        # re (strict ticker-shape validation), dataclasses (result container),
        # datetime (audit-comment timestamp).
        allowed = allowed | {"os", "re", "dataclasses", "datetime"}
    assert roots <= allowed, f"pilots/{module_name}.py imports outside the allowlist: {roots - allowed}"
