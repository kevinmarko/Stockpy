"""
tests/test_fmp_client.py
========================
Unit tests for the shared Financial Modeling Prep HTTP seam in
``data/fmp_client.py``.

Everything here is offline. ``requests.get`` is monkeypatched and the module's
``time`` is replaced with a fake clock, so these assert the client's ACTUAL
spacing/backoff/breaker arithmetic rather than waiting it out. No ``responses``,
no ``requests_mock``, no VCR — the repo's convention is
``patch("data.fmp_client.requests.get", ...)`` and ``patch("settings.settings.X", ...)``.

Two of these tests are regression guards for failures that already happened in
this codebase rather than hypotheticals:

``TestCredentialGate.test_key_from_settings_is_used_when_os_environ_is_empty``
    pydantic-settings' ``env_file=".env"`` populates the ``settings`` singleton
    but does NOT copy values into the real ``os.environ``, so an
    ``os.environ.get()`` read returns ``None`` for the normal operator whose key
    lives only in ``.env`` — silently, indistinguishably from "this source has
    nothing". ``signals/news_catalyst.py::build_finnhub_client`` got this wrong
    and produced zero Finnhub documents for six months (confirmed live
    2026-07-29). This test fails if anyone reintroduces the ``os.environ`` read.

``TestCooldownCircuitBreaker`` / ``TestDeadEndpoint``
    Module-level breaker and dead-endpoint state leaks across tests. The root
    ``conftest.py``'s ``_no_fmp_throttle_in_tests`` fixture resets it before and
    after every test; without that, the tests below would leave later tests'
    FMP calls silently short-circuited.
"""
from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from data.fmp_client import (
    FMPUnavailable,
    _fmp_get,
    batch_quote,
    get_fmp_call_stats,
    historical_eod,
    historical_eod_full_range,
    intraday,
    profile,
    quote,
    reset_fmp_rate_limiter,
    sector_pe_snapshot,
)
from settings import settings


