import datetime
import pathlib
from unittest import mock

import pytest

from data.symbol_view_store import SymbolViewStore


def test_symbol_view_store_isolation():
    # conftest.py isolates this by default, so omitting db_url here
    # uses the isolated memory DB.
    store = SymbolViewStore()
    
    # Assert nothing there yet
    assert store.get_last_viewed("AAPL") is None
    assert store.get_recently_viewed_symbols(7) == []
    
    # Record view
    store.record_view("AAPL")
    last_viewed = store.get_last_viewed("aapl")
    assert last_viewed is not None
    assert isinstance(last_viewed, datetime.datetime)
    
    # Verify the recently viewed symbols
    recent = store.get_recently_viewed_symbols(7)
    assert recent == ["AAPL"]


def test_symbol_view_store_multiple_symbols():
    store = SymbolViewStore()
    
    store.record_view("AAPL")
    store.record_view("MSFT")
    
    recent = store.get_recently_viewed_symbols(7)
    assert recent == ["MSFT", "AAPL"]
    
    store.record_view("AAPL")
    # AAPL is now most recent
    recent = store.get_recently_viewed_symbols(7)
    assert recent == ["AAPL", "MSFT"]


def test_symbol_view_store_readonly_raises_on_write(tmp_path):
    # Write mode db first
    db_url = f"sqlite:///{tmp_path}/fake.db"
    write_store = SymbolViewStore(db_url)
    write_store.record_view("AAPL")
    
    # Using a fake readonly DB
    store = SymbolViewStore(db_url, readonly=True)
    with pytest.raises(RuntimeError):
        store.record_view("AAPL")


def test_symbol_view_store_readonly_degrades_gracefully(tmp_path):
    # Without tables created, a read will fail and log a warning, 
    # returning a safe default.
    db_url = f"sqlite:///{tmp_path}/fake.db"
    store = SymbolViewStore(db_url, readonly=True)
    assert store.get_last_viewed("AAPL") is None
    assert store.get_recently_viewed_symbols(7) == []


class TestSymbolViewStoreDependencies:
    def test_no_sizing_or_execution_or_signals_module_imports_symbol_view_store(self):
        offenders = []
        for pattern in ("sizing/*.py", "execution/*.py", "signals/*.py"):
            for path in pathlib.Path(".").glob(pattern):
                if not path.is_file():
                    continue
                src = path.read_text(encoding="utf-8")
                if "symbol_view_store" in src:
                    offenders.append(str(path))
        assert offenders == [], f"symbol_view_store must never be imported by sizing/execution/signals: {offenders}"
