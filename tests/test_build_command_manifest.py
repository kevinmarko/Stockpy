"""Tests for scripts/build_command_manifest.py::_fetch_strategy_registry.

_fetch_strategy_registry fetches ``sorted(STRATEGY_REGISTRY.keys())`` from
scripts.refresh_validations via an isolated subprocess, mirroring
cli_introspect/capture.py's isolation philosophy (refresh_validations
heavy-imports pandas/numpy/the quant engines). It must NEVER raise -- any
failure (timeout, non-zero exit, unparseable/wrong-shaped output) degrades to
``[]`` (CONSTRAINT #6: dead-letter, don't crash).
"""
from __future__ import annotations

import json
import subprocess
from unittest import mock

from scripts.build_command_manifest import _fetch_strategy_registry


# --------------------------------------------------------------------------- #
# Real invocation -- light smoke test
# --------------------------------------------------------------------------- #
def test_fetch_strategy_registry_real_invocation_returns_nonempty_list_of_strings():
    names = _fetch_strategy_registry(timeout=120)
    assert isinstance(names, list)
    assert len(names) > 0
    assert all(isinstance(n, str) for n in names)
    # Sorted, per the child process's own sorted(STRATEGY_REGISTRY.keys()).
    assert names == sorted(names)


# --------------------------------------------------------------------------- #
# Deterministic failure modes -- subprocess.run mocked
# --------------------------------------------------------------------------- #
def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    result = mock.MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_fetch_strategy_registry_timeout_returns_empty_list():
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
        names = _fetch_strategy_registry()
    assert names == []


def test_fetch_strategy_registry_nonzero_exit_returns_empty_list():
    with mock.patch("subprocess.run", return_value=_result(returncode=1, stderr="Traceback...\nImportError: boom")):
        names = _fetch_strategy_registry()
    assert names == []


def test_fetch_strategy_registry_empty_stdout_returns_empty_list():
    with mock.patch("subprocess.run", return_value=_result(returncode=0, stdout="")):
        names = _fetch_strategy_registry()
    assert names == []


def test_fetch_strategy_registry_unparseable_stdout_returns_empty_list():
    with mock.patch("subprocess.run", return_value=_result(returncode=0, stdout="not json{{{")):
        names = _fetch_strategy_registry()
    assert names == []


def test_fetch_strategy_registry_wrong_shape_list_of_ints_returns_empty_list():
    with mock.patch("subprocess.run", return_value=_result(returncode=0, stdout=json.dumps([1, 2, 3]))):
        names = _fetch_strategy_registry()
    assert names == []


def test_fetch_strategy_registry_wrong_shape_dict_returns_empty_list():
    with mock.patch("subprocess.run", return_value=_result(returncode=0, stdout=json.dumps({"a": "b"}))):
        names = _fetch_strategy_registry()
    assert names == []