class FakeClock:
    """Deterministic stand-in for the module's ``time``.

    ``sleep`` advances the clock instead of blocking, so a test can assert
    exactly how long the client *would* have waited without waiting.
    """

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _resp(status: int = 200, *, payload=None, retry_after=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    resp.json.return_value = payload if payload is not None else [{"symbol": "AAPL"}]
    return resp


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("data.fmp_client.time", fake)
    reset_fmp_rate_limiter()
    yield fake
    reset_fmp_rate_limiter()


@pytest.fixture
def api_key(monkeypatch):
    """A key on the SINGLETON — never via ``patch.dict(os.environ)``, which
    would test a code path this module deliberately does not have."""
    monkeypatch.setattr(settings, "FMP_API_KEY", "test-key-abc123")
    return "test-key-abc123"


@pytest.fixture
def client_settings(monkeypatch, api_key, tmp_path):
    """Explicit, non-default throttle knobs. The root conftest zeroes the
    interval for the suite at large; this module's own tests must opt back in
    or they would assert nothing.

    A nonzero interval means `_fmp_throttle` now also calls
    `cross_process_throttle.wait_turn` (see data/fmp_client.py) -- redirect its
    state file to an isolated `tmp_path` location so these tests never touch
    the real machine-shared `LOCAL_DATA_ROOT/rate_limits/fmp.state` (which a
    real concurrent process elsewhere on this machine may be reading/writing).
    """
    monkeypatch.setattr(settings, "FMP_MIN_REQUEST_INTERVAL_SECONDS", 0.25)
    monkeypatch.setattr(settings, "FMP_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "FMP_RETRY_BACKOFF_SECONDS", 2.0)
    monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 5)
    monkeypatch.setattr(settings, "FMP_COOLDOWN_SECONDS", 300.0)
    monkeypatch.setattr(
        "data.fmp_client._fmp_throttle_state_path_override",
        tmp_path / "fmp.state",
    )
    return settings


class TestCredentialGate:
    def test_missing_key_raises_without_touching_the_network(self, clock, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", None)
        with patch("data.fmp_client.requests.get") as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 0  # zero network cost, not a failed request

    def test_empty_string_key_is_treated_as_missing(self, clock, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "")
        with patch("data.fmp_client.requests.get") as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 0

    def test_key_from_settings_is_used_when_os_environ_is_empty(
        self, clock, monkeypatch
    ):
        """THE regression guard for the six-month Finnhub incident.

        ``.env`` populates the settings singleton but NOT ``os.environ``. A
        client that read ``os.environ.get("FMP_API_KEY")`` would see nothing
        here and skip every request forever, with no error and no warning.
        """
        monkeypatch.delenv("FMP_API_KEY", raising=False)
        assert os.environ.get("FMP_API_KEY") is None  # the operator's real case
        monkeypatch.setattr(settings, "FMP_API_KEY", "only-in-dot-env")

        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            _fmp_get("quote", {"symbol": "AAPL"})

        assert get.call_count == 1
        assert get.call_args.kwargs["params"]["apikey"] == "only-in-dot-env"

    def test_caller_params_dict_is_not_mutated_with_the_key(self, clock, api_key):
        """The caller's dict may be reused across symbols, and this runs under
        an 8-thread pool — leaking the credential into it would be both a
        correctness and a hygiene bug."""
        params = {"symbol": "AAPL"}
        with patch("data.fmp_client.requests.get", return_value=_resp()):
            _fmp_get("quote", params)
        assert params == {"symbol": "AAPL"}


class TestUrlConstruction:
    def test_base_url_and_path_are_joined_without_a_double_slash(self, clock, api_key, monkeypatch):
        monkeypatch.setattr(settings, "FMP_BASE_URL", "https://example.test/stable/")
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            _fmp_get("/quote", {"symbol": "AAPL"})
        assert get.call_args.args[0] == "https://example.test/stable/quote"

    def test_timeout_is_passed_through_from_settings(self, clock, api_key, monkeypatch):
        monkeypatch.setattr(settings, "FMP_TIMEOUT_SECONDS", 3.5)
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_args.kwargs["timeout"] == 3.5


class TestThrottleSpacing:
    def test_consecutive_requests_are_spaced_by_the_minimum_interval(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            _fmp_get("quote", {"symbol": "AAPL"})
            _fmp_get("quote", {"symbol": "MSFT"})
            _fmp_get("quote", {"symbol": "NVDA"})

        assert get.call_count == 3
        # The first request is issued immediately (the clock starts far past
        # the zero-initialised last-request time); each subsequent one waits
        # the full interval because the fake transport returns instantly.
        assert clock.sleeps == [0.25, 0.25]

    def test_elapsed_time_is_credited_against_the_interval(self, clock, client_settings):
        """A slow request must not then also sleep the full interval — the
        spacing is between issuance times, not an unconditional delay."""
        def slow_get(*_args, **_kwargs):
            clock.now += 0.20  # the request itself took 200 ms
            return _resp()

        with patch("data.fmp_client.requests.get", side_effect=slow_get):
            _fmp_get("quote", {"symbol": "AAPL"})
            _fmp_get("quote", {"symbol": "MSFT"})

        assert clock.sleeps == [pytest.approx(0.05)]  # 0.25 interval - 0.20 elapsed

    def test_the_budget_is_shared_across_different_endpoints(self, clock, client_settings):
        """The FMP rate limit is per-ACCOUNT. Two different endpoints must
        share one spacing clock, or six consumers would blow the budget by
        construction — the whole reason this is one module."""
        with patch("data.fmp_client.requests.get", return_value=_resp()):
            _fmp_get("quote", {"symbol": "AAPL"})
            _fmp_get("ratios-ttm", {"symbol": "AAPL"})
        assert clock.sleeps == [0.25]


class TestUnthrottledEquivalence:
    def test_zero_interval_zero_retries_zero_threshold_reproduce_raw_behaviour(
        self, clock, api_key, monkeypatch
    ):
        monkeypatch.setattr(settings, "FMP_MIN_REQUEST_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(settings, "FMP_MAX_RETRIES", 0)
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 0)
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            _fmp_get("quote", {"symbol": "AAPL"})
            _fmp_get("quote", {"symbol": "MSFT"})
        assert get.call_count == 2   # exactly one request each
        assert clock.sleeps == []    # and no waiting at all


class TestRetryAndBackoff:
    def test_429_is_retried_with_exponential_backoff_then_raises(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get", return_value=_resp(429)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})

        # 1 initial attempt + FMP_MAX_RETRIES(2) retries.
        assert get.call_count == 3
        # 2 s backoff (2 * 2**0) already exceeds the 0.25 s issuance spacing, so
        # the throttle adds nothing on top — double-waiting would be the bug.
        # Then 4 s (2 * 2**1); the third attempt is the last, so it gives up.
        assert clock.sleeps == [2.0, 4.0]

    def test_a_retry_that_succeeds_returns_the_payload(self, clock, client_settings):
        ok = _resp(200, payload=[{"symbol": "AAPL", "price": 1.0}])
        with patch(
            "data.fmp_client.requests.get", side_effect=[_resp(429), ok]
        ) as get:
            assert _fmp_get("quote", {"symbol": "AAPL"}) == [
                {"symbol": "AAPL", "price": 1.0}
            ]
        assert get.call_count == 2

    def test_retry_after_header_takes_precedence_over_computed_backoff(
        self, clock, client_settings
    ):
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(429, retry_after="30"), _resp(200)],
        ):
            _fmp_get("quote", {"symbol": "AAPL"})
        assert 30.0 in clock.sleeps  # the server's number, not our 2 s
        assert 2.0 not in clock.sleeps

    def test_unparseable_retry_after_falls_back_to_computed_backoff(
        self, clock, client_settings
    ):
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"), _resp(200)],
        ):
            _fmp_get("quote", {"symbol": "AAPL"})
        assert 2.0 in clock.sleeps

    def test_5xx_is_retried_like_a_429(self, clock, client_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(503)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 3

    def test_404_is_not_retried(self, clock, client_settings):
        """A bad symbol is not an overloaded host. Retrying it spends budget
        that the NEXT symbol — which may be perfectly fine — then does not have."""
        with patch("data.fmp_client.requests.get", return_value=_resp(404)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "NOTATICKER"})
        assert get.call_count == 1

    def test_404_does_not_advance_the_breaker(self, clock, client_settings, monkeypatch):
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 2)
        with patch("data.fmp_client.requests.get", return_value=_resp(404)) as get:
            for _ in range(4):
                with pytest.raises(FMPUnavailable):
                    _fmp_get("quote", {"symbol": "NOTATICKER"})
        assert get.call_count == 4  # every one actually issued, nothing skipped

    def test_transport_error_is_not_retried(self, clock, client_settings):
        """An immediate retry of a read timeout just times out again at full
        cost — measured on GDELT (2026-07-29) as 26 windows x 10 s for zero
        documents, which is the same arithmetic here."""
        with patch(
            "data.fmp_client.requests.get", side_effect=RuntimeError("read timed out")
        ) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 1

    def test_transport_error_is_wrapped_not_leaked(self, clock, client_settings):
        """CONSTRAINT #6: the data layer never raises a raw transport exception
        into the pipeline — callers catch exactly one type."""
        with patch(
            "data.fmp_client.requests.get", side_effect=RuntimeError("connection reset")
        ):
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})


