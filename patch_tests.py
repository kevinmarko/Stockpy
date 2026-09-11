import sys

content = open("tests/test_symbol_view_store.py").read()
content = content.replace('SymbolViewStore("sqlite:///:memory:", readonly=True)', 'SymbolViewStore("sqlite:///fake.db", readonly=True)')

with open("tests/test_symbol_view_store.py", "w") as f:
    f.write(content)

