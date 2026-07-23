"""
tests/test_state_snapshot_parity.py
===================================
Cross-writer schema-parity guard for the two ``state_snapshot.json`` producers:

  * ``reporting.state_snapshot.write_state_snapshot``  — the ADVISORY writer
    (``main.py`` path), sourcing per-signal fields from
    ``engine.advisory.Recommendation.key_indicators``.
  * ``main_orchestrator._write_state_snapshot``        — the ORCHESTRATOR writer
    (full async pipeline), sourcing per-signal fields from ``dashboard_df``
    columns. This is a *superset* — it emits several columns the advisory path
    has no source for.

Both feed the SAME GUI Observability / Analytics panels (and the Pilots
SymbolDetail page via ``pilots/symbols.py``), so the two writers must now emit
the SAME per-signal key set — the advisory writer reached full parity once
``engine.advisory.evaluate()`` began threading the cross-sectional-momentum,
news-sentiment, CoVaR and post-trade-excursion values onto ``key_indicators``
(via ``main._build_context_extras``), and ``macro_status`` off
``Recommendation.macro_regime``.

Every per-signal metric must:

  * be present as a key in every record from BOTH writers (stable schema), and
  * serialize as JSON ``null`` — never a fabricated ``0.0`` (CONSTRAINT #4) —
    when the underlying value is genuinely unavailable (its pre-compute inputs
    were absent for that symbol/cycle). ``realized_slippage`` is
    ``evaluation_engine.EvaluationEngine``'s per-symbol implementation-shortfall
    figure (entry vs. arrival price) — the SAME source ``evaluate_portfolio()``
    uses for ``dashboard_df``'s column on the rich path — sourced from the
    per-symbol closed-trade excursion pre-compute, so it is null only when the
    symbol has no closed trade yet.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

import main_orchestrator as mo
import reporting.state_snapshot as ss
from settings import settings

# The three operator metrics this hardening pass cares about most.
_TRIPLET = ("news_sentiment", "realized_slippage", "covar_proxy")

# Per-signal keys BOTH writers must emit with identical spelling. Consumed by
# the same GUI panels, so a drift here silently blanks a column on one path.
SHARED_SIGNAL_FIELDS = {
    "symbol",
    "action",
    "kelly_target",
    "score",
    "price",
    "shares",
    "hmm_risk_on",
    "buy_range",
    "sell_range",
    "advisory_action",
    "advisory_conviction",
    "advisory_position_pct",
    "advisory_rationale",
    "value_z",
    "quality_z",
    "lowvol_z",
    "size_z",
    "multifactor_composite",
    "news_sentiment",
    "realized_slippage",
    "covar_proxy",
    "sector",
    "score_components",
    # Reached advisory parity: these were ORCHESTRATOR-only until
    # engine.advisory.evaluate() began threading them onto key_indicators
    # (xsec/news/covar/excursion via main._build_context_extras) and the advisory
    # writer began emitting macro_status off Recommendation.macro_regime.
    "macro_status",
    "xsec_12_1m",
    "xsec_momentum_rank",
    "mfe",
    "mae",
    "edge_ratio",
    # Position-sizing decomposition (webapp Strategy Matrix / Symbol Detail
    # "regime sizing impact" views) — reached ORCHESTRATOR parity via
    # pipeline/production_steps.py's Meta_Label_Composite/Regime_Multiplier/
    # Kelly_Target_{Pre,Post}_Regime threading into eval_results + the column
    # projection loop (mirrors the pre-existing Score_Components pattern).
    "meta_label_composite",
    "regime_multiplier",
    "kelly_target_pre_regime",
    "kelly_target_post_regime",
    # Guardrail telemetry (sizing/position_sizer.py) -- reached advisory parity
    # via engine.advisory.Recommendation.sizing_was_capped/
    # .sizing_binding_constraint, threaded through reporting/state_snapshot.py.
    "sizing_was_capped",
    "sizing_binding_constraint",
}

# The four sizing-decomposition fields, tested together below (mirrors _TRIPLET).
_SIZING_QUARTET = (
    "meta_label_composite",
    "regime_multiplier",
    "kelly_target_pre_regime",
    "kelly_target_post_regime",
)

# The advisory writer (main.py path) has no Wikipedia-pageviews attention
# source wired up -- Attention_Score is populated only by
# pipeline/production_steps.py, which the orchestrator path runs and the
# advisory path does not. Genuinely orchestrator-only, not an oversight;
# revisit if/when the advisory path grows an attention source of its own.
ORCHESTRATOR_ONLY_FIELDS: set[str] = {
    "attention_score",
}


# ── Advisory writer fixture (Recommendation / RunResult stubs) ───────────────


def _position(qty: float, price: float) -> SimpleNamespace:
    return SimpleNamespace(quantity=qty, current_price=price)


def _recommendation(symbol: str, **extra_ki) -> SimpleNamespace:
    key_indicators = {"score": 1.0, "garch_vol": 0.2}
    key_indicators.update(extra_ki)
    return SimpleNamespace(
        symbol=symbol,
        action="BUY",
        conviction=0.6,
        suggested_position_pct=0.02,
        rationale="r",
        key_indicators=key_indicators,
        score_components={"momentum": 0.5},
        buy_range="Buy: $10 - $11",
        sell_range="Sell: $12 - $13",
        suggested_exit_pct=0.5,
        sector="Technology",
        macro_regime="RISK ON",
    )


def _macro() -> SimpleNamespace:
    return SimpleNamespace(
        market_regime="RISK ON",
        vix_value=18.0,
        yield_curve=0.2,
        sahm_rule_indicator=0.1,
        credit_spread=4.0,
        hmm_risk_on_probability=0.8,
    )


@pytest.fixture()
def advisory_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    result = SimpleNamespace(
        snapshot=SimpleNamespace(positions={"AAPL": _position(10.0, 150.0)}),
        recommendations=[_recommendation("AAPL")],
    )
    ss.write_state_snapshot(result, _macro())
    snap = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
    return snap["signals"]


@pytest.fixture()
def orchestrator_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    # Row 1: the triplet columns present (round-trip). Row 2: absent (null).
    rows = [
        {
            "Symbol": "AAPL",
            "Action Signal": "BUY",
            "Kelly Target": 0.02,
            "Score": 1.0,
            "Price": 150.0,
            "Shares": 10.0,
            "HMM_Risk_On_Probability": 0.8,
            "buyRange": "Buy: $10 - $11",
            "sellRange": "Sell: $12 - $13",
            "Advisory_Action": "BUY",
            "Advisory_Conviction": 0.6,
            "Advisory_Position_Pct": 0.02,
            "Advisory_Rationale": "r",
            "Value_Z": 1.5,
            "Sector": "Technology",
            "News_Sentiment": 0.42,
            "Realized Slippage": 0.0011,
            "CoVaR Proxy": -0.05,
            "Meta_Label_Composite": 0.0,  # genuine hard-gate — must survive as 0.0, not 1.0/null
            "Regime_Multiplier": 0.75,
            "Kelly_Target_Pre_Regime": 0.04,
            "Kelly_Target_Post_Regime": 0.0,  # clamp(0.04 * 0.75 * 0.0, ...) == 0.0
            "Sizing_Was_Capped": "Yes",
            "Sizing_Binding_Constraint": "kelly_cap",
        },
        {
            "Symbol": "MSFT",
            "Action Signal": "HOLD",
            "Price": 300.0,
            "Shares": 0.0,
            # News_Sentiment / Realized Slippage / CoVaR Proxy / the sizing
            # quartet absent → null. Sizing_Was_Capped/Sizing_Binding_Constraint
            # ALSO absent here -- mirrors a dead-lettered ticker in the real
            # pipeline (pipeline/production_steps.py defaults these to None,
            # never ""/fabricated False -- see TestSizingGuardrailNullSafety
            # below, which locks in that this must round-trip as null, not false).
        },
    ]
    final_df = pd.DataFrame(rows)
    mo._write_state_snapshot({"market_regime": "RISK ON"}, final_df, ["AAPL", "MSFT"])
    snap = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
    return snap["signals"]


def _by_symbol(signals, symbol):
    return next(s for s in signals if s["symbol"] == symbol)


# ── Shared-field key-set parity ─────────────────────────────────────────────


class TestSharedKeyParity:
    def test_advisory_emits_all_shared_fields(self, advisory_signals):
        keys = set(advisory_signals[0])
        missing = SHARED_SIGNAL_FIELDS - keys
        assert not missing, f"advisory writer missing shared fields: {missing}"

    def test_orchestrator_emits_all_shared_fields(self, orchestrator_signals):
        keys = set(orchestrator_signals[0])
        missing = SHARED_SIGNAL_FIELDS - keys
        assert not missing, f"orchestrator writer missing shared fields: {missing}"

    def test_orchestrator_superset_documented(self, orchestrator_signals):
        """The orchestrator adds exactly the documented superset fields on top of
        the shared set — anything else new should be reconciled into
        SHARED_SIGNAL_FIELDS (and given an advisory source) or ORCHESTRATOR_ONLY."""
        keys = set(orchestrator_signals[0])
        extra = keys - SHARED_SIGNAL_FIELDS
        undocumented = extra - ORCHESTRATOR_ONLY_FIELDS
        assert not undocumented, (
            "orchestrator emits undocumented per-signal fields "
            f"{undocumented}; add them to SHARED_SIGNAL_FIELDS (with an advisory "
            "source) or ORCHESTRATOR_ONLY_FIELDS."
        )


# ── The three operator metrics: present + null-honest ───────────────────────


class TestTripletPresence:
    def test_advisory_has_triplet_keys(self, advisory_signals):
        for key in _TRIPLET:
            assert key in advisory_signals[0], f"advisory missing {key}"

    def test_orchestrator_has_triplet_keys(self, orchestrator_signals):
        for key in _TRIPLET:
            assert key in orchestrator_signals[0], f"orchestrator missing {key}"


class TestTripletNullHonesty:
    def test_advisory_triplet_is_null_when_absent(self, advisory_signals):
        """When ``key_indicators`` does not carry these (the fixture rec has an
        empty pre-compute), the advisory writer must emit JSON null — never a
        fabricated 0.0 the GUI would misread as a real reading (CONSTRAINT #4)."""
        sig = advisory_signals[0]
        for key in _TRIPLET:
            assert sig[key] is None, f"{key} must be null when absent from key_indicators"
            assert sig[key] != 0.0

    def test_orchestrator_triplet_round_trips_when_present(self, orchestrator_signals):
        sig = _by_symbol(orchestrator_signals, "AAPL")
        assert sig["news_sentiment"] == pytest.approx(0.42)
        assert sig["realized_slippage"] == pytest.approx(0.0011)
        assert sig["covar_proxy"] == pytest.approx(-0.05)

    def test_orchestrator_triplet_is_null_when_absent(self, orchestrator_signals):
        sig = _by_symbol(orchestrator_signals, "MSFT")
        for key in _TRIPLET:
            assert sig[key] is None, f"{key} must be null when the column is absent"
            assert sig[key] != 0.0


class TestTripletSourcedFromKeyIndicators:
    """If a future PR DOES thread the triplet onto key_indicators, the advisory
    writer must surface it (proving the plumbing reads key_indicators, not a
    hard-coded null)."""

    def test_advisory_triplet_round_trips_from_key_indicators(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        rec = _recommendation(
            "AAPL", news_sentiment=0.33, realized_slippage=0.002, covar_proxy=-0.07
        )
        result = SimpleNamespace(
            snapshot=SimpleNamespace(positions={}),
            recommendations=[rec],
        )
        ss.write_state_snapshot(result, _macro())
        snap = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))
        sig = snap["signals"][0]
        assert sig["news_sentiment"] == pytest.approx(0.33)
        assert sig["realized_slippage"] == pytest.approx(0.002)
        assert sig["covar_proxy"] == pytest.approx(-0.07)