class TestCooldownCircuitBreaker:
    def test_transport_errors_do_advance_the_breaker(self, clock, client_settings, monkeypatch):
        """From the caller's side "the host is refusing us" and "the host is
        not answering us" have identical cost and identical remedy."""
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 3)
        with patch(
            "data.fmp_client.requests.get", side_effect=RuntimeError("read timed out")
        ) as get:
            for _ in range(3):
                with pytest.raises(FMPUnavailable):
                    _fmp_get("quote", {"symbol": "AAPL"})
            assert get.call_count == 3

        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "MSFT"})
        assert get.call_count == 0  # skipped, not issued

    def test_one_transport_error_does_not_open_the_cooldown(self, clock, client_settings):
        """A single flaky socket must not blind the platform to FMP for five
        minutes — the breaker needs CONSECUTIVE failures, not one."""
        with patch(
            "data.fmp_client.requests.get", side_effect=RuntimeError("connection reset")
        ):
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})

        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            _fmp_get("quote", {"symbol": "MSFT"})
        assert get.call_count == 1  # not skipped — one failure is not a trend

    def test_cooldown_opens_after_the_threshold_and_skips_without_requesting(
        self, clock, client_settings, monkeypatch
    ):
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 3)
        with patch("data.fmp_client.requests.get", return_value=_resp(429)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
            calls_during_first = get.call_count
            # Threshold is 3 and the first call already produced 3 rate-limited
            # responses (its retries included), so the cooldown is now open:
            # this must not touch the network at all.
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "MSFT"})
            assert get.call_count == calls_during_first

    def test_cooldown_expires_and_requests_resume(self, clock, client_settings, monkeypatch):
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 3)
        with patch("data.fmp_client.requests.get", return_value=_resp(429)):
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})

        clock.now += 301.0  # past FMP_COOLDOWN_SECONDS
        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            _fmp_get("quote", {"symbol": "MSFT"})
        assert get.call_count == 1

    def test_one_success_clears_the_consecutive_count(self, clock, client_settings, monkeypatch):
        """Two isolated 429s separated by a success must not accumulate toward
        the threshold — the breaker is for a sustained block, not noise."""
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 3)
        with patch(
            "data.fmp_client.requests.get",
            side_effect=[_resp(429), _resp(200), _resp(429), _resp(200)],
        ) as get:
            _fmp_get("quote", {"symbol": "AAPL"})
            _fmp_get("quote", {"symbol": "MSFT"})
        assert get.call_count == 4  # nothing was skipped

    def test_zero_threshold_disables_the_cooldown(self, clock, client_settings, monkeypatch):
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 0)
        with patch("data.fmp_client.requests.get", return_value=_resp(429)) as get:
            for _ in range(3):
                with pytest.raises(FMPUnavailable):
                    _fmp_get("quote", {"symbol": "AAPL"})
        # 3 calls x (1 attempt + 2 retries) — every one actually issued.
        assert get.call_count == 9

    def test_reset_clears_an_open_cooldown(self, clock, client_settings, monkeypatch):
        """Exactly what the root conftest fixture relies on: without this, a
        breaker-exercising test would silently turn every LATER test's FMP
        calls into zero-network skips."""
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 3)
        with patch("data.fmp_client.requests.get", return_value=_resp(429)):
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})

        reset_fmp_rate_limiter()

        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            _fmp_get("quote", {"symbol": "MSFT"})
        assert get.call_count == 1


