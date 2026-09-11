"""Tests for data/symbol_view_store.py."""

import ast
from pathlib import Path

import pytest
from datetime import datetime, timedelta, timezone

from data.symbol_view_store import SymbolViewStore, SymbolView


@pytest.fixture(autouse=True)
def _isolate_views(_isolate_symbol_view_db_in_tests):
    pass


def test_symbol_view_store_record_and_get():
    store = SymbolViewStore()
    
    # Initially empty
    assert store.get_recently_viewed_symbols() == []
    
    # Record a view
    store.record_view("AAPL")
    
    # Retrieve it
    recent = store.get_recently_viewed_symbols()
    assert recent == ["AAPL"]
    
    # Record another view
    store.record_view("MSFT")
    
    # Retrieve them, should be sorted by most recent
    recent = store.get_recently_viewed_symbols()
    assert recent == ["MSFT", "AAPL"]
    
    # Update AAPL view
    store.record_view("aapl ")
    recent = store.get_recently_viewed_symbols()
    assert recent == ["AAPL", "MSFT"]
    

def test_symbol_view_store_get_recently_viewed_cutoff():
    store = SymbolViewStore()
    store.record_view("AAPL")
    store.record_view("MSFT")
    
    # Force AAPL to be older than 14 days
    session = store.Session()
    row = session.query(SymbolView).filter_by(symbol="AAPL").first()
    row.viewed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=15)
    session.commit()
    session.close()
    
    recent = store.get_recently_viewed_symbols(days=14)
    assert recent == ["MSFT"]
    
    # But if we ask for 20 days, AAPL is there
    recent = store.get_recently_viewed_symbols(days=20)
    assert recent == ["MSFT", "AAPL"]


def test_symbol_view_store_readonly_enforcement():
    store = SymbolViewStore()
    store.record_view("AAPL")
    
    readonly_store = SymbolViewStore(readonly=True)
    assert readonly_store.get_recently_viewed_symbols() == ["AAPL"]
    
    with pytest.raises(RuntimeError, match="read-only"):
        readonly_store.record_view("MSFT")


def test_symbol_view_store_import_guard():
    """Ensure symbol_view_store is never imported by sizing, signals, or execution."""
    src_dir = Path(__file__).parent.parent
    
    forbidden_dirs = ["signals", "sizing", "execution"]
    
    for d in forbidden_dirs:
        for py_file in (src_dir / d).rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        assert "symbol_view_store" not in name.name, f"Forbidden import in {py_file}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert "symbol_view_store" not in node.module, f"Forbidden import in {py_file}"
