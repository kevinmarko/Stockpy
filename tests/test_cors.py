"""
tests/test_cors.py
===================
Tests for the shared ``api/cors.py`` LAN/Tailscale origin-matching helper,
used by every standalone service in ``api/*.py``.

Exercises ``LAN_TAILSCALE_ORIGIN_REGEX`` directly with ``re.fullmatch``
(exactly how ``starlette.middleware.cors.CORSMiddleware.is_allowed_origin``
applies it) rather than any one real service, so this file is the single
place proving the matching behavior itself -- every real service's own test
file (test_state_api.py, test_pilots_api.py, ...) only proves it wired the
regex into its CORSMiddleware correctly, via one representative LAN origin,
one Tailscale-range origin, and one rejection each.
"""

from __future__ import annotations

import re

import pytest

from api.cors import LAN_TAILSCALE_ORIGIN_REGEX

_PATTERN = re.compile(LAN_TAILSCALE_ORIGIN_REGEX)


class TestLanRanges:
    @pytest.mark.parametrize(
        "origin",
        [
            "http://192.168.0.1:5173",
            "http://192.168.1.42:5173",
            "http://192.168.255.255:5173",
            "http://10.0.0.1:5173",
            "http://10.42.7.13:5173",
            "http://10.255.255.255:5173",
            "http://172.16.0.1:5173",
            "http://172.20.5.5:5173",
            "http://172.31.255.254:5173",
        ],
    )
    def test_private_ranges_match(self, origin: str) -> None:
        assert _PATTERN.fullmatch(origin)

    @pytest.mark.parametrize(
        "origin",
        [
            "http://172.15.255.255:5173",  # just below the 172.16.0.0/12 block
            "http://172.32.0.0:5173",  # just above it
            "http://11.0.0.1:5173",  # outside 10.0.0.0/8 (different /8)
        ],
    )
    def test_addresses_just_outside_private_ranges_do_not_match(self, origin: str) -> None:
        assert not _PATTERN.fullmatch(origin)


class TestTailscaleRange:
    @pytest.mark.parametrize(
        "origin",
        [
            "http://100.64.0.0:5173",  # start of 100.64.0.0/10
            "http://100.100.100.100:5173",
            "http://100.127.255.255:5173",  # end of 100.64.0.0/10
        ],
    )
    def test_cgnat_range_matches(self, origin: str) -> None:
        assert _PATTERN.fullmatch(origin)

    @pytest.mark.parametrize(
        "origin",
        [
            "http://100.63.255.255:5173",  # just below 100.64.0.0/10
            "http://100.128.0.0:5173",  # just above it
        ],
    )
    def test_addresses_just_outside_cgnat_range_do_not_match(self, origin: str) -> None:
        assert not _PATTERN.fullmatch(origin)


class TestScopeInvariants:
    def test_wrong_port_does_not_match(self) -> None:
        assert not _PATTERN.fullmatch("http://192.168.1.42:5174")

    def test_no_port_does_not_match(self) -> None:
        assert not _PATTERN.fullmatch("http://192.168.1.42")

    def test_https_does_not_match(self) -> None:
        # The dev server itself is http-only; matching https here would be a
        # false sense of coverage, not a real capability.
        assert not _PATTERN.fullmatch("https://192.168.1.42:5173")

    def test_public_ip_does_not_match(self) -> None:
        assert not _PATTERN.fullmatch("http://8.8.8.8:5173")

    def test_arbitrary_hostname_does_not_match(self) -> None:
        assert not _PATTERN.fullmatch("http://evil.example:5173")

    def test_trailing_path_does_not_match(self) -> None:
        # fullmatch (what CORSMiddleware actually uses) must reject a suffix
        # -- an Origin header is always scheme://host[:port] with no path,
        # but this pins that the regex doesn't accidentally allow one.
        assert not _PATTERN.fullmatch("http://192.168.1.42:5173/anything")