class TestAuthRejection:
    def test_401_raises_and_is_not_retried(self, clock, client_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(401)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 1

    def test_401_does_not_advance_the_breaker(self, clock, client_settings, monkeypatch):
        """A rejected key is not evidence the host is unhealthy, and a cooldown
        cannot fix it — conflating the two would hide the real remedy."""
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 2)
        with patch("data.fmp_client.requests.get", return_value=_resp(401)) as get:
            for _ in range(4):
                with pytest.raises(FMPUnavailable):
                    _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 4  # every one issued; nothing was skipped

    def test_401_logs_an_error_once_per_process(self, clock, client_settings, caplog):
        caplog.set_level(logging.ERROR, logger="data.fmp_client")
        with patch("data.fmp_client.requests.get", return_value=_resp(401)):
            for _ in range(5):
                with pytest.raises(FMPUnavailable):
                    _fmp_get("quote", {"symbol": "AAPL"})

        rejected = [r for r in caplog.records if "FMP_API_KEY rejected" in r.message]
        assert len(rejected) == 1
        assert rejected[0].levelno == logging.ERROR


class TestDeadEndpoint:
    def test_403_marks_only_that_endpoint_dead_and_skips_the_next_call(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get", return_value=_resp(403)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("institutional-ownership", {"symbol": "AAPL"})
            assert get.call_count == 1

        # Second call to the SAME path: zero network. A plan entitlement does
        # not change mid-run, so re-asking is a guaranteed-failing request.
        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("institutional-ownership", {"symbol": "MSFT"})
        assert get.call_count == 0

    def test_a_dead_endpoint_does_not_disable_the_others(self, clock, client_settings):
        """Starter serves /quote perfectly well while refusing Form 13F. One
        refusal must not take the working feeds down with it."""
        with patch("data.fmp_client.requests.get", return_value=_resp(403)):
            with pytest.raises(FMPUnavailable):
                _fmp_get("institutional-ownership", {"symbol": "AAPL"})

        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 1

    def test_403_does_not_advance_the_breaker(self, clock, client_settings, monkeypatch):
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 2)
        with patch("data.fmp_client.requests.get", return_value=_resp(403)):
            with pytest.raises(FMPUnavailable):
                _fmp_get("etf/holdings", {"symbol": "SPY"})
            with pytest.raises(FMPUnavailable):
                _fmp_get("form-13f", {"symbol": "SPY"})

        # If those two 403s had advanced the breaker (threshold 2), this would
        # be skipped with zero network instead of issued.
        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            _fmp_get("quote", {"symbol": "AAPL"})
        assert get.call_count == 1

    def test_403_logs_an_error_once_per_endpoint(self, clock, client_settings, caplog):
        caplog.set_level(logging.ERROR, logger="data.fmp_client")
        with patch("data.fmp_client.requests.get", return_value=_resp(403)):
            for _ in range(3):
                with pytest.raises(FMPUnavailable):
                    _fmp_get("form-13f", {"symbol": "AAPL"})

        dead = [r for r in caplog.records if "form-13f" in r.message]
        assert len(dead) == 1
        assert dead[0].levelno == logging.ERROR

    def test_a_200_body_with_an_entitlement_error_is_treated_as_a_403(
        self, clock, client_settings
    ):
        """FMP answers an out-of-plan endpoint with 200 + an error BODY at
        least as often as with a 403. A status-only check would hand the caller
        a dict that looks like data."""
        denied = _resp(
            200,
            payload={
                "Error Message": (
                    "Exclusive Endpoint: This endpoint is not available under "
                    "your current subscription. Please upgrade your plan."
                )
            },
        )
        with patch("data.fmp_client.requests.get", return_value=denied) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("institutional-ownership", {"symbol": "AAPL"})
            assert get.call_count == 1

        with patch("data.fmp_client.requests.get", return_value=_resp(200)) as get:
            with pytest.raises(FMPUnavailable):
                _fmp_get("institutional-ownership", {"symbol": "MSFT"})
        assert get.call_count == 0

    def test_a_bare_access_denied_string_body_is_recognised(self, clock, client_settings):
        denied = _resp(200, payload="ACCESS DENIED. Please upgrade your plan.")
        with patch("data.fmp_client.requests.get", return_value=denied):
            with pytest.raises(FMPUnavailable):
                _fmp_get("form-13f", {"symbol": "AAPL"})

    def test_a_real_payload_is_not_mistaken_for_an_entitlement_error(
        self, clock, client_settings
    ):
        """Negative guard on the marker scan: a genuine record that happens to
        contain prose must not latch its endpoint dead for the whole process."""
        real = _resp(
            200,
            payload=[
                {
                    "symbol": "AAPL",
                    "description": "Apple Inc. designs premium endpoint devices.",
                }
            ],
        )
        with patch("data.fmp_client.requests.get", return_value=real) as get:
            assert _fmp_get("profile", {"symbol": "AAPL"})[0]["symbol"] == "AAPL"
            _fmp_get("profile", {"symbol": "MSFT"})
        assert get.call_count == 2  # endpoint never latched


