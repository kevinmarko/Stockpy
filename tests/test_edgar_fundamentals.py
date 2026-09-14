import math
from unittest import mock

import pytest

from data import edgar_fundamentals
from data.yahoo_fundamentals import compute_fundamentals
from settings import settings
from tests.test_yahoo_fundamentals import base_kwargs as _yahoo_base_kwargs

@pytest.fixture
def mock_tickers(monkeypatch):
    data = b'{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."}}'
    mock_get = mock.Mock(return_value=data)
    monkeypatch.setattr(edgar_fundamentals, "_http_get", mock_get)
    edgar_fundamentals._cik_cache.clear()

def test_get_cik(mock_tickers):
    assert edgar_fundamentals.get_cik("AAPL") == "0000320193"
    assert edgar_fundamentals.get_cik("MSFT") == "0000789019"
    assert edgar_fundamentals.get_cik("UNKNOWN") is None

def test_extract_latest_fact():
    us_gaap = {
        "EarningsPerShareBasic": {
            "units": {
                "USD/shares": [
                    {"val": 1.0, "filed": "2019-10-30"},
                    {"val": 1.5, "filed": "2020-01-30"},
                    {"val": 2.0, "filed": "2020-04-30"}
                ]
            }
        }
    }
    
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2019-01-01") is None
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2019-11-01") == 1.0
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2020-02-01") == 1.5
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2020-05-01") == 2.0

def test_compute_pit_ratios():
    facts = {
        "facts": {
            "us-gaap": {
                "EarningsPerShareBasic": {"units": {"USD/shares": [{"val": 5.0, "filed": "2020-01-15"}]}},
                "StockholdersEquity": {"units": {"USD": [{"val": 100000.0, "filed": "2020-01-15"}]}},
                "NetIncomeLoss": {"units": {"USD": [{"val": 15000.0, "filed": "2020-01-15"}]}},
                "Revenues": {"units": {"USD": [{"val": 50000.0, "filed": "2020-01-15"}]}},
                "OperatingIncomeLoss": {"units": {"USD": [{"val": 10000.0, "filed": "2020-01-15"}]}},
                "PaymentsOfDividends": {"units": {"USD": [{"val": 2000.0, "filed": "2020-01-15"}]}},
                "LongTermDebt": {"units": {"USD": [{"val": 50000.0, "filed": "2020-01-15"}]}},
                "AssetsCurrent": {"units": {"USD": [{"val": 30000.0, "filed": "2020-01-15"}]}},
                "LiabilitiesCurrent": {"units": {"USD": [{"val": 20000.0, "filed": "2020-01-15"}]}},
            }
        }
    }
    
    # price = 100.0, shares = 1000.0 -> market_cap = 100,000.0
    out = edgar_fundamentals.compute_pit_ratios(facts, "2020-01-15", 100.0, 1000.0)
    
    assert out["eps"] == 5.0
    assert out["pe_ratio"] == 100.0 / 5.0
    
    # book_value = 100000.0 / 1000 = 100.0
    # pb_ratio = 100.0 / 100.0 = 1.0
    assert out["pb_ratio"] == 1.0
    
    # roe = 15000.0 / 100000.0 = 0.15
    assert out["roe"] == 0.15
    
    # market_cap = 100000.0
    assert out["market_cap"] == 100000.0
    
    # dividend_yield = 2000 / 100000 = 0.02
    assert out["dividend_yield"] == 0.02
    
    # operating_margin = 10000 / 50000 = 0.2
    assert out["operating_margin"] == 0.2
    
    # debt_to_equity = (50000 / 100000) * 100 = 50.0
    assert out["debt_to_equity"] == 50.0

    # current_ratio = 30000 / 20000 = 1.5
    assert out["current_ratio"] == 1.5


