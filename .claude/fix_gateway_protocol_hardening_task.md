# FIX gateway protocol-correctness fixes (F1–F5) — Task Tracker

| # | Task | Status |
|---|------|--------|
| 1 | F1: reject `from_fix_str()` on missing/unlocatable CheckSum (Tag 10) | ✅ Done |
| 2 | F2: verify BodyLength (Tag 9) against actual body byte count | ✅ Done |
| 3 | F3: `FixValueError` + SOH/`=`/`\|` rejection in `set_tag()`/`to_fix_str()` | ✅ Done |
| 4 | F3 cleanup: reformat `route_order()`'s `\|`-separated Text field | ✅ Done |
| 5 | F3 supporting fix: WARNING log on duplicate tag in `from_fix_str()` | ✅ Done |
| 6 | F4: `_is_legal_order_transition()` guard in `EXECUTION_REPORT` branch | ✅ Done |
| 7 | F5: pending-TestRequest tracking + `_disconnect_sync()` + timeout logic | ✅ Done |
| 8 | Minor: `api/pilots_api.py` reconnect endpoint uses `_set_state()` | ✅ Done |
| 9 | Regression tests (9 new, one per finding + 2 companions) | ✅ Done |
| 10 | `docs/architecture/execution.md` bullet update | ✅ Done |
| 11 | `pytest tests/test_fix_gateway.py -q` — zero failures | ✅ 55/55 passed |
| 12 | `pytest tests/test_pilots_api.py -q -k fix` — zero failures | ✅ 31/31 passed |
| 13 | `pytest tests/test_multi_broker_gateway.py -q` — zero failures | ✅ 38/38 passed |
| 14 | Full offline suite (`pytest tests/ -q -p no:randomly -m "not network"`) | ✅ 12056 passed, 5 pre-existing unrelated failures (confirmed via `git stash`) |
| 15 | PR opened | ⏳ pending |
