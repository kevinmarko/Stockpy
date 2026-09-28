"""Tests for scripts/compare_shadow_queue.py (step 5.2's read-only shadow-vs-real
queue diff) and the shadow history archive it reads."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import compare_shadow_queue as csq

_T0 = datetime(2026, 9, 29, 12, 45, tzinfo=timezone.utc)  # 08:45 ET


def _source(ts: datetime, targets):
    return {"schema_version": 1, "source_id": "advisory", "generated_at": ts.isoformat(),
            "targets": targets}


def _queue(ts: datetime, intents):
    return {"generated_at": ts.isoformat(), "mode": "review", "intents": intents}


def _target(sym, action="BUY", conviction=0.85, pct=0.05):
    return {"symbol": sym, "action": action, "conviction": conviction,
            "suggested_position_pct": pct, "strategy": "s", "rationale": "r"}


def _intent(sym, side="buy", conviction=0.85, notional=25.0, allow=False):
    return {"symbol": sym, "side": side, "conviction": conviction, "target_notional": notional,
            "gate_allowed": True, "allow_place": allow}


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_real(out: Path, targets, intents, ts=_T0):
    _write(out / "queue_sources" / "advisory.json", _source(ts, targets))
    _write(out / "execution_queue.json", _queue(ts, intents))


def _write_history(out: Path, ts: datetime, targets, intents=None):
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    hist = out / "shadow" / "history"
    _write(hist / f"{stamp}_advisory.json", _source(ts, targets))
    if intents is not None:
        _write(hist / f"{stamp}_execution_queue.json", _queue(ts, intents))


def test_identical_runs_report_no_difference(tmp_path, capsys):
    _write_real(tmp_path, [_target("AAPL")], [_intent("AAPL")])
    _write_history(tmp_path, _T0 + timedelta(minutes=20), [_target("AAPL")], [_intent("AAPL")])
    assert csq.main(["--output-dir", str(tmp_path)]) == 0
    assert "TOTAL: 0 differing symbol rows" in capsys.readouterr().out


def test_picks_the_earliest_shadow_run_after_the_real_run(tmp_path):
    _write_real(tmp_path, [_target("AAPL")], [_intent("AAPL")])
    _write_history(tmp_path, _T0 - timedelta(hours=1), [_target("OLD")], [])
    _write_history(tmp_path, _T0 + timedelta(minutes=15), [_target("AAPL")], [_intent("AAPL")])
    _write_history(tmp_path, _T0 + timedelta(hours=2), [_target("LATE")], [])
    c = csq.compare(tmp_path)
    assert c.ok and c.shadow_origin.startswith("history/20260929T130000Z")
    assert c.n_differences == 0


def test_differences_are_reported_per_symbol_and_field(tmp_path):
    _write_real(tmp_path, [_target("AAPL"), _target("KO", "SELL", 0.8, 0.0)],
                [_intent("AAPL"), _intent("KO", "sell", 0.8, 100.0)])
    _write_history(tmp_path, _T0 + timedelta(minutes=5),
                   [_target("AAPL", conviction=0.7), _target("NVDA")],
                   [_intent("NVDA")])
    c = csq.compare(tmp_path)
    targets = {r["symbol"]: r["changed"] for r in c.targets}
    assert targets == {"AAPL": ["conviction"], "KO": ["<only in real>"], "NVDA": ["<only in shadow>"]}
    intents = {r["symbol"]: r["changed"] for r in c.intents}
    assert intents == {"AAPL": ["<only in real>"], "KO": ["<only in real>"], "NVDA": ["<only in shadow>"]}
    assert csq.main(["--output-dir", str(tmp_path)]) == 2


def test_nothing_to_compare_exits_1(tmp_path, capsys):
    assert csq.main(["--output-dir", str(tmp_path)]) == 1
    _write_real(tmp_path, [_target("AAPL")], [])
    _write_history(tmp_path, _T0 - timedelta(minutes=1), [_target("AAPL")], [])
    assert csq.main(["--output-dir", str(tmp_path)]) == 1
    assert "no shadow run at or after" in capsys.readouterr().out


def test_max_lag_excludes_late_shadow_runs(tmp_path):
    _write_real(tmp_path, [_target("AAPL")], [])
    _write_history(tmp_path, _T0 + timedelta(hours=5), [_target("AAPL")], [])
    assert not csq.compare(tmp_path, timedelta(hours=2)).ok
    assert csq.compare(tmp_path, timedelta(hours=6)).ok


def test_live_shadow_files_are_used_without_history(tmp_path):
    _write_real(tmp_path, [_target("AAPL")], [_intent("AAPL")])
    ts = _T0 + timedelta(minutes=30)
    _write(tmp_path / "shadow" / "queue_sources" / "advisory.json", _source(ts, [_target("AAPL")]))
    _write(tmp_path / "shadow" / "execution_queue.json", _queue(ts, [_intent("AAPL")]))
    c = csq.compare(tmp_path)
    assert c.ok and c.shadow_origin == "shadow/queue_sources/advisory.json"
    assert c.n_differences == 0


def test_a_stale_live_shadow_queue_is_not_paired_with_a_newer_source(tmp_path):
    _write_real(tmp_path, [_target("AAPL")], [_intent("AAPL")])
    ts = _T0 + timedelta(minutes=30)
    _write(tmp_path / "shadow" / "queue_sources" / "advisory.json", _source(ts, [_target("AAPL")]))
    _write(tmp_path / "shadow" / "execution_queue.json", _queue(_T0 - timedelta(days=1), [_intent("AAPL")]))
    c = csq.compare(tmp_path)
    assert c.shadow_queue_generated_at is None
    assert {r["symbol"]: r["changed"] for r in c.intents} == {"AAPL": ["<only in real>"]}


def test_json_output_is_machine_readable(tmp_path, capsys):
    _write_real(tmp_path, [_target("AAPL")], [_intent("AAPL")])
    _write_history(tmp_path, _T0, [_target("AAPL")], [_intent("AAPL")])
    csq.main(["--output-dir", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["n_differences"] == 0


def test_script_writes_nothing(tmp_path):
    _write_real(tmp_path, [_target("AAPL")], [_intent("AAPL")])
    _write_history(tmp_path, _T0 + timedelta(minutes=1), [_target("AAPL")], [_intent("AAPL")])
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    csq.main(["--output-dir", str(tmp_path)])
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before


def test_archive_shadow_run_copies_this_cycles_files_and_prunes(tmp_path, monkeypatch):
    import pipeline.production_steps as ps

    shadow = tmp_path / "shadow"
    src = shadow / "queue_sources" / "advisory.json"
    q = shadow / "execution_queue.json"
    _write(src, {"a": 1})
    _write(q, {"q": 1})
    ps.archive_shadow_run(shadow, _T0, src, q)
    ps.archive_shadow_run(shadow, _T0 + timedelta(hours=1), src, None)  # compose wrote nothing
    names = sorted(p.name for p in (shadow / "history").iterdir())
    assert names == ["20260929T124500Z_advisory.json", "20260929T124500Z_execution_queue.json",
                     "20260929T134500Z_advisory.json"]
    monkeypatch.setattr(ps, "SHADOW_HISTORY_MAX_FILES", 2)
    ps.archive_shadow_run(shadow, _T0 + timedelta(hours=2), src, q)
    names = sorted(p.name for p in (shadow / "history").iterdir())
    assert names == ["20260929T144500Z_advisory.json", "20260929T144500Z_execution_queue.json"]