def test_compute_pit_ratios_missing_debt_fact_is_nan_not_zero():
    """A company whose LongTermDebt XBRL fact simply wasn't found must report
    debt_to_equity as NaN (undefined), never a fabricated 0.0 that would read
    as "verified zero debt" (CONSTRAINT #4)."""
    facts = {
        "facts": {
            "us-gaap": {
                "StockholdersEquity": {"units": {"USD": [{"val": 100000.0, "filed": "2020-01-15"}]}},
                # No "LongTermDebt" key at all.
            }
        }
    }

    out = edgar_fundamentals.compute_pit_ratios(facts, "2020-01-15", 100.0, 1000.0)

    assert math.isnan(out["debt_to_equity"])


def test_compute_pit_ratios_missing_current_liabilities_is_nan_not_fabricated():
    """No LiabilitiesCurrent fact -> current_ratio stays NaN, never a
    fabricated 0.0 or a divide-by-zero (CONSTRAINT #4)."""
    facts = {
        "facts": {
            "us-gaap": {
                "AssetsCurrent": {"units": {"USD": [{"val": 30000.0, "filed": "2020-01-15"}]}},
            }
        }
    }
    out = edgar_fundamentals.compute_pit_ratios(facts, "2020-01-15", 100.0, 1000.0)
    assert math.isnan(out["current_ratio"])


def test_extract_shares_prefers_dei_falls_back_to_us_gaap():
    facts_dei = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 5_000_000.0, "filed": "2020-01-15"}]}
                }
            },
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 9_999.0, "filed": "2020-01-15"}]}
                }
            },
        }
    }
    # dei wins when both are present.
    assert edgar_fundamentals.extract_shares(facts_dei, "2020-01-15") == 5_000_000.0

    facts_us_gaap_only = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 9_999.0, "filed": "2020-01-15"}]}
                }
            }
        }
    }
    assert edgar_fundamentals.extract_shares(facts_us_gaap_only, "2020-01-15") == 9_999.0


def test_extract_shares_neither_present_returns_zero_not_fabricated():
    assert edgar_fundamentals.extract_shares({"facts": {}}, "2020-01-15") == 0.0


class TestScaleRuleParityWithYahooFundamentals:
    """data/edgar_fundamentals.py independently reimplements (rather than
    imports) data/yahoo_fundamentals.py's two scale-critical conventions --
    dividendYield as a FRACTION, debtToEquity x100 -- because the two
    modules' input shapes are fundamentally different (raw EDGAR XBRL facts
    vs. yfinance-shaped statement DataFrames), so literal code sharing isn't
    practical. This test pins that the two independent implementations stay
    numerically consistent: if either file's formula ever drifts from the
    other, this breaks loudly instead of silently diverging.

    Uses tests/test_yahoo_fundamentals.py's own base_kwargs() fixture
    (equity=1000, total_debt=1500, price=150, shares=100) so both sides are
    fed genuinely equivalent underlying financials.
    """

    def test_debt_to_equity_matches(self):
        yahoo_out = compute_fundamentals(**_yahoo_base_kwargs())

        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquity": {"units": {"USD": [{"val": 1000.0, "filed": "2025-12-31"}]}},
                    "LongTermDebt": {"units": {"USD": [{"val": 1500.0, "filed": "2025-12-31"}]}},
                }
            }
        }
        edgar_out = edgar_fundamentals.compute_pit_ratios(facts, "2025-12-31", price=150.0, shares=100.0)

        assert yahoo_out["debtToEquity"] == pytest.approx(150.0, abs=1e-6)
        assert edgar_out["debt_to_equity"] == pytest.approx(150.0, abs=1e-6)
        assert edgar_out["debt_to_equity"] == pytest.approx(yahoo_out["debtToEquity"], abs=1e-6)

    def test_dividend_yield_matches(self):
        """base_kwargs() pays $4.00/share/yr at price $150 -> yahoo fraction
        4/150. EDGAR reports the AGGREGATE dollar amount (100 shares *
        $4.00 = $400 total) against market_cap (150*100=15000) -- the same
        ratio via a different but mathematically equivalent path
        (total_dividends/market_cap == per_share_dividends/price)."""
        yahoo_out = compute_fundamentals(**_yahoo_base_kwargs())

        facts = {
            "facts": {
                "us-gaap": {
                    "PaymentsOfDividends": {"units": {"USD": [{"val": 400.0, "filed": "2025-12-31"}]}},
                }
            }
        }
        edgar_out = edgar_fundamentals.compute_pit_ratios(facts, "2025-12-31", price=150.0, shares=100.0)

        assert yahoo_out["dividendYield"] == pytest.approx(4.0 / 150.0, abs=1e-6)
        assert edgar_out["dividend_yield"] == pytest.approx(4.0 / 150.0, abs=1e-6)
        assert edgar_out["dividend_yield"] == pytest.approx(yahoo_out["dividendYield"], abs=1e-6)
        # Guard against the wrong (×100) scaling explicitly, matching
        # TestScaleRules.test_dividend_yield_is_a_fraction's own guard.
        assert edgar_out["dividend_yield"] < 1.0


