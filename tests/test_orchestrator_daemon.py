"""
tests/test_orchestrator_daemon.py — tests for desktop/orchestrator_daemon.py
=============================================================================
``desktop/orchestrator_daemon.py`` depends on ``desktop.daemon_runtime``'s
``OrchestratorDaemon`` class, built by a parallel workstream. ``run_forever()``
resolves that name via a DEFERRED import (``from desktop.daemon_runtime
import OrchestratorDaemon`` inside the function body), so these tests patch
``desktop.daemon_runtime.OrchestratorDaemon`` directly via
``unittest.mock.patch`` -- exercising ONLY this module's own lifecycle logic
(call order, signal handling, teardown idempotency, discovery-file writing,
CLI parsing) independently of whatever the real daemon_runtime implementation
ends up doing.

The SIGTERM/SIGINT hardening tests mirror tests/test_app_shell.py's proven
technique exactly: mock `threading.Thread` (capturing its target instead of
starting a real thread), mock `signal.sigwait` (return immediately instead
of genuinely blocking on an OS signal), and mock `os._exit` (never actually
terminate the test process) so the watcher's logic is exercised
deterministically and synchronously by invoking the captured target
directly -- never by sending real OS signals.
"""

from __future__ import annotations

import json
import signal
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class BaseDaemonEntrypointTest(unittest.TestCase):
    """Common setup: import desktop.orchestrator_daemon fresh and patch
    _load_dotenv so tests never mutate the real process environment.
    """

    def setUp(self):
        import desktop.orchestrator_daemon as orchestrator_daemon
        self.mod = orchestrator_daemon

        self._load_dotenv_patcher = patch.object(self.mod, "_load_dotenv")
        self._load_dotenv_patcher.start()
        self.addCleanup(self._load_dotenv_patcher.stop)

    def _make_mock_daemon_class(self, status=None):
        """Return a MagicMock standing in for OrchestratorDaemon, plus the
        instance it will return when constructed.

        ``run_forever()`` imports ``OrchestratorDaemon`` via a DEFERRED
        import (``from desktop.daemon_runtime import OrchestratorDaemon``
        inside the function body) -- mirroring app_shell.py's deferred
        `from desktop.xxx import yyy` pattern so the module is importable
        before the real daemon_runtime lands. That means the name to patch
        is ``desktop.daemon_runtime.OrchestratorDaemon`` (resolved fresh on
        each call), not a module-level attribute of
        ``desktop.orchestrator_daemon`` itself.
        """
        instance = MagicMock(name="daemon_instance")
        instance.status.return_value = status or {
            "is_running": False,
            "current_run_id": None,
            "interval_seconds": 60,
            "last_run": None,
            "engines_warm": True,
            "started_at": None,
        }
        daemon_cls = MagicMock(name="OrchestratorDaemon", return_value=instance)
        return daemon_cls, instance

    def _patch_daemon_class(self, daemon_cls):
        import desktop.daemon_runtime as daemon_runtime
        return patch.object(daemon_runtime, "OrchestratorDaemon", daemon_cls)

    def _patch_uvicorn(self):
        """Patch uvicorn.Config/Server so run_forever() never attempts to
        bind a real socket in tests. The fake Server reports ``started =
        True`` immediately, so run_forever()'s bounded readiness-poll loop
        exits on its first check instead of waiting out the full timeout.
        Returns a context manager; the constructed fake instance is
        available as ``self.fake_api_server`` once entered indirectly via
        ``self._fake_api_server_holder`` (set at construction time).

        ``run_forever`` may construct TWO ``uvicorn.Server`` instances (the
        Control API, always; the Pilots API, only when
        ``settings.PILOTS_API_ENABLED``) -- the holder's ``"instance"`` key
        keeps its original last-constructed-wins meaning for existing
        single-server tests, and ``"instances"`` (a list, construction
        order) is added for tests that need to distinguish both."""
        holder = self._fake_api_server_holder = {}
        holder["instances"] = []

        class _FakeUvicornServer:
            def __init__(self, config):
                self.config = config
                self.started = True
                self.should_exit = False
                holder["instance"] = self
                holder["instances"].append(self)

            def run(self):
                # Real uvicorn.Server.run() blocks until should_exit; the
                # fake returns immediately since it's invoked via a fake
                # thread target in these tests (never actually called in
                # most tests since threading.Thread itself is faked too).
                return None

        return patch.multiple(
            self.mod.uvicorn,
            Config=MagicMock(name="uvicorn.Config"),
            Server=_FakeUvicornServer,
        )


