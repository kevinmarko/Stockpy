"""Tests for pilots.digest_models."""
import pytest
from datetime import datetime

from pilots.digest_models import DigestItem, DigestPayload


def test_digest_item_creation():
    """Verify that DigestItem holds the expected properties."""
    item = DigestItem(
        symbol="AAPL",
        reason="Strong earnings.",
        selection_type="Sector Gap"
    )
    assert item.symbol == "AAPL"
    assert item.reason == "Strong earnings."
    assert item.selection_type == "Sector Gap"


def test_digest_payload_defaults():
    """Verify that DigestPayload defaults to an empty list and valid datetime."""
    payload = DigestPayload()
    assert payload.items == []
    assert isinstance(payload.generated_at, datetime)


def test_digest_payload_max_items():
    """Verify that DigestPayload enforces a maximum of 5 items."""
    item = DigestItem("AAPL", "Reason", "Type")
    
    # Up to 5 is fine
    valid_payload = DigestPayload(items=[item] * 5)
    assert len(valid_payload.items) == 5

    # 6 should raise ValueError
    with pytest.raises(ValueError, match="cannot contain more than 5 items"):
        DigestPayload(items=[item] * 6)
