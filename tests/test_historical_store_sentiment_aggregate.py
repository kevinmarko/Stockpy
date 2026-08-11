"""tests/test_historical_store_sentiment_aggregate.py
=======================================================
Finding 5 regression: ``HistoricalStore.get_sentiment_aggregate_by_symbol()``
must compute a genuine credibility-WEIGHTED mean of ``final_weighted_score``
per symbol -- ``sum(final_weighted_score) / sum(credibility_weight)`` -- not
a plain per-document ``.mean()`` (which divides by document COUNT instead of
total credibility WEIGHT, and is therefore gameable by flooding a symbol with
many low-credibility documents; see ``signals/credibility.py``'s
``_MIN_CREDIBILITY_WEIGHT=0.1`` floor).

Recall ``final_weighted_score = raw_sentiment_score * credibility_weight``
(``data/sentiment_sources.py::CompositeSentimentSource._archive``), so the
weighted-mean SIGN can never differ from the naive per-document mean's sign
(both divide the same numerator by a positive denominator) -- what the fix
corrects is the MAGNITUDE: a naive count-based mean dilutes a genuine,
high-credibility signal toward zero as an attacker adds more low-credibility
documents, while the correctly-weighted mean stays anchored to how much real
credibility-weighted evidence exists, not how many documents were posted.
The tests below pin the exact corrected formula and demonstrate that
magnitude-suppression property directly (an old, count-based mean lands near
zero / gets diluted away, while the corrected weighted mean stays clearly,
meaningfully negative for the same underlying documents).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data.historical_store import HistoricalStore


def _make_doc(
    *,
    symbol: str,
    raw_sentiment_score: float,
    credibility_weight,
    as_of: datetime,
    is_bot: bool = False,
    source_name: str = "test_source",
    author_handle: str = "author",
):
    final_weighted_score = (
        raw_sentiment_score * credibility_weight if credibility_weight is not None else raw_sentiment_score
    )
    return {
        "as_of": as_of,
        "symbol": symbol,
        "source_name": source_name,
        "author_handle": author_handle,
        "text_content": "test document",
        "raw_sentiment_score": raw_sentiment_score,
        "credibility_weight": credibility_weight,
        "is_bot": is_bot,
        "final_weighted_score": final_weighted_score,
    }


# A fixed intraday timestamp (10:00 ET) so every document resolves to the
# same trading day regardless of when the test suite runs.
_AS_OF = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)  # 10:00 ET (EDT)
_TRADING_DAY = "2026-07-21"


class TestGenuineWeightedMeanFormula:
    """Pins the exact corrected formula against small, hand-computed cases."""

    def test_equal_weights_reduces_to_plain_mean(self, tmp_path):
        """A sanity check: when every document has the SAME credibility
        weight, the weighted mean and the plain mean coincide (the formulas
        are only supposed to diverge when weights differ across documents)."""
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))
        docs = [
            _make_doc(symbol="AAPL", raw_sentiment_score=1.0, credibility_weight=0.5, as_of=_AS_OF),
            _make_doc(symbol="AAPL", raw_sentiment_score=-1.0, credibility_weight=0.5, as_of=_AS_OF),
        ]
        store.save_sentiment_documents(docs)

        result = store.get_sentiment_aggregate_by_symbol(_TRADING_DAY)
        assert result["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(0.0, abs=1e-9)

    def test_unequal_weights_skew_toward_the_higher_credibility_document(self, tmp_path):
        """A high-credibility bearish doc (weight 0.9) should outweigh a
        low-credibility bullish doc (weight 0.1) of the same magnitude --
        the correctly weighted aggregate must be negative, not neutral."""
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))
        docs = [
            _make_doc(symbol="AAPL", raw_sentiment_score=1.0, credibility_weight=0.1, as_of=_AS_OF),
            _make_doc(symbol="AAPL", raw_sentiment_score=-1.0, credibility_weight=0.9, as_of=_AS_OF),
        ]
        store.save_sentiment_documents(docs)

        result = store.get_sentiment_aggregate_by_symbol(_TRADING_DAY)
        # sum(final_weighted_score) = 0.1*1.0 + 0.9*(-1.0) = -0.8
        # sum(credibility_weight)   = 0.1 + 0.9 = 1.0
        # weighted mean = -0.8 / 1.0 = -0.8
        assert result["AAPL"]["credibility_weighted_sentiment"] == pytest.approx(-0.8, abs=1e-9)
        # The OLD (buggy) plain mean over 2 documents would have been
        # (0.1*1.0 + 0.9*(-1.0)) / 2 = -0.4 -- also negative here, but
        # notably smaller in magnitude than the correctly weighted -0.8.
        naive_plain_mean = (0.1 * 1.0 + 0.9 * -1.0) / 2
        assert abs(result["AAPL"]["credibility_weighted_sentiment"]) > abs(naive_plain_mean)

    def test_uses_sum_credibility_weight_not_document_count_as_divisor(self, tmp_path):
        """Directly distinguishes the fix from the bug: 3 documents with
        weights 0.2/0.2/0.2 (sum=0.6, count=3) must divide by 0.6, not 3."""
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))
        docs = [
            _make_doc(symbol="MSFT", raw_sentiment_score=1.0, credibility_weight=0.2, as_of=_AS_OF),
            _make_doc(symbol="MSFT", raw_sentiment_score=1.0, credibility_weight=0.2, as_of=_AS_OF),
            _make_doc(symbol="MSFT", raw_sentiment_score=1.0, credibility_weight=0.2, as_of=_AS_OF),
        ]
        store.save_sentiment_documents(docs)

        result = store.get_sentiment_aggregate_by_symbol(_TRADING_DAY)
        # sum(final_weighted_score) = 3 * (1.0*0.2) = 0.6; sum(weight) = 0.6
        # weighted mean = 0.6 / 0.6 = 1.0 (full-strength, since every
        # document agrees) -- NOT 0.6/3 = 0.2 (the buggy divide-by-count
        # result, which would understate a unanimous signal just because
        # each document individually carried a low credibility weight).
        assert result["MSFT"]["credibility_weighted_sentiment"] == pytest.approx(1.0, abs=1e-9)


class TestFloodResistance:
    """Finding 5's core scenario: a flood of many low-credibility documents
    pushing a symbol's sentiment toward the flood's own direction must be
    resisted by the corrected weighted-mean formula relative to a naive
    per-document mean over the identical underlying documents."""

    def test_flood_of_low_credibility_bullish_docs_vs_few_high_credibility_bearish_docs(
        self, tmp_path
    ):
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))

        docs = []
        # 20 low-credibility (floor weight 0.1) bullish "flood" documents.
        for _ in range(20):
            docs.append(
                _make_doc(
                    symbol="TSLA", raw_sentiment_score=1.0, credibility_weight=0.1, as_of=_AS_OF,
                    source_name="low_cred_flood",
                )
            )
        # 3 high-credibility (weight 0.95) genuinely bearish documents.
        for _ in range(3):
            docs.append(
                _make_doc(
                    symbol="TSLA", raw_sentiment_score=-1.0, credibility_weight=0.95, as_of=_AS_OF,
                    source_name="institutional_bearish",
                )
            )
        store.save_sentiment_documents(docs)

        result = store.get_sentiment_aggregate_by_symbol(_TRADING_DAY)
        weighted_mean = result["TSLA"]["credibility_weighted_sentiment"]

        # sum(final_weighted_score) = 20*0.1*1.0 + 3*0.95*(-1.0) = 2.0 - 2.85 = -0.85
        # sum(credibility_weight)   = 20*0.1 + 3*0.95 = 2.0 + 2.85 = 4.85
        expected_weighted_mean = -0.85 / 4.85
        assert weighted_mean == pytest.approx(expected_weighted_mean, abs=1e-9)

        # The correctly-weighted aggregate is MEANINGFULLY negative --
        # reflecting the genuine, high-credibility bearish signal.
        assert weighted_mean < -0.1

        # The OLD (buggy) plain per-document mean over the same 23
        # documents dilutes that same signal toward zero purely because of
        # the flood's sheer document COUNT: -0.85 / 23 ~= -0.037 -- more
        # than 4x smaller in magnitude than the correctly weighted result,
        # and easily mistaken for "basically neutral" by any downstream
        # consumer applying a real-world materiality threshold.
        naive_plain_mean = -0.85 / 23
        assert abs(naive_plain_mean) < 0.05
        assert abs(weighted_mean) > 4 * abs(naive_plain_mean)

    def test_suppression_grows_stronger_as_the_flood_grows(self, tmp_path):
        """A second, distinct flood size (15 vs the 20 used above) against
        the SAME 3 high-credibility bearish documents, proving the
        suppression effect isn't a coincidence of one hand-picked document
        count: as more low-credibility documents are added, the naive
        per-document mean is diluted toward zero FASTER than the correctly
        weighted mean is -- i.e. the ratio |weighted_mean| / |naive_mean|
        grows with flood size, meaning weighting becomes relatively more
        effective at resisting a larger flood, not less."""
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))

        def _build(symbol: str, flood_count: int) -> None:
            docs = []
            for _ in range(flood_count):
                docs.append(
                    _make_doc(
                        symbol=symbol, raw_sentiment_score=1.0, credibility_weight=0.1,
                        as_of=_AS_OF, source_name="low_cred_flood",
                    )
                )
            for _ in range(3):
                docs.append(
                    _make_doc(
                        symbol=symbol, raw_sentiment_score=-1.0, credibility_weight=0.95,
                        as_of=_AS_OF, source_name="institutional_bearish",
                    )
                )
            store.save_sentiment_documents(docs)

        _build("SMALL_FLOOD", flood_count=15)
        _build("BIG_FLOOD", flood_count=25)
        result = store.get_sentiment_aggregate_by_symbol(_TRADING_DAY)

        # SMALL_FLOOD (n=15): numerator = 15*0.1 - 3*0.95 = 1.5 - 2.85 = -1.35
        #   weighted = -1.35 / (1.5 + 2.85) = -1.35 / 4.35
        #   naive    = -1.35 / 18
        small_weighted = result["SMALL_FLOOD"]["credibility_weighted_sentiment"]
        small_naive = -1.35 / 18
        assert small_weighted == pytest.approx(-1.35 / 4.35, abs=1e-9)

        # BIG_FLOOD (n=25): numerator = 25*0.1 - 3*0.95 = 2.5 - 2.85 = -0.35
        #   weighted = -0.35 / (2.5 + 2.85) = -0.35 / 5.35
        #   naive    = -0.35 / 28
        big_weighted = result["BIG_FLOOD"]["credibility_weighted_sentiment"]
        big_naive = -0.35 / 28
        assert big_weighted == pytest.approx(-0.35 / 5.35, abs=1e-9)

        small_ratio = abs(small_weighted) / abs(small_naive)
        big_ratio = abs(big_weighted) / abs(big_naive)
        assert big_ratio > small_ratio, (
            "the larger flood should show a BIGGER gap between the naive "
            "(diluted-toward-zero) mean and the correctly weighted mean, "
            "not a smaller one"
        )


class TestNullCredibilityWeightGuard:
    """CONSTRAINT #4 / #6: a symbol whose documents all carry a NULL
    credibility_weight must degrade to NaN (not a fabricated 0.0, not a
    ZeroDivisionError crash)."""

    def test_all_null_weights_degrades_to_nan(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))
        docs = [
            _make_doc(symbol="GME", raw_sentiment_score=0.5, credibility_weight=None, as_of=_AS_OF),
            _make_doc(symbol="GME", raw_sentiment_score=-0.5, credibility_weight=None, as_of=_AS_OF),
        ]
        store.save_sentiment_documents(docs)

        result = store.get_sentiment_aggregate_by_symbol(_TRADING_DAY)
        assert result["GME"]["credibility_weighted_sentiment"] != result["GME"]["credibility_weighted_sentiment"]  # NaN


class TestEmptyAndFailureDegradation:
    def test_no_rows_for_day_returns_empty_dict(self, tmp_path):
        store = HistoricalStore(db_path=str(tmp_path / "sentiment.db"))
        assert store.get_sentiment_aggregate_by_symbol("2099-01-01") == {}
