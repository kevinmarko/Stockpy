"""
tests/test_app_shell.py — orchestration tests for app_shell.py
===============================================================
app_shell.py depends on three sibling modules built in parallel by other
workstreams (WS1/WS2/WS3): desktop.net_util, desktop.ui_server, and
desktop.engine_supervisor. Those modules may not exist yet in this worktree
at test-collection time, so every test in this file installs fake modules
into sys.modules BEFORE importing app_shell, then patches the specific
functions under test via unittest.mock.patch. This exercises app_shell's
OWN orchestration logic (call order, error handling, teardown-on-exception)
completely independently of whether the real desktop/ package has landed.

The `webview` module (pywebview) is mocked the same way — these tests never
require pywebview to be installed.
"""

from __future__ import annotations

import signal
import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch


def _install_fake_desktop_modules() -> None:
    """Install minimal fake `desktop.net_util`, `desktop.ui_server`, and
    `desktop.engine_supervisor` modules into sys.modules so `app_shell`'s
    `from desktop.xxx import yyy` statements resolve without the real
    desktop/ package being present. Each fake module exposes MagicMock
    callables matching the frozen signatures app_shell.py imports.
    """
    desktop_pkg = types.ModuleType("desktop")
    desktop_pkg.__path__ = []  # mark as a package

    net_util = types.ModuleType("desktop.net_util")
    net_util.find_free_port = MagicMock(name="find_free_port", return_value=54321)
    net_util.wait_for_http = MagicMock(name="wait_for_http", return_value=True)

    ui_server = types.ModuleType("desktop.ui_server")
    ui_server.start_ui_server = MagicMock(name="start_ui_server")
    ui_server.stop_ui_server = MagicMock(name="stop_ui_server", return_value=True)

    engine_supervisor = types.ModuleType("desktop.engine_supervisor")
    engine_supervisor.start_engine = MagicMock(name="start_engine")
    engine_supervisor.stop_engine = MagicMock(name="stop_engine", return_value=True)

    sys.modules["desktop"] = desktop_pkg
    sys.modules["desktop.net_util"] = net_util
    sys.modules["desktop.ui_server"] = ui_server
    sys.modules["desktop.engine_supervisor"] = engine_supervisor


def _install_fake_webview_module() -> types.ModuleType:
    """Install a fake `webview` module into sys.modules and return it so
    tests can assert on create_window/start calls or configure side effects.
    """
    webview = types.ModuleType("webview")
    webview.create_window = MagicMock(name="create_window")
    webview.start = MagicMock(name="start")
    sys.modules["webview"] = webview
    return webview


def _purge_app_shell_related_modules() -> None:
    """Remove app_shell + fake desktop/webview modules from sys.modules so
    each test gets a clean import (patched mocks don't bleed across tests).
    """
    for name in list(sys.modules):
        if name == "app_shell" or name.startswith("desktop") or name == "webview":
            del sys.modules[name]


class BaseAppShellTest(unittest.TestCase):
    """Common setup: install fake desktop.* + webview modules, import
    app_shell fresh, and clean up sys.modules afterward so tests don't
    interfere with each other or with any real desktop/ package that might
    later exist on disk.
    """

    def setUp(self):
        _purge_app_shell_related_modules()
        _install_fake_desktop_modules()
        self.fake_webview = _install_fake_webview_module()

        # Import app_shell fresh, with the fake desktop.* modules already
        # registered in sys.modules so its top-level `from dotenv import ...`
        # succeeds normally and its deferred `from desktop.xxx import yyy`
        # (inside main()) resolves against our fakes.
        import app_shell  # noqa: PLC0415
        self.app_shell = app_shell

        # Prevent real .env loading from mutating the test process env.
        self._load_dotenv_patcher = patch.object(app_shell, "_load_dotenv")
        self._load_dotenv_patcher.start()

    def tearDown(self):
        self._load_dotenv_patcher.stop()
        _purge_app_shell_related_modules()