class TestSizingQuartetPresence:
    def test_advisory_has_sizing_keys(self, advisory_signals):
        for key in _SIZING_QUARTET:
            assert key in advisory_signals[0], f"advisory missing {key}"

    def test_orchestrator_has_sizing_keys(self, orchestrator_signals):
        for key in _SIZING_QUARTET:
            assert key in orchestrator_signals[0], f"orchestrator missing {key}"


class TestSizingQuartetNullHonesty:
    """Mirrors TestTripletNullHonesty. The load-bearing case here is a GENUINE
    0.0 (a MetaLabeler hard-gate, or a regime multiplier zeroing sizing) —
    it must round-trip as 0.0, distinct from both a fabricated 1.0 default
    and a null "not computed" (CONSTRAINT #4)."""

    def test_orchestrator_sizing_round_trips_including_a_genuine_zero(self, orchestrator_signals):
        sig = _by_symbol(orchestrator_signals, "AAPL")
        assert sig["meta_label_composite"] == 0.0
        assert sig["meta_label_composite"] is not None
        assert sig["regime_multiplier"] == pytest.approx(0.75)
        assert sig["kelly_target_pre_regime"] == pytest.approx(0.04)
        assert sig["kelly_target_post_regime"] == 0.0
        assert sig["kelly_target_post_regime"] is not None

    def test_orchestrator_sizing_is_null_when_absent(self, orchestrator_signals):
        sig = _by_symbol(orchestrator_signals, "MSFT")
        for key in _SIZING_QUARTET:
            assert sig[key] is None, f"{key} must be null when the column is absent"

    def test_advisory_sizing_round_trips_including_a_genuine_zero(self, tmp_path, monkeypatch):
        """Regression test for the fixed `or 1.0` bug: a genuine 0.0
        meta_label_composite (a real MetaLabeler hard-gate) must survive
        reporting/state_snapshot.py's writer, not be silently rewritten to a
        fabricated 1.0."""
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        rec = _recommendation(
            "AAPL",
            meta_label_composite=0.0,
            regime_multiplier=0.75,
            kelly_target_pre_regime=0.04,
            kelly_target_post_regime=0.0,
        )
        result = SimpleNamespace(snapshot=SimpleNamespace(positions={}), recommendations=[rec])
        ss.write_state_snapshot(result, _macro())
        sig = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))["signals"][0]
        assert sig["meta_label_composite"] == 0.0
        assert sig["meta_label_composite"] is not None
        assert sig["kelly_target_post_regime"] == 0.0
        assert sig["kelly_target_post_regime"] is not None

    def test_advisory_sizing_is_null_when_absent(self, advisory_signals):
        # The base fixture rec has no sizing decomposition in key_indicators.
        sig = advisory_signals[0]
        for key in _SIZING_QUARTET:
            assert sig[key] is None, f"{key} must be null when absent from key_indicators"

    def test_advisory_snapshot_is_valid_strict_json(self, tmp_path, monkeypatch):
        """Regression test for the fixed raw-NaN-into-json.dumps bug: when the
        strategy engine didn't produce kelly_target_pre_regime/post_regime for
        a symbol, the advisory writer must emit JSON null, never a bare `NaN`
        token (invalid RFC-8259 — json.loads tolerates it, but strict parsers
        like the webapp's JSON.parse do not)."""
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        result = SimpleNamespace(
            snapshot=SimpleNamespace(positions={}), recommendations=[_recommendation("AAPL")]
        )
        ss.write_state_snapshot(result, _macro())
        raw = (tmp_path / "state_snapshot.json").read_text(encoding="utf-8")
        assert "NaN" not in raw, "snapshot contains a bare NaN token — invalid strict JSON"
        # json.loads with parse_constant raising proves no NaN/Infinity token
        # anywhere in the document, not just in the two fields under test.
        json.loads(
            raw,
            parse_constant=lambda tok: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {tok}")),
        )