class TestEmptyPayloads:
    def test_an_empty_list_is_returned_not_raised(self, clock, client_settings):
        """"This symbol has no dividends" and "this endpoint is broken" are
        different facts; only the caller knows which one matters for its column."""
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=[])):
            assert _fmp_get("dividends", {"symbol": "AAPL"}) == []

    def test_an_empty_dict_is_returned_not_raised(self, clock, client_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload={})):
            assert _fmp_get("key-metrics-ttm", {"symbol": "AAPL"}) == {}

    def test_an_empty_payload_does_not_advance_the_breaker(
        self, clock, client_settings, monkeypatch
    ):
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 2)
        with patch(
            "data.fmp_client.requests.get", return_value=_resp(200, payload=[])
        ) as get:
            for _ in range(4):
                _fmp_get("dividends", {"symbol": "AAPL"})
        assert get.call_count == 4

    def test_an_unparseable_body_raises_rather_than_returning_garbage(
        self, clock, client_settings
    ):
        resp = _resp(200)
        resp.json.side_effect = ValueError("Expecting value: line 1 column 1")
        with patch("data.fmp_client.requests.get", return_value=resp):
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})


class TestCallStats:
    def test_calls_successes_and_failures_are_counted_per_endpoint(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get", return_value=_resp(200)):
            _fmp_get("quote", {"symbol": "AAPL"})
            _fmp_get("quote", {"symbol": "MSFT"})
        with patch("data.fmp_client.requests.get", return_value=_resp(404)):
            with pytest.raises(FMPUnavailable):
                _fmp_get("profile", {"symbol": "NOTATICKER"})

        stats = get_fmp_call_stats()
        assert stats["quote"]["calls"] == 2
        assert stats["quote"]["successes"] == 2
        assert stats["quote"]["failures"] == 0
        assert stats["profile"]["failures"] == 1
        assert stats["profile"]["successes"] == 0

    def test_zero_network_short_circuits_count_as_skipped_not_calls(
        self, clock, client_settings, monkeypatch
    ):
        """A high `skipped` with a low `calls` is the breaker doing its job.
        Collapsing the two would make an outage look like a quiet cycle."""
        monkeypatch.setattr(settings, "FMP_COOLDOWN_THRESHOLD", 3)
        with patch("data.fmp_client.requests.get", return_value=_resp(429)):
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "AAPL"})
            with pytest.raises(FMPUnavailable):
                _fmp_get("quote", {"symbol": "MSFT"})  # cooldown open

        stats = get_fmp_call_stats()
        assert stats["quote"]["calls"] == 3      # 1 attempt + 2 retries
        assert stats["quote"]["skipped"] == 1    # the second call never issued

    def test_reset_clears_the_counters(self, clock, client_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(200)):
            _fmp_get("quote", {"symbol": "AAPL"})
        assert get_fmp_call_stats()["quote"]["calls"] == 1
        reset_fmp_rate_limiter()
        assert get_fmp_call_stats() == {}

    def test_the_snapshot_is_a_copy_not_the_live_dict(self, clock, client_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp(200)):
            _fmp_get("quote", {"symbol": "AAPL"})
        snapshot = get_fmp_call_stats()
        snapshot["quote"]["calls"] = 999
        assert get_fmp_call_stats()["quote"]["calls"] == 1