class TestHappyPath(BaseAppShellTest):
    def test_returns_zero_on_clean_window_close(self):
        rc = self.app_shell.main(interval_seconds=60, ui_port=9999)
        self.assertEqual(rc, 0)

    def test_calls_happen_in_order(self):
        """start_ui_server -> start_engine -> wait_for_http -> webview.create_window/start
        -> (on close) stop_engine -> stop_ui_server.
        """
        manager = MagicMock()

        from desktop.net_util import wait_for_http
        from desktop.ui_server import start_ui_server, stop_ui_server
        from desktop.engine_supervisor import start_engine, stop_engine

        manager.attach_mock(start_ui_server, "start_ui_server")
        manager.attach_mock(start_engine, "start_engine")
        manager.attach_mock(wait_for_http, "wait_for_http")
        manager.attach_mock(self.fake_webview.create_window, "create_window")
        manager.attach_mock(self.fake_webview.start, "webview_start")
        manager.attach_mock(stop_engine, "stop_engine")
        manager.attach_mock(stop_ui_server, "stop_ui_server")

        rc = self.app_shell.main(interval_seconds=120, ui_port=8080)
        self.assertEqual(rc, 0)

        expected_order = [
            "start_ui_server",
            "start_engine",
            "wait_for_http",
            "create_window",
            "webview_start",
            "stop_engine",
            "stop_ui_server",
        ]
        actual_order = [c[0] for c in manager.mock_calls]
        self.assertEqual(actual_order, expected_order)

    def test_ui_port_passed_through_to_start_ui_server_and_url(self):
        from desktop.ui_server import start_ui_server

        self.app_shell.main(interval_seconds=60, ui_port=7777)

        start_ui_server.assert_called_once_with(7777, headless=True)
        self.fake_webview.create_window.assert_called_once_with(
            "InvestYo", "http://127.0.0.1:7777", width=1440, height=900
        )

    def test_ui_port_none_resolves_via_find_free_port(self):
        from desktop.net_util import find_free_port
        from desktop.ui_server import start_ui_server

        find_free_port.return_value = 54321
        self.app_shell.main(interval_seconds=60, ui_port=None)

        find_free_port.assert_called_once()
        start_ui_server.assert_called_once_with(54321, headless=True)

    def test_engine_started_with_interval_seconds(self):
        from desktop.engine_supervisor import start_engine

        self.app_shell.main(interval_seconds=42, ui_port=1234)
        start_engine.assert_called_once_with(42)

    def test_teardown_called_exactly_once_each(self):
        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import stop_ui_server

        self.app_shell.main(interval_seconds=60, ui_port=1234)
        stop_engine.assert_called_once()
        stop_ui_server.assert_called_once()


class TestWaitForHttpNotReady(BaseAppShellTest):
    def test_proceeds_to_open_window_even_when_not_ready(self):
        """If wait_for_http returns False, app_shell should log an error but
        still attempt to open the window (best-effort, never hang forever).
        """
        from desktop.net_util import wait_for_http

        wait_for_http.return_value = False

        rc = self.app_shell.main(interval_seconds=60, ui_port=1234)

        self.assertEqual(rc, 0)
        self.fake_webview.create_window.assert_called_once()
        self.fake_webview.start.assert_called_once()


class TestExceptionDuringWindow(BaseAppShellTest):
    def test_webview_start_exception_still_tears_down(self):
        """If webview.start() raises, stop_engine and stop_ui_server must
        STILL be called — the finally block must run under all conditions.
        """
        self.fake_webview.start.side_effect = RuntimeError("window crashed")

        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import stop_ui_server

        with self.assertRaises(RuntimeError):
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_called_once()
        stop_ui_server.assert_called_once()

    def test_create_window_exception_still_tears_down(self):
        self.fake_webview.create_window.side_effect = RuntimeError("cannot create window")

        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import stop_ui_server

        with self.assertRaises(RuntimeError):
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_called_once()
        stop_ui_server.assert_called_once()

    def test_start_engine_exception_still_tears_down_ui_server(self):
        """If start_engine() itself raises (engine_handle never assigned),
        stop_ui_server must still run; stop_engine must NOT be called since
        there is no handle to stop.
        """
        from desktop.engine_supervisor import start_engine, stop_engine
        from desktop.ui_server import stop_ui_server

        start_engine.side_effect = RuntimeError("engine failed to start")

        with self.assertRaises(RuntimeError):
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_not_called()
        stop_ui_server.assert_called_once()

    def test_keyboard_interrupt_during_webview_start_still_tears_down(self):
        self.fake_webview.start.side_effect = KeyboardInterrupt()

        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import stop_ui_server

        with self.assertRaises(KeyboardInterrupt):
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_called_once()
        stop_ui_server.assert_called_once()


class _FakeWatcherThread:
    """Stand-in for threading.Thread that captures its target/daemon args
    instead of actually starting a background thread. Tests invoke the
    captured target manually to simulate "the signal arrived", since
    signal.sigwait() is separately mocked to return immediately rather than
    genuinely blocking on an OS signal.
    """

    instances: list["_FakeWatcherThread"] = []

    def __init__(self, target=None, daemon=None, **kw):
        self.target = target
        self.daemon = daemon
        self.started = False
        _FakeWatcherThread.instances.append(self)

    def start(self):
        self.started = True