class TestSizingGuardrailNullSafety:
    """Regression coverage for the fixed dead-letter fabrication bug: a
    ticker missing sizing telemetry entirely (dead-lettered this cycle, or
    -- for the advisory fixture -- simply not a BUY) must round-trip as JSON
    null, never the ACTIVE FALSE CLAIM ``sizing_was_capped: false``
    (CONSTRAINT #4). A ticker that genuinely WAS capped must round-trip its
    real True/constraint-name, not just be "present" in the key set."""

    def test_orchestrator_sizing_was_capped_true_round_trips(self, orchestrator_signals):
        sig = _by_symbol(orchestrator_signals, "AAPL")
        assert sig["sizing_was_capped"] is True
        assert sig["sizing_binding_constraint"] == "kelly_cap"

    def test_orchestrator_sizing_was_capped_is_null_when_absent(self, orchestrator_signals):
        """MSFT's Sizing_Was_Capped/Sizing_Binding_Constraint are absent from
        the fixture row entirely -- mirrors a dead-lettered ticker. Must be
        null, NOT false -- a plain str(x).lower()=="yes" coercion would
        collapse this into a fabricated "no ceiling bound" claim."""
        sig = _by_symbol(orchestrator_signals, "MSFT")
        assert sig["sizing_was_capped"] is None
        assert sig["sizing_binding_constraint"] is None

    def test_advisory_sizing_was_capped_defaults_false_not_fabricated_true(self, advisory_signals):
        """Unlike the orchestrator's dead-letter case above, a REAL
        Recommendation always carries this field with a genuine default
        (False -- nothing was capped because nothing was BUY-sized this
        cycle); the getattr fallback below only matters for an old/stub
        Recommendation shape, matching engine.advisory.Recommendation.sector's
        own getattr-guard precedent. Still must never be fabricated True."""
        sig = advisory_signals[0]
        assert sig["sizing_was_capped"] is False
        assert sig["sizing_binding_constraint"] is None

    def test_advisory_sizing_was_capped_true_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        rec = _recommendation("AAPL")
        rec.sizing_was_capped = True
        rec.sizing_binding_constraint = "advisory_max_position_pct"
        result = SimpleNamespace(snapshot=SimpleNamespace(positions={}), recommendations=[rec])
        ss.write_state_snapshot(result, _macro())
        sig = json.loads((tmp_path / "state_snapshot.json").read_text(encoding="utf-8"))["signals"][0]
        assert sig["sizing_was_capped"] is True
        assert sig["sizing_binding_constraint"] == "advisory_max_position_pct"