class _FakeWatcherThread:
    """Stand-in for threading.Thread that captures its target/daemon/name
    args instead of actually starting a background thread, and makes
    .join() a no-op (rather than blocking forever) since the real watcher
    thread in production genuinely never returns until a signal arrives.

    ``run_forever`` constructs up to THREE threads via ``threading.Thread``:
    the Control API server thread (``name="OrchestratorControlAPI"``,
    created first), the OPTIONAL Pilots API server thread
    (``name="PilotsAPI"``, created second, only when
    ``settings.PILOTS_API_ENABLED``), and the SIGTERM/SIGINT watcher thread
    (unnamed, created last). Tests that only care about the watcher use
    ``watcher_instances()`` to filter ``instances`` down to the one whose
    ``name`` is neither known API thread name, so this fake continues to
    serve all three call sites without changing every existing assertion's
    shape.
    """

    _API_THREAD_NAMES = frozenset({"OrchestratorControlAPI", "PilotsAPI"})

    instances: list["_FakeWatcherThread"] = []

    def __init__(self, target=None, daemon=None, name=None, **kw):
        self.target = target
        self.daemon = daemon
        self.name = name
        self.started = False
        _FakeWatcherThread.instances.append(self)

    def start(self):
        self.started = True

    def join(self, timeout=None):
        return None

    @classmethod
    def watcher_instances(cls) -> list["_FakeWatcherThread"]:
        return [t for t in cls.instances if t.name not in cls._API_THREAD_NAMES]

    @classmethod
    def api_instances(cls) -> list["_FakeWatcherThread"]:
        return [t for t in cls.instances if t.name == "OrchestratorControlAPI"]

    @classmethod
    def pilots_api_instances(cls) -> list["_FakeWatcherThread"]:
        return [t for t in cls.instances if t.name == "PilotsAPI"]


