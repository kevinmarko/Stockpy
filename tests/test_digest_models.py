"""Tests for pilots.digest_models."""
import pytest
from datetime import datetime

from pilots.digest_models import DigestItem, DigestPayload


def test_digest_item_creation():
    """Verify that DigestItem holds the expected properties, including the
    new confidence_tier field."""
    item = DigestItem(
        symbol="AAPL",
        reason="Strong earnings.",
        selection_type="Sector Gap",
        confidence_tier="medium",
    )
    assert item.symbol == "AAPL"
    assert item.reason == "Strong earnings."
    assert item.selection_type == "Sector Gap"
    assert item.confidence_tier == "medium"


def test_digest_payload_defaults():
    """Verify that DigestPayload defaults to an empty list, a valid
    datetime, and honest personalization_active/reason defaults (never a
    fabricated True/blank -- CONSTRAINT #4)."""
    payload = DigestPayload()
    assert payload.items == []
    assert isinstance(payload.generated_at, datetime)
    assert payload.personalization_active is False
    assert payload.reason is None


def test_digest_payload_personalization_and_reason_fields():
    """Verify the new fields are stored as given, not derived/overridden
    by the dataclass itself (derivation is compose_digest's job)."""
    payload = DigestPayload(
        items=[],
        personalization_active=True,
        reason="Some honest explanation.",
    )
    assert payload.personalization_active is True
    assert payload.reason == "Some honest explanation."


def test_digest_payload_max_items():
    """Verify that DigestPayload enforces a maximum of 5 items."""
    item = DigestItem("AAPL", "Reason", "Type", "low")

    # Up to 5 is fine
    valid_payload = DigestPayload(items=[item] * 5)
    assert len(valid_payload.items) == 5

    # 6 should raise ValueError
    with pytest.raises(ValueError, match="cannot contain more than 5 items"):
        DigestPayload(items=[item] * 6)