class TestAdvisoryNewFieldsRoundTrip:
    """The advisory writer surfaces the newly-wired SymbolDetail fields from
    ``key_indicators`` (proving the plumbing reads them, not a hard-coded null),
    and ``macro_status`` from ``Recommendation.macro_regime``."""

    def test_xsec_and_excursion_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        rec = _recommendation(
            "AAPL",
            xsec_12_1m=0.185,
            xsec_momentum_rank=0.9,
            mfe=0.12,
            mae=0.04,
            edge_ratio=3.0,
        )
        result = SimpleNamespace(
            snapshot=SimpleNamespace(positions={}), recommendations=[rec]
        )
        ss.write_state_snapshot(result, _macro())
        sig = json.loads(
            (tmp_path / "state_snapshot.json").read_text(encoding="utf-8")
        )["signals"][0]
        assert sig["xsec_12_1m"] == pytest.approx(0.185)
        assert sig["xsec_momentum_rank"] == pytest.approx(0.9)
        assert sig["mfe"] == pytest.approx(0.12)
        assert sig["mae"] == pytest.approx(0.04)
        assert sig["edge_ratio"] == pytest.approx(3.0)

    def test_macro_status_from_recommendation(self, advisory_signals):
        # _recommendation stub carries macro_regime="RISK ON".
        assert advisory_signals[0]["macro_status"] == "RISK ON"

    def test_new_numeric_fields_null_when_absent(self, advisory_signals):
        # The base fixture rec has no xsec/excursion in key_indicators → null,
        # never a fabricated 0.0 (CONSTRAINT #4).
        sig = advisory_signals[0]
        for key in ("xsec_12_1m", "xsec_momentum_rank", "mfe", "mae", "edge_ratio"):
            assert sig[key] is None, f"{key} must be null when absent from key_indicators"
            assert sig[key] != 0.0
