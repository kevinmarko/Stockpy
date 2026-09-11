import pytest
from unittest.mock import patch, MagicMock

from pilots.weekly_digest import compose_digest
from pilots.digest_models import DigestPayload


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_empty(mock_find_sectors, mock_store, mock_radar, mock_load):
    """No snapshot yet -> compose_digest calls radar_feed unconditionally
    (which itself honestly handles snapshot=None) and reuses radar_feed's
    own reason verbatim -- rung 1 of the fallback ladder."""
    mock_load.return_value = None
    mock_radar.return_value = {
        "items": [],
        "reason": "No state snapshot yet — run the pipeline first.",
    }

    payload = compose_digest()

    assert isinstance(payload, DigestPayload)
    assert len(payload.items) == 0
    assert payload.personalization_active is False
    assert payload.reason == "No state snapshot yet — run the pipeline first."
    # Nothing to personalize when there are no candidates -- neither
    # dependency should even be reached.
    mock_store.assert_not_called()
    mock_find_sectors.assert_not_called()


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_no_radar_candidates_reuses_radar_reason(
    mock_find_sectors, mock_store, mock_radar, mock_load
):
    """A real snapshot exists but Radar computed zero candidates this cycle
    (sparse DailySignals) -- rung 1's other trigger. The reason string must
    be radar_feed's own, never a re-typed literal."""
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [],
        "reason": "No signals computed yet for this cycle.",
    }

    payload = compose_digest()

    assert payload.items == []
    assert payload.personalization_active is False
    assert payload.reason == "No signals computed yet for this cycle."
    mock_store.assert_not_called()
    mock_find_sectors.assert_not_called()


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_empty_items_without_radar_reason_still_honest(
    mock_find_sectors, mock_store, mock_radar, mock_load
):
    """Even if radar_feed's own dict is missing a "reason" key entirely,
    compose_digest must still surface a non-empty, honest reason -- never a
    blank/None reason paired with an empty item list."""
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {"items": []}

    payload = compose_digest()

    assert payload.items == []
    assert payload.reason


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_empty_view_log_never_fabricates_personalized(
    mock_find_sectors, mock_store, mock_radar, mock_load
):
    """CRITICAL BUG regression: a fresh-install / no-view-history state
    (get_recently_viewed_symbols() -> []) must NEVER cause every candidate
    to be vacuously tagged "Personalized" just because none of them are in
    an empty set. personalization_active must be False and the reason must
    disclose that personalization isn't active. Sector-gap tagging (an
    orthogonal, portfolio-composition claim) is still allowed to fire."""
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "reason 1"},
            {"symbol": "MSFT", "sector": "Technology", "reason": "reason 2"},
            {"symbol": "GOOG", "sector": "Communication", "reason": "reason 3"},
        ]
    }

    mock_store_instance = MagicMock()
    mock_store_instance.get_recently_viewed_symbols.return_value = []  # fresh install
    mock_store.return_value = mock_store_instance

    mock_find_sectors.return_value = ["Technology"]

    payload = compose_digest()

    assert payload.personalization_active is False
    assert payload.reason is not None
    assert "personaliz" in payload.reason.lower()

    assert len(payload.items) == 3
    for item in payload.items:
        assert item.selection_type != "Personalized"
        assert item.confidence_tier != "high"

    # Sector-gap tagging is independent of view-tracking history and may
    # still honestly fire.
    aapl = next(i for i in payload.items if i.symbol == "AAPL")
    assert aapl.selection_type == "Sector Gap"
    assert aapl.confidence_tier == "medium"

    goog = next(i for i in payload.items if i.symbol == "GOOG")
    assert goog.selection_type == "Today's Radar"
    assert goog.confidence_tier == "low"


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_fallback_ladder(mock_find_sectors, mock_store, mock_radar, mock_load):
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "reason 1"},  # Not in viewed -> Personalized
            {"symbol": "MSFT", "sector": "Technology", "reason": "reason 2"},  # In viewed, but sector in gaps -> Sector Gap
            {"symbol": "GOOG", "sector": "Communication", "reason": "reason 3"},  # In viewed, sector not in gaps -> Today's Radar
            {"symbol": "AMZN", "sector": "Consumer", "reason": "reason 4"},  # Not in viewed -> Personalized
            {"symbol": "TSLA", "sector": "Consumer", "reason": "reason 5"},  # Not in viewed -> Personalized
            {"symbol": "META", "sector": "Communication", "reason": "reason 6"},  # Ignored (limit 5)
        ]
    }

    mock_store_instance = MagicMock()
    mock_store_instance.get_recently_viewed_symbols.return_value = ["MSFT", "GOOG"]
    mock_store.return_value = mock_store_instance

    mock_find_sectors.return_value = ["Technology"]

    payload = compose_digest()

    assert payload.personalization_active is True
    assert payload.reason is None

    assert len(payload.items) == 5
    assert payload.items[0].symbol == "AAPL"
    assert payload.items[0].selection_type == "Personalized"
    assert payload.items[0].confidence_tier == "high"

    assert payload.items[1].symbol == "MSFT"
    assert payload.items[1].selection_type == "Sector Gap"
    assert payload.items[1].confidence_tier == "medium"

    assert payload.items[2].symbol == "GOOG"
    assert payload.items[2].selection_type == "Today's Radar"
    assert payload.items[2].confidence_tier == "low"

    assert payload.items[3].symbol == "AMZN"
    assert payload.items[3].selection_type == "Personalized"
    assert payload.items[3].confidence_tier == "high"

    assert payload.items[4].symbol == "TSLA"
    assert payload.items[4].selection_type == "Personalized"
    assert payload.items[4].confidence_tier == "high"


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_sparse_data(mock_find_sectors, mock_store, mock_radar, mock_load):
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "reason 1"},
            {"symbol": "MSFT", "sector": "Technology", "reason": "reason 2"},
        ]
    }

    mock_store_instance = MagicMock()
    mock_store_instance.get_recently_viewed_symbols.return_value = ["AAPL", "MSFT"]
    mock_store.return_value = mock_store_instance

    mock_find_sectors.return_value = []

    payload = compose_digest()

    assert payload.personalization_active is True
    assert payload.reason is None
    assert len(payload.items) == 2
    for item in payload.items:
        assert item.selection_type == "Today's Radar"
        assert item.confidence_tier == "low"


@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_view_store_failure_degrades_to_no_personalization(
    mock_find_sectors, mock_store, mock_radar, mock_load
):
    """A SymbolViewStore construction/read failure must degrade to an
    honest empty view-history set (personalization inactive), never raise
    out of compose_digest (CONSTRAINT #6)."""
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [{"symbol": "AAPL", "sector": "Technology", "reason": "reason 1"}]
    }
    mock_store.side_effect = Exception("DB unavailable")
    mock_find_sectors.return_value = []

    payload = compose_digest()

    assert payload.personalization_active is False
    assert payload.reason is not None
    assert len(payload.items) == 1
    assert payload.items[0].selection_type == "Today's Radar"