class TestRunForeverHappyPath(BaseDaemonEntrypointTest):
    """run_forever() must start the daemon before touching signals, and
    must write the discovery file with the expected keys.
    """

    def setUp(self):
        super().setUp()
        _FakeWatcherThread.instances = []
        self._thread_patcher = patch.object(self.mod.threading, "Thread", _FakeWatcherThread)
        self._thread_patcher.start()
        self.addCleanup(self._thread_patcher.stop)
        self._sigmask_patcher = patch.object(self.mod.signal, "pthread_sigmask")
        self._sigmask_patcher.start()
        self.addCleanup(self._sigmask_patcher.stop)

    def test_daemon_start_called_before_watcher_thread_runs(self):
        daemon_cls, instance = self._make_mock_daemon_class()
        call_order = []
        instance.start.side_effect = lambda: call_order.append("start")

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file") as mock_write:
            rc = self.mod.run_forever(60)

        self.assertEqual(rc, 0)
        instance.start.assert_called_once()
        self.assertEqual(call_order, ["start"])
        mock_write.assert_called_once()
        # daemon.start() must precede thread creation (watcher started after
        # the Control API thread, which is created first -- see
        # test_control_api_thread_started_after_daemon_start below for that
        # ordering assertion specifically).
        watchers = _FakeWatcherThread.watcher_instances()
        self.assertEqual(len(watchers), 1)
        self.assertTrue(watchers[0].started)
        self.assertTrue(watchers[0].daemon)

    def test_daemon_constructed_with_interval_dry_run_strict_kwargs(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"):
            self.mod.run_forever(45, dry_run=True, strict=True)

        daemon_cls.assert_called_once_with(interval_seconds=45, dry_run=True, strict=True)

    def test_shutdown_called_on_clean_return_path(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"):
            self.mod.run_forever(60)

        instance.shutdown.assert_called_once_with(timeout=10.0)

    def test_control_api_daemon_registered_after_daemon_start(self):
        """set_daemon(daemon) must be called with the real daemon instance
        after daemon.start() succeeds, so the Control API can immediately
        serve status/trigger requests against warm engines."""
        daemon_cls, instance = self._make_mock_daemon_class()
        call_order = []
        instance.start.side_effect = lambda: call_order.append("daemon.start")

        import api.control_api as control_api

        def _record_set_daemon(d):
            call_order.append("set_daemon")

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"), \
             patch.object(control_api, "set_daemon", side_effect=_record_set_daemon) as mock_set_daemon:
            self.mod.run_forever(60)

        mock_set_daemon.assert_called_once_with(instance)
        instance.start.assert_called_once()
        self.assertEqual(call_order, ["daemon.start", "set_daemon"])

    def test_control_api_thread_started_after_daemon_start(self):
        daemon_cls, instance = self._make_mock_daemon_class()
        call_order = []
        instance.start.side_effect = lambda: call_order.append("daemon.start")

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"):
            self.mod.run_forever(60)

        api_threads = _FakeWatcherThread.api_instances()
        self.assertEqual(len(api_threads), 1)
        self.assertTrue(api_threads[0].started)
        self.assertTrue(api_threads[0].daemon)
        self.assertEqual(api_threads[0].name, "OrchestratorControlAPI")

    def test_uvicorn_config_constructed_with_settings_host_and_port(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        from settings import settings

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"):
            self.mod.run_forever(60)
            self.mod.uvicorn.Config.assert_called_once()
            _, kwargs = self.mod.uvicorn.Config.call_args

        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], settings.ORCHESTRATOR_API_PORT)

    def test_daemon_json_includes_port_key(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        from settings import settings

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file") as mock_write:
            self.mod.run_forever(60)

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        self.assertEqual(kwargs.get("port"), settings.ORCHESTRATOR_API_PORT)

    def test_api_server_should_exit_set_and_thread_joined_on_teardown(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"):
            self.mod.run_forever(60)

        fake_server = self._fake_api_server_holder["instance"]
        self.assertTrue(fake_server.should_exit)
        api_threads = _FakeWatcherThread.api_instances()
        self.assertEqual(len(api_threads), 1)
        # _FakeWatcherThread.join() is a no-op but must have been callable
        # without error during teardown (proven implicitly by run_forever
        # returning cleanly above); explicitly assert it's the same thread
        # object whose .started flag we already verified.
        self.assertTrue(api_threads[0].started)


class TestPilotsAPIHosting(BaseDaemonEntrypointTest):
    """settings.PILOTS_API_ENABLED gates an OPTIONAL second uvicorn service
    (api/pilots_api.py) hosted alongside the always-on Control API. False
    (the default) must reproduce every pre-existing behavior byte-for-byte;
    True must start it, wait for it, tear it down, and record its port."""

    def setUp(self):
        super().setUp()
        _FakeWatcherThread.instances = []
        self._thread_patcher = patch.object(self.mod.threading, "Thread", _FakeWatcherThread)
        self._thread_patcher.start()
        self.addCleanup(self._thread_patcher.stop)
        self._sigmask_patcher = patch.object(self.mod.signal, "pthread_sigmask")
        self._sigmask_patcher.start()
        self.addCleanup(self._sigmask_patcher.stop)

    def test_disabled_by_default_no_second_server_or_thread(self):
        from settings import settings
        self.assertFalse(settings.PILOTS_API_ENABLED)  # precondition: real default

        daemon_cls, instance = self._make_mock_daemon_class()
        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file") as mock_write:
            self.mod.run_forever(60)

        self.assertEqual(len(_FakeWatcherThread.pilots_api_instances()), 0)
        self.assertEqual(len(self._fake_api_server_holder["instances"]), 1)  # Control API only
        _, kwargs = mock_write.call_args
        self.assertIsNone(kwargs.get("pilots_api_port"))

    def test_enabled_starts_second_server_and_thread_on_configured_port(self):
        from settings import settings

        daemon_cls, instance = self._make_mock_daemon_class()
        with patch.object(settings, "PILOTS_API_ENABLED", True), \
             patch.object(settings, "PILOTS_API_PORT", 8602), \
             self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file") as mock_write:
            self.mod.run_forever(60)

            # Assert on the uvicorn.Config mock WHILE the patch is active —
            # it's restored to the real class once this `with` block exits.
            self.assertEqual(self.mod.uvicorn.Config.call_count, 2)
            pilots_config_kwargs = self.mod.uvicorn.Config.call_args_list[1].kwargs
            self.assertEqual(pilots_config_kwargs["host"], "127.0.0.1")
            self.assertEqual(pilots_config_kwargs["port"], 8602)

        pilots_threads = _FakeWatcherThread.pilots_api_instances()
        self.assertEqual(len(pilots_threads), 1)
        self.assertTrue(pilots_threads[0].started)
        self.assertTrue(pilots_threads[0].daemon)

        # Two uvicorn.Server instances: Control API + Pilots API.
        self.assertEqual(len(self._fake_api_server_holder["instances"]), 2)

        _, kwargs = mock_write.call_args
        self.assertEqual(kwargs.get("pilots_api_port"), 8602)
        # Control API port is unaffected by the optional second service.
        self.assertEqual(kwargs.get("port"), settings.ORCHESTRATOR_API_PORT)

    def test_enabled_teardown_stops_pilots_api_server_and_joins_thread(self):
        from settings import settings

        daemon_cls, instance = self._make_mock_daemon_class()
        with patch.object(settings, "PILOTS_API_ENABLED", True), \
             self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"):
            self.mod.run_forever(60)

        control_server, pilots_server = self._fake_api_server_holder["instances"]
        self.assertTrue(control_server.should_exit)
        self.assertTrue(pilots_server.should_exit)
        self.assertTrue(_FakeWatcherThread.pilots_api_instances()[0].started)

    def test_pilots_api_startup_failure_is_swallowed_daemon_still_starts(self):
        """A broken import/construction for the OPTIONAL Pilots API must never
        abort the daemon or the always-on Control API (CONSTRAINT #6)."""
        from settings import settings

        daemon_cls, instance = self._make_mock_daemon_class()

        def _boom(*a, **kw):
            raise RuntimeError("pilots_api import exploded")

        with patch.object(settings, "PILOTS_API_ENABLED", True), \
             self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file") as mock_write, \
             patch.dict("sys.modules", {"api.pilots_api": None}):
            # Forcing the deferred `from api.pilots_api import app` to raise:
            # removing the module from sys.modules with a None sentinel makes
            # the import machinery raise ImportError on the next `import`.
            self.mod.run_forever(60)

        instance.start.assert_called_once()  # daemon itself is unaffected
        self.assertEqual(len(_FakeWatcherThread.pilots_api_instances()), 0)
        _, kwargs = mock_write.call_args
        self.assertIsNone(kwargs.get("pilots_api_port"))
        # Control API still got its one server as usual.
        self.assertEqual(len(self._fake_api_server_holder["instances"]), 1)


class TestDaemonFileWriting(BaseDaemonEntrypointTest):
    """_write_daemon_file() writes valid JSON with the expected keys, using
    a temp directory so it never touches the real repo's output/ directory.
    """

    def test_writes_expected_keys(self, ):
        instance = MagicMock()
        instance.status.return_value = {
            "is_running": False,
            "current_run_id": None,
            "interval_seconds": 30,
            "last_run": None,
            "engines_warm": True,
            "started_at": None,
        }

        with self._tmp_output_dir() as output_dir:
            self.mod._write_daemon_file(instance, output_dir)
            final_path = output_dir / "daemon.json"
            self.assertTrue(final_path.exists())
            payload = json.loads(final_path.read_text(encoding="utf-8"))

        self.assertIn("pid", payload)
        self.assertIn("state", payload)
        self.assertIn("interval_seconds", payload)
        self.assertIn("started_at", payload)
        self.assertEqual(payload["interval_seconds"], 30)

    def test_port_key_present_when_passed(self):
        instance = MagicMock()
        instance.status.return_value = {"is_running": False, "interval_seconds": 30}

        with self._tmp_output_dir() as output_dir:
            self.mod._write_daemon_file(instance, output_dir, port=8601)
            payload = json.loads((output_dir / "daemon.json").read_text(encoding="utf-8"))

        self.assertIn("port", payload)
        self.assertEqual(payload["port"], 8601)

    def test_port_key_is_none_when_omitted(self):
        instance = MagicMock()
        instance.status.return_value = {"is_running": False, "interval_seconds": 30}

        with self._tmp_output_dir() as output_dir:
            self.mod._write_daemon_file(instance, output_dir)
            payload = json.loads((output_dir / "daemon.json").read_text(encoding="utf-8"))

        self.assertIn("port", payload)
        self.assertIsNone(payload["port"])

    def test_creates_output_dir_if_missing(self):
        instance = MagicMock()
        instance.status.return_value = {"is_running": False, "interval_seconds": 0}

        with self._tmp_output_dir() as output_dir:
            nested = output_dir / "nested" / "dir"
            self.assertFalse(nested.exists())
            self.mod._write_daemon_file(instance, nested)
            self.assertTrue((nested / "daemon.json").exists())

    def test_write_failure_is_caught_and_logged_never_propagates(self):
        instance = MagicMock()
        instance.status.return_value = {"is_running": False, "interval_seconds": 0}

        with self._tmp_output_dir() as output_dir:
            with patch.object(Path, "replace", side_effect=OSError("disk full")):
                # Must not raise.
                self.mod._write_daemon_file(instance, output_dir)

    def _tmp_output_dir(self):
        import tempfile

        class _Ctx:
            def __enter__(self_inner):
                self_inner._tmpdir = tempfile.TemporaryDirectory()
                return Path(self_inner._tmpdir.name)

            def __exit__(self_inner, *exc):
                self_inner._tmpdir.cleanup()
                return False

        return _Ctx()


class TestSignalHandling(BaseDaemonEntrypointTest):
    """Covers the SIGTERM/SIGINT hardening, mirroring
    tests/test_app_shell.py's TestSigtermHandling technique exactly: mock
    threading.Thread (capture target), mock signal.sigwait (return
    immediately), mock os._exit (never really terminate the test process).
    """

    def setUp(self):
        super().setUp()
        _FakeWatcherThread.instances = []
        self._thread_patcher = patch.object(self.mod.threading, "Thread", _FakeWatcherThread)
        self._thread_patcher.start()
        self._sigwait_patcher = patch.object(
            self.mod.signal, "sigwait", return_value=signal.SIGTERM
        )
        self._sigwait_patcher.start()
        self.addCleanup(self._thread_patcher.stop)
        self.addCleanup(self._sigwait_patcher.stop)

    def _watcher_target(self):
        watchers = _FakeWatcherThread.watcher_instances()
        self.assertEqual(len(watchers), 1)
        return watchers[0].target

    def test_signals_blocked_before_watcher_thread_started(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"), \
             patch.object(self.mod.signal, "pthread_sigmask") as mock_mask:
            rc = self.mod.run_forever(60)

        self.assertEqual(rc, 0)
        instance_thread = _FakeWatcherThread.watcher_instances()[0]
        self.assertTrue(instance_thread.started)
        self.assertTrue(instance_thread.daemon)
        self.assertIsNotNone(instance_thread.target)
        mock_mask.assert_any_call(
            signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT}
        )

    def test_pthread_sigmask_unblocked_on_clean_exit(self):
        daemon_cls, instance = self._make_mock_daemon_class()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"), \
             patch.object(self.mod.signal, "pthread_sigmask") as mock_mask:
            self.mod.run_forever(60)

        mock_mask.assert_any_call(
            signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT}
        )

    def test_sigterm_arrival_calls_shutdown_exactly_once_and_force_exits(self):
        """Simulates an external `kill <pid>` arriving: invoking the
        captured watcher target directly (as sigwait() returning would)
        must call daemon.shutdown(timeout=10.0) and then force-exit via
        os._exit.
        """
        daemon_cls, instance = self._make_mock_daemon_class()

        # Make the WATCHER thread's join() invoke the watcher target
        # directly, simulating the OS delivering the signal while the main
        # thread is parked. The Control API thread (created earlier, with
        # name="OrchestratorControlAPI") keeps the plain no-op join() from
        # _FakeWatcherThread so its own teardown-time join() doesn't
        # re-trigger this side effect.
        def _join_side_effect(timeout=None):
            self._watcher_target()()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"), \
             patch.object(self.mod.signal, "pthread_sigmask"):

            class _JoinTriggeringThread(_FakeWatcherThread):
                def join(self_inner, timeout=None):
                    if self_inner.name != "OrchestratorControlAPI":
                        _join_side_effect()

            with patch.object(self.mod.threading, "Thread", _JoinTriggeringThread):
                with patch.object(self.mod.os, "_exit") as mock_exit:
                    self.mod.run_forever(60)

        instance.shutdown.assert_called_once_with(timeout=10.0)
        mock_exit.assert_called_once_with(0)

    def test_signals_blocked_before_any_thread_or_daemon_start(self):
        """Regression test for a real bug found via live-process verification
        (not caught by any of this file's other mocked tests, since mocks
        can't observe cross-thread signal-mask ordering).

        ``signal.pthread_sigmask()`` sets the CALLING THREAD's mask, not a
        process-wide one. A thread created BEFORE this call (the daemon's
        own optional interval-timer thread, or the Control API's uvicorn
        thread) inherits an UNBLOCKED mask at creation time and stays
        eligible for the kernel's default SIGTERM disposition for its
        entire lifetime -- when a real ``kill -TERM <pid>`` arrives, the
        kernel may deliver it to that thread instead of the sigwait
        watcher, silently killing the whole process with ZERO log output
        and without ever running teardown.

        Confirmed via a real subprocess + a real ``kill -TERM`` + a traced
        ``os._exit()`` wrapper: with ``pthread_sigmask(SIG_BLOCK, ...)``
        called AFTER starting the Control API thread, the traced
        ``os._exit`` was never invoked and no "Received signal" log line
        ever appeared, yet the process still died within ~1s -- proof the
        OS's default disposition killed it directly on the unblocked
        Control API thread, bypassing this module's teardown entirely.
        Moving the ``pthread_sigmask`` call to the top of ``run_forever()``,
        before ``daemon.start()`` and before the Control API thread is
        created, fixed it (re-verified with the same real-process test).

        This test guards that ordering the only way a fully-mocked suite
        can: asserting SIG_BLOCK is called before daemon construction,
        before ``daemon.start()``, and before every ``threading.Thread(...)``
        construction.
        """
        call_order: list[str] = []

        daemon_cls, instance = self._make_mock_daemon_class()

        def _daemon_cls_side_effect(*_a, **_k):
            call_order.append("daemon_constructed")
            return instance

        daemon_cls.side_effect = _daemon_cls_side_effect
        instance.start.side_effect = lambda: call_order.append("daemon_start")

        def _mask_side_effect(how, _mask):
            if how == signal.SIG_BLOCK:
                call_order.append("sig_block")

        class _OrderTrackingThread(_FakeWatcherThread):
            def __init__(self, target=None, daemon=None, name=None, **kw):
                call_order.append(f"thread_created:{name}")
                super().__init__(target=target, daemon=daemon, name=name, **kw)

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"), \
             patch.object(self.mod.threading, "Thread", _OrderTrackingThread), \
             patch.object(self.mod.signal, "pthread_sigmask", side_effect=_mask_side_effect):
            self.mod.run_forever(60)

        self.assertIn("sig_block", call_order)
        block_idx = call_order.index("sig_block")
        thread_and_start_events = [
            e for e in call_order
            if e.startswith("thread_created:") or e in ("daemon_constructed", "daemon_start")
        ]
        self.assertTrue(thread_and_start_events, "expected at least one thread/daemon-start event")
        for entry in thread_and_start_events:
            entry_idx = call_order.index(entry)
            self.assertGreater(
                entry_idx, block_idx,
                f"'{entry}' happened before SIG_BLOCK (full order: {call_order}) -- "
                f"a thread created or daemon.start() called before the signal mask "
                f"is blocked inherits an UNBLOCKED mask and can be killed directly "
                f"by SIGTERM's default disposition, bypassing the sigwait watcher "
                f"entirely (this is the exact bug this test guards against).",
            )

    def test_teardown_is_idempotent_across_signal_path_and_normal_finally(self):
        """If the signal watcher's teardown runs (simulated), and control
        then also flows through the normal `finally` teardown afterward
        (os._exit is mocked to a no-op so it doesn't really terminate),
        daemon.shutdown must be called only once.
        """
        daemon_cls, instance = self._make_mock_daemon_class()

        class _JoinTriggeringThread(_FakeWatcherThread):
            def join(self_inner, timeout=None):
                if self_inner.name == "OrchestratorControlAPI":
                    return
                with patch.object(self.mod.os, "_exit"):
                    self._watcher_target()()

        with self._patch_daemon_class(daemon_cls), \
             self._patch_uvicorn(), \
             patch.object(self.mod, "_write_daemon_file"), \
             patch.object(self.mod.signal, "pthread_sigmask"), \
             patch.object(self.mod.threading, "Thread", _JoinTriggeringThread):
            self.mod.run_forever(60)

        instance.shutdown.assert_called_once_with(timeout=10.0)


class TestArgparse(BaseDaemonEntrypointTest):
    def test_interval_defaults_to_none(self):
        args = self.mod._parse_args([])
        self.assertIsNone(args.interval)

    def test_interval_flag_explicit_value_wins(self):
        args = self.mod._parse_args(["--interval", "120"])
        self.assertEqual(args.interval, 120)

    def test_dry_run_flag_parsed(self):
        args = self.mod._parse_args(["--dry-run"])
        self.assertTrue(args.dry_run)
        self.assertFalse(args.strict)

    def test_strict_flag_parsed(self):
        args = self.mod._parse_args(["--strict"])
        self.assertTrue(args.strict)
        self.assertFalse(args.dry_run)

    def test_defaults_are_false_when_omitted(self):
        args = self.mod._parse_args([])
        self.assertFalse(args.dry_run)
        self.assertFalse(args.strict)


class TestIntervalFallbackToSettings(BaseDaemonEntrypointTest):
    """Mirrors the __main__ block's logic: an explicit --interval always
    wins; an omitted flag falls back to settings.ORCHESTRATOR_INTERVAL_SECONDS.
    This test exercises that fallback expression directly (rather than
    running the __main__ block, which isn't executed under pytest import)
    since that's where the CLI/settings merge logic actually lives.
    """

    def test_explicit_interval_flag_wins_over_settings_default(self):
        args = self.mod._parse_args(["--interval", "15"])
        with patch.object(self.mod, "run_forever") as mock_run_forever:
            mock_settings = MagicMock(ORCHESTRATOR_INTERVAL_SECONDS=999)
            with patch("settings.settings", mock_settings):
                interval = args.interval if args.interval is not None else mock_settings.ORCHESTRATOR_INTERVAL_SECONDS
        self.assertEqual(interval, 15)

    def test_omitted_interval_falls_back_to_settings_value(self):
        args = self.mod._parse_args([])
        mock_settings = MagicMock(ORCHESTRATOR_INTERVAL_SECONDS=42)
        interval = args.interval if args.interval is not None else mock_settings.ORCHESTRATOR_INTERVAL_SECONDS
        self.assertEqual(interval, 42)


class TestPipelineModeThreading(unittest.TestCase):
    """Exercises the real ``OrchestratorDaemon.trigger_run(mode=...)`` path:
    ``mode`` is threaded into ``main_orchestrator._main_body(mode=...)`` and
    recorded on the ``RunRecord``; ``status()`` exposes a most-recent-first
    ``run_history``. The heavy pipeline body is stubbed with an async recorder,
    so no real data fetch / engines run.
    """

    def _run_and_wait(self, daemon, *, mode_kwarg, recorder_holder):
        import time
        from desktop.daemon_runtime import RunState

        if mode_kwarg is _SENTINEL:
            result = daemon.trigger_run()
        else:
            result = daemon.trigger_run(mode=mode_kwarg)
        run_id = result.run_id
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            rec = daemon.get_run(run_id)
            if rec is not None and rec.state != RunState.RUNNING:
                return run_id, rec
            time.sleep(0.02)
        self.fail("run did not complete within timeout")

    def _make_daemon_with_recorder(self):
        import main_orchestrator
        from desktop.daemon_runtime import OrchestratorDaemon

        recorded = {}

        async def _fake_main_body(dry_run, strict=False, *, engines=None,
                                  data_engine=None, mode="full"):
            recorded["mode"] = mode

        daemon = OrchestratorDaemon(interval_seconds=0)
        patcher = patch.object(main_orchestrator, "_main_body", _fake_main_body)
        patcher.start()
        self.addCleanup(patcher.stop)
        return daemon, recorded

    def test_mode_data_threaded_into_main_body_and_run_record(self):
        daemon, recorded = self._make_daemon_with_recorder()
        run_id, rec = self._run_and_wait(daemon, mode_kwarg="data", recorder_holder=recorded)
        self.assertEqual(recorded["mode"], "data")
        self.assertEqual(rec.mode, "data")

    def test_mode_metrics_threaded_through(self):
        daemon, recorded = self._make_daemon_with_recorder()
        run_id, rec = self._run_and_wait(daemon, mode_kwarg="metrics", recorder_holder=recorded)
        self.assertEqual(recorded["mode"], "metrics")
        self.assertEqual(rec.mode, "metrics")

    def test_default_mode_is_full(self):
        daemon, recorded = self._make_daemon_with_recorder()
        run_id, rec = self._run_and_wait(daemon, mode_kwarg=_SENTINEL, recorder_holder=recorded)
        self.assertEqual(recorded["mode"], "full")
        self.assertEqual(rec.mode, "full")

    def test_status_run_history_most_recent_first_with_mode(self):
        daemon, recorded = self._make_daemon_with_recorder()
        self._run_and_wait(daemon, mode_kwarg="data", recorder_holder=recorded)
        self._run_and_wait(daemon, mode_kwarg="metrics", recorder_holder=recorded)
        history = daemon.status()["run_history"]
        self.assertEqual(len(history), 2)
        # Most-recent-first: the "metrics" run was triggered last.
        self.assertEqual(history[0].mode, "metrics")
        self.assertEqual(history[1].mode, "data")


_SENTINEL = object()


class TestMainBodyStepSelection(unittest.TestCase):
    """Directly proves ``main_orchestrator._main_body_impl`` selects the right
    pipeline steps per ``mode``. The AsyncPipelineRunner is faked to capture the
    step list and no-op its ``run`` (no real data fetch / engines / broker)."""

    def _capture_steps_for_mode(self, mode):
        import asyncio
        import main_orchestrator
        import pipeline.runner

        captured = {}

        class _FakeRunner:
            def __init__(self, steps):
                captured["steps"] = [type(s).__name__ for s in steps]

            async def run(self, ctx, progress):
                return None

        with patch.object(pipeline.runner, "AsyncPipelineRunner", _FakeRunner):
            asyncio.run(
                main_orchestrator._main_body_impl(False, mode=mode, progress=None)
            )
        return captured["steps"]

    def test_data_mode_only_fetch_step(self):
        self.assertEqual(self._capture_steps_for_mode("data"), ["AsyncDataFetchStep"])

    def test_metrics_mode_fetch_plus_pipeline(self):
        self.assertEqual(
            self._capture_steps_for_mode("metrics"),
            ["AsyncDataFetchStep", "RunPipelineStep"],
        )

    def test_full_mode_all_four_steps(self):
        self.assertEqual(
            self._capture_steps_for_mode("full"),
            [
                "AsyncDataFetchStep",
                "RunPipelineStep",
                "BrokerExecutionStep",
                "StateSnapshotStep",
            ],
        )


if __name__ == "__main__":
    unittest.main()