class TestEndpointWrappers:
    def test_quote_hits_the_verified_path_with_an_upper_cased_symbol(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            quote("aapl")
        assert get.call_args.args[0].endswith("/quote")
        assert get.call_args.kwargs["params"]["symbol"] == "AAPL"

    def test_batch_quote_sends_one_comma_joined_symbols_param(self, clock, client_settings):
        """One request for the whole universe instead of 33 — the single
        largest rate-limit saving available."""
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            batch_quote(["aapl", "MSFT", " nvda ", ""])
        assert get.call_count == 1
        assert get.call_args.args[0].endswith("/batch-quote")
        assert get.call_args.kwargs["params"]["symbols"] == "AAPL,MSFT,NVDA"

    def test_historical_eod_builds_the_variant_path_and_date_range(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            historical_eod(
                "AAPL",
                variant="dividend-adjusted",
                from_date="2024-01-01",
                to_date="2024-12-31",
            )
        assert get.call_args.args[0].endswith(
            "/historical-price-eod/dividend-adjusted"
        )
        assert get.call_args.kwargs["params"]["from"] == "2024-01-01"
        assert get.call_args.kwargs["params"]["to"] == "2024-12-31"

    def test_an_unknown_eod_variant_raises_before_any_request(self, clock, client_settings):
        """A typo'd variant would otherwise build a path that 404s, which the
        caller would read as "this symbol has no history"."""
        with patch("data.fmp_client.requests.get") as get:
            with pytest.raises(ValueError):
                historical_eod("AAPL", variant="adjusted")
        assert get.call_count == 0

    def test_an_unknown_intraday_interval_raises_before_any_request(
        self, clock, client_settings
    ):
        with patch("data.fmp_client.requests.get") as get:
            with pytest.raises(ValueError):
                intraday("AAPL", "3hour")
        assert get.call_count == 0

    def test_intraday_builds_the_verified_hourly_path(self, clock, client_settings):
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            intraday("AAPL", "1hour")
        assert get.call_args.args[0].endswith("/historical-chart/1hour")

    def test_sector_snapshot_always_sends_the_date(self, clock, client_settings):
        """The one new FMP feed with a real point-in-time story — an undated
        call would throw that away."""
        with patch("data.fmp_client.requests.get", return_value=_resp()) as get:
            sector_pe_snapshot("2026-07-31")
        assert get.call_args.kwargs["params"]["date"] == "2026-07-31"

    def test_wrappers_return_the_raw_parsed_json_unmapped(self, clock, client_settings):
        """The client does no key mapping and no unit conversion — every scale
        decision lives in a pure, I/O-free consumer module so it can be tested
        without a single mock."""
        raw = [{"symbol": "AAPL", "debtToEquityRatioTTM": 1.5, "beta": 1.2}]
        with patch("data.fmp_client.requests.get", return_value=_resp(200, payload=raw)):
            assert profile("AAPL") == raw


def _bars(dates: list, **extra) -> list:
    """A minimal ``historical_eod``-shaped payload — only ``date`` matters
    for these tests unless ``extra`` is used to make rows distinguishable
    (e.g. for the dedup/first-seen-wins test)."""
    return [{"symbol": "SPY", "date": d, **extra} for d in dates]


class TestHistoricalEodFullRange:
    """``historical_eod_full_range`` — pagination past FMP's undocumented,
    silent ~5,000-row-per-request cap on ``/historical-price-eod/{variant}``.

    Mocks at the ``historical_eod`` boundary (module-level function patch),
    not ``requests.get`` — this is testing the pagination/merge algorithm,
    not the HTTP layer, which ``TestEndpointWrappers`` above already covers.
    Mirrors ``tests/test_fmp_news.py::TestFMPNewsSource``'s pagination-test
    convention. None of these need the ``clock``/``client_settings``
    fixtures since ``historical_eod`` itself never runs.
    """

    def test_single_request_suffices_when_first_page_reaches_from_date(self):
        payload = _bars(["2010-01-01", "2010-01-02", "2010-01-03"])
        with patch("data.fmp_client.historical_eod", return_value=payload) as mock:
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2010-01-01", to_date="2010-01-03",
            )
        assert mock.call_count == 1
        assert [r["date"] for r in result] == ["2010-01-01", "2010-01-02", "2010-01-03"]

    def test_truncation_triggers_one_follow_up_call_with_corrected_to_date(self):
        first_page = _bars(["2006-10-05", "2006-10-06"])
        follow_up = _bars(["2005-01-01", "2005-01-02"])
        with patch(
            "data.fmp_client.historical_eod", side_effect=[first_page, follow_up]
        ) as mock:
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2005-01-01", to_date="2026-08-21",
            )
        assert mock.call_count == 2
        second_call_kwargs = mock.call_args_list[1].kwargs
        assert second_call_kwargs["from_date"] == "2005-01-01"
        assert second_call_kwargs["to_date"] == "2006-10-04"  # earliest - 1 day
        assert [r["date"] for r in result] == [
            "2005-01-01", "2005-01-02", "2006-10-05", "2006-10-06",
        ]

    def test_multiple_rounds_of_truncation_keep_paging_until_from_date_is_reached(self):
        page1 = _bars(["2015-01-01", "2015-01-02"])
        page2 = _bars(["2010-01-01", "2010-01-02"])
        page3 = _bars(["2000-01-01", "2000-01-02"])
        with patch(
            "data.fmp_client.historical_eod", side_effect=[page1, page2, page3]
        ) as mock:
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2000-01-01", to_date="2020-01-01",
            )
        assert mock.call_count == 3
        # Each follow-up's `to_date` derives from the PRIOR round's new
        # earliest date, not the original request's `to_date`.
        assert mock.call_args_list[1].kwargs["to_date"] == "2014-12-31"
        assert mock.call_args_list[2].kwargs["to_date"] == "2009-12-31"
        assert [r["date"] for r in result] == [
            "2000-01-01", "2000-01-02", "2010-01-01", "2010-01-02",
            "2015-01-01", "2015-01-02",
        ]

    def test_exhausted_history_stops_cleanly_on_empty_follow_up(self, caplog):
        """A follow-up bounded before the symbol's real IPO returns nothing —
        the loop must stop cleanly (not raise) and disclose via WARNING that
        the result is short of the requested ``from_date``."""
        page1 = _bars(["2015-01-01"])
        with patch(
            "data.fmp_client.historical_eod", side_effect=[page1, []]
        ) as mock, caplog.at_level(logging.WARNING, logger="data.fmp_client"):
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="1990-01-01", to_date="2020-01-01",
            )
        assert mock.call_count == 2
        assert [r["date"] for r in result] == ["2015-01-01"]
        assert any("exhausted history" in rec.message for rec in caplog.records)

    def test_no_new_earlier_dates_in_follow_up_stops_the_loop(self, caplog):
        """An anomalous follow-up that returns rows but none earlier than
        what's already collected must not spin forever — one more call, then
        stop, with a WARNING distinguishing this from clean completion."""
        page1 = _bars(["2015-01-01"])
        follow_up_no_progress = _bars(["2015-01-01"])  # same date, no progress
        with patch(
            "data.fmp_client.historical_eod",
            side_effect=[page1, follow_up_no_progress],
        ) as mock, caplog.at_level(logging.WARNING, logger="data.fmp_client"):
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="1990-01-01", to_date="2020-01-01",
            )
        assert mock.call_count == 2
        assert len(result) == 1  # deduped, not doubled
        assert any("no new earlier dates" in rec.message for rec in caplog.records)

    def test_max_requests_safety_stop_logs_warning_and_returns_partial(self, caplog):
        """A pathological response that keeps making SOME progress every
        round, but never reaches ``from_date``, must still be bounded by
        ``max_requests`` — never an unbounded call budget."""
        pages = [
            _bars(["2010-01-05"]),
            _bars(["2010-01-04"]),
            _bars(["2010-01-03"]),
            _bars(["2010-01-02"]),  # would be requested if the cap didn't hold
        ]
        with patch(
            "data.fmp_client.historical_eod", side_effect=pages
        ) as mock, caplog.at_level(logging.WARNING, logger="data.fmp_client"):
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="1900-01-01", to_date="2020-01-01",
                max_requests=3,
            )
        assert mock.call_count == 3  # never 4 — the 4th page is never consumed
        assert [r["date"] for r in result] == ["2010-01-03", "2010-01-04", "2010-01-05"]
        assert any("max_requests" in rec.message for rec in caplog.records)

    def test_dedup_correctness_on_overlapping_dates_between_pages(self):
        """Overlapping dates between the two pages must collapse to one row
        each, and the FIRST-seen row (from the earlier/original call) wins on
        conflict — not silently overwritten by the follow-up's version."""
        page1 = _bars(["2006-01-01", "2006-01-02"], source="page1")
        page2 = _bars(["2005-01-01", "2005-12-31", "2006-01-01"], source="page2")
        with patch(
            "data.fmp_client.historical_eod", side_effect=[page1, page2]
        ) as mock:
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2005-01-01", to_date="2006-01-02",
            )
        assert mock.call_count == 2
        dates = [r["date"] for r in result]
        assert dates == ["2005-01-01", "2005-12-31", "2006-01-01", "2006-01-02"]
        overlapping_row = next(r for r in result if r["date"] == "2006-01-01")
        assert overlapping_row["source"] == "page1"  # first-seen wins

    def test_first_call_empty_returns_empty_no_follow_up(self):
        with patch("data.fmp_client.historical_eod", return_value=[]) as mock:
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2010-01-01", to_date="2010-01-05",
            )
        assert result == []
        assert mock.call_count == 1

    def test_first_call_exception_propagates_unchanged(self):
        with patch(
            "data.fmp_client.historical_eod", side_effect=RuntimeError("boom")
        ) as mock, pytest.raises(RuntimeError, match="boom"):
            historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2010-01-01", to_date="2010-01-05",
            )
        assert mock.call_count == 1

    def test_follow_up_exception_returns_partial_result_with_warning(self, caplog):
        page1 = _bars(["2010-01-01"])
        with patch(
            "data.fmp_client.historical_eod",
            side_effect=[page1, RuntimeError("network blip")],
        ) as mock, caplog.at_level(logging.WARNING, logger="data.fmp_client"):
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2000-01-01", to_date="2020-01-01",
            )
        assert mock.call_count == 2
        assert [r["date"] for r in result] == ["2010-01-01"]
        assert any("follow-up request failed" in rec.message for rec in caplog.records)

    def test_result_is_sorted_ascending_by_date_regardless_of_input_order(self):
        scrambled = _bars(["2010-01-03", "2010-01-01", "2010-01-02"])
        with patch("data.fmp_client.historical_eod", return_value=scrambled):
            result = historical_eod_full_range(
                "SPY", variant="dividend-adjusted",
                from_date="2010-01-01", to_date="2010-01-03",
            )
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)
        assert dates == ["2010-01-01", "2010-01-02", "2010-01-03"]
