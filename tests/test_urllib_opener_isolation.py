"""A test that installs a process-wide urllib opener must not leak it to the next test.

``fredapi.Fred(...)`` installs ``ProxyHandler({'http': None, 'https': <proxy>})`` as the default
opener when only ``HTTPS_PROXY`` is set; afterwards every plain ``http://`` ``urlopen`` in that
process dies inside urllib. The root ``conftest.py`` fixture ``_restore_urllib_default_opener_
after_each_test`` confines it. These two tests are pinned to one xdist group, in definition order,
so the second runs in the same worker right after the first.
"""

from __future__ import annotations

import urllib.request

import pytest


@pytest.mark.xdist_group(name="urllib_opener_isolation")
class TestPoisonedOpenerDoesNotLeak:
    poisoned = None

    def test_a_installs_what_fredapi_installs_under_an_https_only_proxy_env(self):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": None, "https": "http://127.0.0.1:9"})
        )
        urllib.request.install_opener(opener)
        type(self).poisoned = opener
        assert urllib.request._opener is opener  # the setup really took effect

    def test_b_the_next_test_does_not_see_it(self):
        if type(self).poisoned is None:
            pytest.skip("test_a did not run in this worker first (not grouped); nothing to check")
        assert urllib.request._opener is not type(self).poisoned

    def test_c_a_plain_http_request_does_not_crash_in_urllib_proxy_parsing(self):
        """The user-visible symptom: with the poisoned opener leaked, urlopen('http://127.0.0.1:1')
        raised TypeError. A refused connection (URLError/OSError) is the healthy outcome."""
        with pytest.raises(OSError):
            urllib.request.urlopen("http://127.0.0.1:1/", timeout=2)  # nosec B310 - loopback test