def test_fetch_companyfacts(monkeypatch):
    mock_get = mock.Mock(return_value=b'{"facts": {"us-gaap": {}}}')
    monkeypatch.setattr(edgar_fundamentals, "_http_get", mock_get)

    res = edgar_fundamentals.fetch_companyfacts("0000320193")
    assert "facts" in res


@pytest.fixture
def reset_edgar_state():
    """Clear the module-global CIK cache and throttle clock before AND after —
    thread-safety tests must not inherit or leak cross-test state (a stale
    _last_request_time would make the throttle sleep for real)."""
    edgar_fundamentals._cik_cache.clear()
    edgar_fundamentals._last_request_time = 0.0
    yield
    edgar_fundamentals._cik_cache.clear()
    edgar_fundamentals._last_request_time = 0.0


class TestThreadSafety:
    """The backfill script now drives this module from a ThreadPoolExecutor, so
    the throttle and the lazy CIK cache must be thread-safe."""

    def test_throttle_serializes_request_issuance(self, monkeypatch, reset_edgar_state, tmp_path):
        """Under N concurrent _http_get calls, consecutive requests are still
        issued >= _REQUEST_DELAY apart. An unlocked throttle would let a burst
        through with near-zero gaps and blow SEC's ≤10 req/s limit. Verified at
        W > 10 (per the plan).

        `_throttle()` now also calls `cross_process_throttle.wait_turn` -- redirect
        its state file to an isolated `tmp_path` location so this test never
        touches the real machine-shared `LOCAL_DATA_ROOT/rate_limits/edgar.state`.

        The two locks (the pre-existing in-process `threading.Lock` and the new
        `flock`-based one) are two SEPARATE critical sections, not one atomic
        block spanning both -- a thread can be descheduled between releasing the
        first and acquiring the second, so the real syscall overhead of the
        second lock (file open/flock/read/write/close, each of which can release
        the GIL) adds a small amount of extra scheduling jitter on top of the
        original single-lock implementation. At the original 0.02s interval /
        0.8x tolerance this occasionally clipped below the floor by ~1ms under
        12-thread contention (measured, not theoretical) -- bumped to 0.04s /
        0.6x, and then to 0.15s / 0.6x after that ALSO failed in CI.

        The second failure is the interesting one, because it says the previous
        fix moved the wrong variable. The tolerance was already loose (0.6x);
        what was marginal was the ABSOLUTE floor. At 0.04s that floor is 24ms,
        and GitHub's runner has 2 vCPUs shared by two xdist workers plus these
        12 threads -- scheduler jitter there is routinely tens of milliseconds,
        so one descheduled thread clips a gap under 24ms and the build goes
        red. It passed locally every time (10 cores) and failed on the runner:
        the signature of a contention-sensitive threshold, not a real defect.

        Raising the INTERVAL rather than loosening the tolerance again is what
        actually buys margin. At 0.15s the floor is 90ms, so the same jitter is
        a small fraction of it instead of most of it, while a genuinely
        unlocked throttle still produces ~0 gaps and still fails by an order of
        magnitude. Cost is ~1.8s for this one test (12 x 0.15s serialized).

        If this goes marginal a THIRD time, do not widen the margin again --
        assert the property directly instead (median gap near the interval AND
        minimum gap far above zero). 'every gap >= a fixed fraction of the
        interval' is inherently jitter-fragile on a contended runner.
        """
        import threading
        import time

        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.15)
        monkeypatch.setattr(
            edgar_fundamentals, "_edgar_throttle_state_path_override", tmp_path / "edgar.state"
        )

        issued: list[float] = []
        issued_lock = threading.Lock()

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def _fake_urlopen(req, timeout=10):
            with issued_lock:
                issued.append(time.monotonic())
            return _FakeResp()

        monkeypatch.setattr(edgar_fundamentals.urllib.request, "urlopen", _fake_urlopen)

        n = 12
        threads = [
            threading.Thread(target=lambda: edgar_fundamentals._http_get("https://x.test/y"))
            for _ in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(issued) == n
        issued.sort()
        gaps = [b - a for a, b in zip(issued, issued[1:])]
        # 0.6x tolerance for scheduler jitter, now against a 0.15s interval --
        # so the floor is 90ms, comfortably above the tens-of-ms jitter a
        # 2-vCPU CI runner produces under this much contention. A broken
        # throttle produces ~0 gaps, still an order of magnitude below it.
        assert all(g >= 0.15 * 0.6 for g in gaps), gaps


class _NoSleepClock:
    """A stand-in for the ``time`` MODULE that delegates everything except
    ``sleep``, which becomes a no-op.

    Substituted for ONE of ``_throttle()``'s two throttle layers so that layer
    can no longer actually block, leaving the other one solely responsible for
    the spacing the test then asserts. Note this replaces the module-level
    ``time`` *reference* inside one module (``monkeypatch.setattr("<mod>.time",
    ...)``) rather than setting an attribute on the real ``time`` module --
    the latter is a single shared object, so poking ``sleep`` on it would
    neutralize BOTH layers at once and defeat the purpose. This mirrors the
    module-reference patching ``tests/test_gdelt_rate_limiter.py``'s ``clock``
    fixture already uses.
    """

    def __init__(self, base):
        self._base = base

    def __getattr__(self, name):
        return getattr(self._base, name)

    def sleep(self, seconds):
        # `seconds` is intentionally unused -- this stand-in exists precisely
        # to make the sleep a no-op.
        return None


class TestThrottleLayersIndependently:
    """``_throttle()`` has TWO spacing layers -- an in-process ``threading.Lock``
    and ``cross_process_throttle.wait_turn``'s ``flock`` -- and both enforce the
    SAME ``_REQUEST_DELAY``. That makes them fully redundant for
    ``TestThreadSafety``'s assertion above: sabotaging either one alone leaves
    that test green (verified by direct sabotage, not by reading), so either
    layer could be deleted, deadlocked, or silently made to return early with no
    test noticing.

    They are NOT interchangeable in production. The in-process lock spaces
    threads within ONE process; ``wait_turn`` spaces requests across this repo's
    many concurrent git worktrees, each of which is an independent process that
    otherwise believes it owns the whole ~10 req/s SEC budget (the reason F8 --
    docs/module_efficiency_redundancy_audit.md -- added it). Losing either one
    silently means blowing that budget in exactly one of those two dimensions.

    These two tests pin each layer on its own by neutralizing the other, so a
    regression in either is caught by a failing test rather than by an IP block.
    """

    # Eight threads rather than TestThreadSafety's twelve: layer attribution,
    # not peak contention, is what these two assert (the W > 10 case stays
    # covered by that test), and fewer threads means less scheduler jitter
    # against the same 90 ms floor and ~1.2 s instead of ~1.8 s per test.
    N_THREADS = 8

    def _run_concurrent_gets(self, monkeypatch, tmp_path):
        """Fire N concurrent ``_http_get`` calls and return the sorted gaps
        between successive request issuances."""
        import threading
        import time

        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.15)
        monkeypatch.setattr(
            edgar_fundamentals, "_edgar_throttle_state_path_override", tmp_path / "edgar.state"
        )

        issued: list[float] = []
        issued_lock = threading.Lock()

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def _fake_urlopen(req, timeout=10):
            with issued_lock:
                issued.append(time.monotonic())
            return _FakeResp()

        monkeypatch.setattr(edgar_fundamentals.urllib.request, "urlopen", _fake_urlopen)

        threads = [
            threading.Thread(target=lambda: edgar_fundamentals._http_get("https://x.test/y"))
            for _ in range(self.N_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(issued) == self.N_THREADS
        issued.sort()
        return [b - a for a, b in zip(issued, issued[1:])]

    def test_in_process_layer_alone_serializes_request_issuance(
        self, monkeypatch, reset_edgar_state, tmp_path
    ):
        """With the cross-process layer unable to block, the in-process
        ``threading.Lock`` must still space consecutive requests on its own.

        Fails if the in-process sleep is removed or the lock stops being held
        across it; unaffected by anything done to ``wait_turn``.
        """
        import time as _real_time

        from data import cross_process_throttle

        monkeypatch.setattr(cross_process_throttle, "time", _NoSleepClock(_real_time))

        gaps = self._run_concurrent_gets(monkeypatch, tmp_path)
        # Same 0.15 s interval and 0.6x tolerance as TestThreadSafety -- see
        # that test's docstring for why the margin is where it is and why
        # widening it again is the wrong fix.
        assert all(g >= 0.15 * 0.6 for g in gaps), gaps

    def test_cross_process_layer_alone_serializes_request_issuance(
        self, monkeypatch, reset_edgar_state, tmp_path
    ):
        """With the in-process layer unable to block, ``wait_turn``'s ``flock``
        must still space consecutive requests on its own.

        Fails if ``wait_turn``'s sleep is removed, if it stops holding the lock
        across that sleep, or if ``_throttle()`` simply stops calling it --
        i.e. this covers the wiring, not just the implementation. Unaffected by
        anything done to the in-process block.
        """
        import time as _real_time

        monkeypatch.setattr(edgar_fundamentals, "time", _NoSleepClock(_real_time))

        gaps = self._run_concurrent_gets(monkeypatch, tmp_path)
        assert all(g >= 0.15 * 0.6 for g in gaps), gaps


class TestCooldownCircuitBreaker:
    """New for F8 (docs/module_efficiency_redundancy_audit.md): EDGAR
    previously had the spacing throttle but no cooldown/circuit-breaker at
    all, unlike its FMP/GDELT siblings. Mirrors
    tests/test_fmp_client.py's TestCooldown class conventions."""

    def _http_error(self, code):
        import urllib.error
        return urllib.error.HTTPError(
            url="https://x.test/y", code=code, msg="err", hdrs=None, fp=None
        )

    def test_first_call_is_not_in_cooldown(self, reset_edgar_state):
        assert edgar_fundamentals._edgar_in_cooldown() is False

    def test_consecutive_429s_open_the_cooldown(self, monkeypatch, reset_edgar_state):
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_THRESHOLD", 3)
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_SECONDS", 300.0)
        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.0)

        def _fake_urlopen(req, timeout=10):
            raise self._http_error(429)

        monkeypatch.setattr(edgar_fundamentals.urllib.request, "urlopen", _fake_urlopen)

        for _ in range(3):
            with pytest.raises(Exception):
                edgar_fundamentals._http_get("https://x.test/y")

        assert edgar_fundamentals._edgar_in_cooldown() is True

    def test_cooldown_skips_the_request_entirely(self, monkeypatch, reset_edgar_state):
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_THRESHOLD", 1)
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_SECONDS", 300.0)
        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.0)

        calls = {"n": 0}

        def _fake_urlopen(req, timeout=10):
            calls["n"] += 1
            raise self._http_error(429)

        monkeypatch.setattr(edgar_fundamentals.urllib.request, "urlopen", _fake_urlopen)

        with pytest.raises(Exception):
            edgar_fundamentals._http_get("https://x.test/y")
        assert calls["n"] == 1

        # Cooldown is now open (threshold=1) -- the next call must be
        # skipped WITHOUT reaching urlopen at all.
        with pytest.raises(edgar_fundamentals.EdgarUnavailable):
            edgar_fundamentals._http_get("https://x.test/y")
        assert calls["n"] == 1, "urlopen must not be called while the cooldown is open"

    def test_a_success_clears_the_consecutive_count(self, monkeypatch, reset_edgar_state):
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_THRESHOLD", 3)
        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.0)

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        responses = [self._http_error(429), self._http_error(429), _FakeResp()]

        def _fake_urlopen(req, timeout=10):
            resp = responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        monkeypatch.setattr(edgar_fundamentals.urllib.request, "urlopen", _fake_urlopen)

        with pytest.raises(Exception):
            edgar_fundamentals._http_get("https://x.test/y")
        with pytest.raises(Exception):
            edgar_fundamentals._http_get("https://x.test/y")
        edgar_fundamentals._http_get("https://x.test/y")  # succeeds

        assert edgar_fundamentals._edgar_consecutive_failures == 0
        assert edgar_fundamentals._edgar_in_cooldown() is False

    def test_a_404_clears_the_breaker_not_treated_as_host_failure(self, monkeypatch, reset_edgar_state):
        """Mirrors data/fmp_client.py::_fmp_note_answered's documented
        reasoning: 'that ticker doesn't exist' answers the request just as
        squarely as a 200 -- it must not count toward the cooldown."""
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_THRESHOLD", 1)
        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.0)

        def _fake_urlopen(req, timeout=10):
            raise self._http_error(404)

        monkeypatch.setattr(edgar_fundamentals.urllib.request, "urlopen", _fake_urlopen)

        with pytest.raises(Exception):
            edgar_fundamentals._http_get("https://x.test/y")

        assert edgar_fundamentals._edgar_in_cooldown() is False

    def test_get_cik_degrades_gracefully_when_cooldown_open(self, monkeypatch, reset_edgar_state):
        """The two real call sites (get_cik, fetch_companyfacts) already
        wrap _http_get in a broad except -- confirm EdgarUnavailable is
        caught by that existing degrade-to-None path with no special
        casing needed."""
        monkeypatch.setattr(settings, "EDGAR_COOLDOWN_THRESHOLD", 1)
        monkeypatch.setattr(edgar_fundamentals, "_REQUEST_DELAY", 0.0)
        edgar_fundamentals._edgar_cooldown_until = edgar_fundamentals.time.monotonic() + 300.0

        assert edgar_fundamentals.get_cik("AAPL") is None

    def test_reset_edgar_rate_limiter_clears_state(self, reset_edgar_state):
        edgar_fundamentals._edgar_consecutive_failures = 5
        edgar_fundamentals._edgar_cooldown_until = edgar_fundamentals.time.monotonic() + 300.0
        edgar_fundamentals._edgar_cooldown_logged = True

        edgar_fundamentals.reset_edgar_rate_limiter()

        assert edgar_fundamentals._edgar_consecutive_failures == 0
        assert edgar_fundamentals._edgar_cooldown_until == 0.0
        assert edgar_fundamentals._edgar_cooldown_logged is False

    def test_cik_cache_fetched_once_under_concurrency(self, monkeypatch, reset_edgar_state):
        """W threads racing into get_cik with an empty cache trigger exactly ONE
        company_tickers.json fetch (the double-checked lock), not W."""
        import threading

        data = b'{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}'
        call_count = {"n": 0}
        count_lock = threading.Lock()

        def _counting_http_get(url):
            with count_lock:
                call_count["n"] += 1
            return data

        monkeypatch.setattr(edgar_fundamentals, "_http_get", _counting_http_get)

        results: list = []
        res_lock = threading.Lock()

        def worker():
            r = edgar_fundamentals.get_cik("AAPL")
            with res_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert call_count["n"] == 1
        assert all(r == "0000320193" for r in results)
