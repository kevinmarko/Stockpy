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


def test_digest_item_confidence_tier_is_structurally_enforced_not_caller_settable():
    """CONFIRMED BUG regression: confidence_tier's docstring claims it is
    'derived mechanically from selection_type... never set independently,
    so the two can never drift apart' -- but before this fix, nothing on
    DigestItem itself enforced that; it was a plain, independently-settable
    field, and a mismatched pair HAD already shipped in
    webapp/src/api/mock.ts's fixtures. __post_init__ now always overwrites
    whatever confidence_tier value is passed (or omitted) with the
    mechanically-correct one, making a mismatched pair impossible to
    construct at all -- this test passes a deliberately WRONG
    confidence_tier for each selection_type and asserts it gets corrected,
    not honored."""
    assert DigestItem(symbol="A", reason="r", selection_type="Personalized", confidence_tier="low").confidence_tier == "high"
    assert DigestItem(symbol="A", reason="r", selection_type="Sector Gap", confidence_tier="high").confidence_tier == "medium"
    assert DigestItem(symbol="A", reason="r", selection_type="Today's Radar", confidence_tier="high").confidence_tier == "low"
    # Omitting it entirely still derives the correct value.
    assert DigestItem(symbol="A", reason="r", selection_type="Personalized").confidence_tier == "high"
    # An unrecognized selection_type (should never occur in production)
    # degrades to the least-confident tier rather than raising.
    assert DigestItem(symbol="A", reason="r", selection_type="Bogus").confidence_tier == "low"


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


def test_weekly_digest_interval_hours_rejects_non_positive():
    """CONFIRMED gap: WEEKLY_DIGEST_INTERVAL_HOURS controls
    desktop/daemon_runtime.py::maybe_dispatch_weekly_digest's throttle
    window (timedelta(hours=settings.WEEKLY_DIGEST_INTERVAL_HOURS)) -- a
    zero or negative value makes `(now - last_dispatch) < interval` false
    on essentially every check, defeating the once-per-interval throttle
    entirely and re-dispatching the digest on every timer wake instead of
    once a week. The field now has a real lower-bound constraint (gt=0)."""
    from pydantic import ValidationError
    from settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, WEEKLY_DIGEST_INTERVAL_HOURS=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, WEEKLY_DIGEST_INTERVAL_HOURS=-1.0)
    # A positive value is unaffected.
    s = Settings(_env_file=None, WEEKLY_DIGEST_INTERVAL_HOURS=1.0)
    assert s.WEEKLY_DIGEST_INTERVAL_HOURS == 1.0