class TestSigtermHandling(BaseAppShellTest):
    """Covers the SIGTERM hardening: an external `kill <pid>` (as opposed to
    the user clicking the window's own close button) must still tear down
    both child processes.

    A plain `signal.signal(SIGTERM, ...)` handler does NOT work here in
    practice: CPython only invokes a Python-level signal handler when the
    interpreter's bytecode loop regains control, and pywebview's native
    Cocoa event loop never hands control back while the window is open --
    confirmed with a real `kill -TERM` against a real running window not
    terminating the process even after 20+ seconds. The fix instead blocks
    SIGTERM via `signal.pthread_sigmask` and spawns a dedicated daemon
    thread that calls the genuinely-blocking `signal.sigwait()`, which the
    kernel wakes directly regardless of what the main thread is doing.

    These tests mock `threading.Thread` (capturing its target instead of
    really starting a thread), `signal.sigwait` (returns immediately instead
    of blocking), and `os._exit` (never actually terminates the test
    process) so the watcher's logic is exercised deterministically and
    synchronously.
    """

    def setUp(self):
        super().setUp()
        _FakeWatcherThread.instances = []
        self._thread_patcher = patch("app_shell.threading.Thread", _FakeWatcherThread)
        self._thread_patcher.start()
        self._sigwait_patcher = patch("app_shell.signal.sigwait", return_value=signal.SIGTERM)
        self._sigwait_patcher.start()
        self.addCleanup(self._thread_patcher.stop)
        self.addCleanup(self._sigwait_patcher.stop)

    def _watcher_target(self):
        """The single watcher thread's captured target callable."""
        self.assertEqual(len(_FakeWatcherThread.instances), 1)
        return _FakeWatcherThread.instances[0].target

    def test_watcher_thread_started_as_daemon_with_sigterm_blocked(self):
        with patch("app_shell.signal.pthread_sigmask") as mock_mask:
            rc = self.app_shell.main(interval_seconds=60, ui_port=1234)

        self.assertEqual(rc, 0)
        instance = _FakeWatcherThread.instances[0]
        self.assertTrue(instance.started)
        self.assertTrue(instance.daemon)
        self.assertIsNotNone(instance.target)
        mock_mask.assert_any_call(signal.SIG_BLOCK, {signal.SIGTERM})

    def test_pthread_sigmask_unblocked_on_clean_exit(self):
        with patch("app_shell.signal.pthread_sigmask") as mock_mask:
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        mock_mask.assert_any_call(signal.SIG_UNBLOCK, {signal.SIGTERM})

    def test_sigterm_during_window_tears_down_and_force_exits(self):
        """Simulates an external `kill <pid>` arriving while the window is
        open: invoking the captured watcher target directly (as sigwait()
        returning would) must call stop_engine/stop_ui_server and then
        force-exit via os._exit.
        """
        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import stop_ui_server

        def _webview_start_side_effect(*a, **kw):
            # Simulate the OS delivering SIGTERM: invoke the watcher's
            # target the same way the real thread would once sigwait()
            # (mocked to return immediately) unblocks it.
            self._watcher_target()()

        self.fake_webview.start.side_effect = _webview_start_side_effect

        with patch("app_shell.os._exit") as mock_exit:
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_called_once()
        stop_ui_server.assert_called_once()
        mock_exit.assert_called_once_with(0)

    def test_sigterm_teardown_is_idempotent_with_normal_finally(self):
        """If SIGTERM arrives (and is handled) mid-run, main()'s own
        `finally` teardown afterward must not double-stop anything.
        """
        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import stop_ui_server

        def _webview_start_side_effect(*a, **kw):
            # os._exit is mocked to a no-op so control returns here instead
            # of the process actually exiting, letting us prove the
            # subsequent `finally` path doesn't double-call teardown.
            with patch("app_shell.os._exit"):
                self._watcher_target()()

        self.fake_webview.start.side_effect = _webview_start_side_effect

        self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_called_once()
        stop_ui_server.assert_called_once()

    def test_sigterm_before_children_started_is_a_harmless_noop(self):
        """A SIGTERM arriving right as start_ui_server is called -- before
        `ui_popen`/`engine_handle` are assigned in main()'s scope -- must not
        raise or call stop_engine/stop_ui_server; there is nothing to tear
        down yet.
        """
        from desktop.engine_supervisor import stop_engine
        from desktop.ui_server import start_ui_server, stop_ui_server

        def _start_ui_server_side_effect(*a, **kw):
            self._watcher_target()()
            return MagicMock(name="ui_popen")

        start_ui_server.side_effect = _start_ui_server_side_effect

        with patch("app_shell.os._exit") as mock_exit:
            self.app_shell.main(interval_seconds=60, ui_port=1234)

        stop_engine.assert_not_called()
        stop_ui_server.assert_not_called()
        mock_exit.assert_called_once_with(0)


class TestArgparse(BaseAppShellTest):
    def test_default_interval_is_300(self):
        args = self.app_shell._parse_args([])
        self.assertEqual(args.interval_seconds, 300)

    def test_interval_flag_parsed(self):
        args = self.app_shell._parse_args(["--interval", "60"])
        self.assertEqual(args.interval_seconds, 60)


if __name__ == "__main__":
    unittest.main()
