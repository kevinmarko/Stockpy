"""Tests for execution/agentic_research.py — Research Scout / Strategy Analyst artifacts.

Fully offline. The thesis validator is the anti-fabrication gate: every
number in a thesis must cite a field that holds exactly that value.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from execution import agentic_preflight as ap
from execution import agentic_research as ar

GEN = "2026-11-03T15:00:00+00:00"
TODAY = date(2026, 11, 3)
_REAL_TODAY_ET = ar._today_et


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    """The validator rejects past earnings dates relative to the US/Eastern
    date; pin it so the fixtures' 2026-11-20 date is not a date bomb."""
    monkeypatch.setattr(ar, "_today_et", lambda: TODAY)


def queue():
    return {"mode": "live", "generated_at": GEN, "kill_switch_active": False, "intents": [
        {"client_order_id": "c-T", "symbol": "T", "side": "buy", "conviction": 0.95,
         "target_notional": 150.0, "allow_place": True, "gate_allowed": True, "qty": None},
        {"client_order_id": "c-F", "symbol": "F", "side": "sell", "conviction": 0.8,
         "target_notional": 120.0, "allow_place": True, "gate_allowed": True, "qty": 10.0},
    ]}


def research(**over):
    doc = {"queue_generated_at": GEN, "symbols": {
        "T": {"next_earnings": {"date": "2026-11-20", "source": "get_earnings_calendar"},
              "analyst_ratings": {"buy": 12, "hold": 5, "sell": 1, "source": "get_equity_analyst_ratings"},
              "recent_filings": [{"form": "8-K", "filed": "2026-10-30", "url": "https://sec.gov/x"}],
              "news": [{"headline": "h", "url": "https://example.com/n", "published": "2026-11-02"}],
              "data_gaps": []},
        "F": {"next_earnings": {"date": None, "reason": "none scheduled in 31-day window",
                                "source": "get_earnings_calendar"},
              "analyst_ratings": None, "data_gaps": ["analyst_ratings"]},
    }}
    doc.update(over)
    return doc


SOURCES = {"signals_T.json": {"data": {"modules": [{"name": "momentum", "score": "0.42"},
                                                   {"name": "value", "score": 0.1}]},
                              "regime": "RISK_ON"}}


def thesis(coid="c-T", **over):
    t = {"symbol": "T", "side": "buy", "thesis": "Momentum leader with a cheap valuation.",
         "invalidation": "Close below the 50-day average.", "horizon": "30d",
         "drivers": [
             {"name": "conviction", "value": 0.95, "cite": f"queue:intents[client_order_id={coid}].conviction"},
             {"name": "momentum", "value": 0.42, "cite": "file:signals_T.json:data.modules[name=momentum].score"},
             {"name": "regime", "value": "RISK_ON", "cite": "file:signals_T.json:regime"},
         ],
         "flags": [], "data_gaps": []}
    t.update(over)
    return t


def theses(*items, gen=GEN):
    return {"queue_generated_at": gen, "theses": dict(items)}


# ---------------------------------------------------------------------------
# Path / cite resolution
# ---------------------------------------------------------------------------

class TestResolve:
    @pytest.mark.parametrize("path,expected", [
        ("intents[0].symbol", "T"),
        ("intents[client_order_id=c-F].qty", 10.0),
        ("generated_at", GEN),
    ])
    def test_paths(self, path, expected):
        assert ar.resolve_path(queue(), path) == expected

    @pytest.mark.parametrize("path", ["intents[9].symbol", "intents[client_order_id=nope].qty",
                                      "nope", "intents.symbol", "", "intents[0]..symbol", "generated_at[0]"])
    def test_missing_paths(self, path):
        assert ar.resolve_path(queue(), path) is ar._MISSING

    @pytest.mark.parametrize("cite", ["queue", "web:https://x", "file:../etc/passwd.json:a",
                                      "file:missing.json:a", "file:signals_T.json", 42, None])
    def test_bad_cites_are_errors(self, cite):
        value, err = ar.resolve_cite(cite, queue(), SOURCES)
        assert value is ar._MISSING and err

    @pytest.mark.parametrize("claimed,source,ok", [
        (0.42, "0.42", True), (0.42, 0.42000000001, True), (0.42, 0.43, False),
        ("RISK_ON", "RISK_ON", True), ("RISK_ON", "RISK_OFF", False),
        (1.0, "not a number", False), (float("nan"), float("nan"), False), (True, 1, False),
    ])
    def test_values_match(self, claimed, source, ok):
        assert ar.values_match(claimed, source) is ok


# ---------------------------------------------------------------------------
# research.json
# ---------------------------------------------------------------------------

class TestResearch:
    def test_clean(self):
        assert ar.validate_research(research(), queue()) == []

    def test_wrong_queue(self):
        assert any("not this queue" in e for e in ar.validate_research(research(queue_generated_at="x"), queue()))

    def test_missing_queue_symbol(self):
        doc = research()
        del doc["symbols"]["F"]
        assert any("no entry for queue symbol(s) F" in e for e in ar.validate_research(doc, queue()))

    def test_null_earnings_needs_reason(self):
        doc = research()
        doc["symbols"]["F"]["next_earnings"] = {"date": None, "source": "x"}
        assert any("null with no reason" in e for e in ar.validate_research(doc, queue()))

    def test_bad_date(self):
        doc = research()
        doc["symbols"]["T"]["next_earnings"]["date"] = "Nov 20"
        assert any("not YYYY-MM-DD" in e for e in ar.validate_research(doc, queue()))

    def test_ratings_missing_without_gap(self):
        doc = research()
        doc["symbols"]["F"]["data_gaps"] = []
        assert any("analyst_ratings: missing" in e for e in ar.validate_research(doc, queue()))

    def test_news_needs_url(self):
        doc = research()
        doc["symbols"]["T"]["news"] = [{"headline": "no link"}]
        assert any("news[0]" in e for e in ar.validate_research(doc, queue()))

    def test_not_an_object(self):
        assert ar.validate_research([], queue()) == ["research: not a JSON object"]

    def test_earnings_bridge(self):
        got = ar.earnings_dates_from_research(research())
        assert got == {"T": date(2026, 11, 20), "F": None}

    def test_earnings_bridge_drops_unparseable_and_reasonless(self):
        doc = research()
        doc["symbols"]["T"]["next_earnings"]["date"] = "soon"
        doc["symbols"]["F"]["next_earnings"] = {"date": None}
        assert ar.earnings_dates_from_research(doc) == {}

    # -- past earnings date (review #5) -------------------------------------
    def test_past_earnings_date_is_an_error_for_that_symbol(self):
        doc = research()
        doc["symbols"]["T"]["next_earnings"]["date"] = "2026-11-02"
        errs = ar.validate_research(doc, queue(), today=TODAY)
        assert any("research[T].next_earnings.date 2026-11-02 is in the past" in e for e in errs)
        assert set(ar.research_symbol_errors(doc, today=TODAY)) == {"T"}

    def test_earnings_today_is_not_past(self):
        doc = research()
        doc["symbols"]["T"]["next_earnings"]["date"] = TODAY.isoformat()
        assert ar.validate_research(doc, queue(), today=TODAY) == []

    def test_past_check_uses_injected_today(self):
        assert ar.validate_research(research(), queue(), today=date(2026, 11, 21))
        assert ar.validate_research(research(), queue(), today=date(2026, 11, 20)) == []

    def test_today_defaults_to_us_eastern_date(self, monkeypatch):
        # 2026-11-04 03:00 UTC is still 2026-11-03 in New York.
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc).astimezone(tz)
        monkeypatch.setattr(ar, "datetime", _DT)
        assert _REAL_TODAY_ET() == date(2026, 11, 3)

    # -- coverage follows the preflight (review #9) --------------------------
    def _pf(self, passed_coids, gen=GEN):
        return {"queue_generated_at": gen, "verdicts": [
            {"client_order_id": c, "passed": c in passed_coids} for c in ("c-T", "c-F")]}

    def test_with_preflight_only_passed_symbols_required(self):
        doc = research()
        del doc["symbols"]["F"]
        assert ar.validate_research(doc, queue(), preflight=self._pf({"c-T"})) == []
        assert any("F" in e for e in ar.validate_research(doc, queue(), preflight=self._pf({"c-F"})))

    def test_with_preflight_nothing_passed_requires_nothing(self):
        doc = research(symbols={})
        assert ar.validate_research(doc, queue(), preflight=self._pf(set())) == []

    def test_preflight_for_other_queue_keeps_todays_rule(self):
        doc = research()
        del doc["symbols"]["F"]
        errs = ar.validate_research(doc, queue(), preflight=self._pf({"c-T"}, gen="2026-11-03T14:00:00+00:00"))
        assert any("no entry for queue symbol(s) F" in e for e in errs)

    def test_required_coverage(self):
        assert ar.required_coverage(queue()) == (["c-F", "c-T"], {"F", "T"})
        assert ar.required_coverage(queue(), self._pf({"c-F"})) == (["c-F"], {"F"})
        assert ar.required_coverage(queue(), {"queue_generated_at": GEN, "verdicts": "junk"}) == (
            ["c-F", "c-T"], {"F", "T"})


# ---------------------------------------------------------------------------
# theses.json — the anti-fabrication gate
# ---------------------------------------------------------------------------

class TestTheses:
    def test_clean(self):
        assert ar.validate_theses(theses(("c-T", thesis())), queue(), SOURCES) == {}

    def test_tampered_number_is_rejected(self):
        t = thesis()
        t["drivers"][1]["value"] = 0.55
        errs = ar.validate_theses(theses(("c-T", t)), queue(), SOURCES)
        assert any("does not match its source" in e for e in errs["c-T"])

    def test_tampered_conviction_is_rejected(self):
        t = thesis()
        t["drivers"][0]["value"] = 0.99
        assert "c-T" in ar.validate_theses(theses(("c-T", t)), queue(), SOURCES)

    def test_number_without_cite_is_rejected(self):
        t = thesis()
        del t["drivers"][1]["cite"]
        assert any("is not 'queue:" in e for e in ar.validate_theses(theses(("c-T", t)), queue(), SOURCES)["c-T"])

    def test_dangling_cite_is_rejected(self):
        t = thesis()
        t["drivers"][1]["cite"] = "file:signals_T.json:data.modules[name=rsi].score"
        assert any("does not resolve" in e for e in ar.validate_theses(theses(("c-T", t)), queue(), SOURCES)["c-T"])

    def test_nan_needs_data_gap(self):
        t = thesis()
        t["drivers"].append({"name": "forecast_30d", "value": None})
        assert "c-T" in ar.validate_theses(theses(("c-T", t)), queue(), SOURCES)
        t["data_gaps"] = ["forecast_30d"]
        assert ar.validate_theses(theses(("c-T", t)), queue(), SOURCES) == {}
        t["drivers"][-1]["value"] = float("nan")
        assert ar.validate_theses(theses(("c-T", t)), queue(), SOURCES) == {}

    def test_unknown_intent(self):
        assert "c-X" in ar.validate_theses(theses(("c-X", thesis("c-X"))), queue(), SOURCES)

    def test_symbol_and_side_must_match_queue(self):
        errs = ar.validate_theses(theses(("c-T", thesis(symbol="F", side="sell"))), queue(), SOURCES)["c-T"]
        assert any("symbol" in e for e in errs) and any("side" in e for e in errs)

    def test_length_limits(self):
        errs = ar.validate_theses(theses(("c-T", thesis(thesis="x" * 281, invalidation=""))), queue(), SOURCES)["c-T"]
        assert any("281 chars" in e for e in errs) and any("'invalidation' is required" in e for e in errs)

    def test_wrong_queue_is_doc_level(self):
        errs = ar.validate_theses(theses(("c-T", thesis()), gen="other"), queue(), SOURCES)
        assert "_doc" in errs
        assert ar.valid_thesis_ids(theses(("c-T", thesis()), gen="other"), errs) == []

    def test_valid_ids_exclude_bad_ones(self):
        bad = thesis("c-F", symbol="F", side="sell")
        bad["drivers"] = [{"name": "conviction", "value": 0.1, "cite": "queue:intents[client_order_id=c-F].conviction"}]
        doc = theses(("c-T", thesis()), ("c-F", bad))
        errs = ar.validate_theses(doc, queue(), SOURCES)
        assert ar.valid_thesis_ids(doc, errs) == ["c-T"]


# ---------------------------------------------------------------------------
# Files on disk + CLI
# ---------------------------------------------------------------------------

def _write_run(tmp_path, research_doc=None, theses_doc=None, sources=SOURCES):
    run = ar.run_dir_for_queue(queue(), output_dir=tmp_path)
    (run / "inputs").mkdir(parents=True)
    for name, data in sources.items():
        (run / "inputs" / name).write_text(json.dumps(data))
    if research_doc is not None:
        (run / ar.RESEARCH_FILENAME).write_text(json.dumps(research_doc))
    if theses_doc is not None:
        (run / ar.THESES_FILENAME).write_text(json.dumps(theses_doc))
    (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
    return run


class TestOnDisk:
    def test_run_dir_matches_preflight_folder(self, tmp_path):
        report = ap.PreflightReport(GEN, "live", "", [], [], {})
        assert ar.run_dir_for_queue(queue(), tmp_path).name == ap.run_id_for(report)

    def test_load_clean(self, tmp_path):
        _write_run(tmp_path, research(), theses(("c-T", thesis())))
        art = ar.load_run_artifacts(queue(), tmp_path)
        assert art["research_errors"] == [] and art["thesis_errors"] == {}
        assert art["valid_thesis_ids"] == ["c-T"]

    def test_load_missing_files_are_reported(self, tmp_path):
        _write_run(tmp_path)
        art = ar.load_run_artifacts(queue(), tmp_path)
        assert art["research"] is None and art["theses"] is None
        assert art["research_errors"] and "_doc" in art["thesis_errors"]
        assert art["valid_thesis_ids"] == []

    def test_corrupt_input_reported(self, tmp_path):
        run = _write_run(tmp_path, research(), theses(("c-T", thesis())))
        (run / "inputs" / "broken.json").write_text("{nope")
        assert ar.load_run_artifacts(queue(), tmp_path)["input_errors"]

    def test_cli_ok_and_not_ok(self, tmp_path, capsys):
        _write_run(tmp_path, research(), theses(("c-T", thesis())))
        assert ar.main(["validate", "--output-dir", str(tmp_path)]) == 0
        assert json.loads(capsys.readouterr().out)["ok"] is True
        t = thesis()
        t["drivers"][1]["value"] = 0.9
        (ar.run_dir_for_queue(queue(), tmp_path) / ar.THESES_FILENAME).write_text(json.dumps(theses(("c-T", t))))
        assert ar.main(["validate", "--output-dir", str(tmp_path)]) == 1
        assert "c-T" in json.loads(capsys.readouterr().out)["thesis_errors"]

    def test_coverage_and_missing_theses_follow_the_preflight(self, tmp_path):
        doc = research()
        del doc["symbols"]["F"]
        _write_run(tmp_path, doc, theses(("c-T", thesis())))
        art = ar.load_run_artifacts(queue(), tmp_path)        # no preflight on disk
        assert not art["has_preflight"] and art["research_errors"]
        assert art["required_client_order_ids"] == ["c-F", "c-T"] and art["missing_thesis_ids"] == ["c-F"]
        report = ap.PreflightReport(GEN, "live", "", [], [], {})
        folder = ar.run_dir_for_queue(queue(), tmp_path)
        (folder / ap.PREFLIGHT_FILENAME).write_text(json.dumps({
            "queue_generated_at": GEN, "run_blockers": [], "verdicts": [
                {"client_order_id": "c-T", "passed": True}, {"client_order_id": "c-F", "passed": False}]}))
        assert folder.name == ap.run_id_for(report)
        art = ar.load_run_artifacts(queue(), tmp_path)
        assert art["has_preflight"] and art["research_errors"] == []
        assert art["required_client_order_ids"] == ["c-T"] and art["missing_thesis_ids"] == []
        # an explicit preflight=None ignores the file (today's rule)
        assert ar.load_run_artifacts(queue(), tmp_path, preflight=None)["research_errors"]

    def test_valid_research_symbols_exclude_bad_entries(self, tmp_path):
        doc = research()
        doc["symbols"]["T"]["next_earnings"]["date"] = "2026-10-01"
        _write_run(tmp_path, doc, theses(("c-T", thesis())))
        art = ar.load_run_artifacts(queue(), tmp_path)
        assert art["valid_research_symbols"] == ["F"]
        assert any("in the past" in e for e in art["research_errors"])

    def test_cli_unreadable_queue(self, tmp_path, capsys):
        assert ar.main(["validate", "--output-dir", str(tmp_path)]) == 2


class TestThesisTextNumbers:
    """A number in the thesis sentence must be one of the cited driver values."""

    def _errs(self, text):
        t = thesis(thesis=text)
        return ar.validate_theses(theses(("c-T", t)), queue(), SOURCES).get("c-T", [])

    @pytest.mark.parametrize("text", [
        "Conviction 0.95 and momentum 0.42.",
        "Momentum score of $0.42 with no number games.",
        "Strong 95% conviction.",                       # 0.95 * 100
        "A Q3 beat and S&P500 inclusion talk.",         # digits inside words are not quotes
        "No numbers at all.",
    ])
    def test_allowed(self, text):
        assert self._errs(text) == []

    @pytest.mark.parametrize("text,bad", [
        ("Momentum 0.5 and rising.", "0.5"),
        ("Analysts see 30% upside.", "30%"),
        ("Target 1,200 within the month.", "1200"),
    ])
    def test_uncited_number_rejected(self, text, bad):
        assert any(f"quotes {bad}" in e for e in self._errs(text))

    def test_invalidation_numbers_are_conditions_not_quotes(self):
        t = thesis(invalidation="Close below the 50-day average for 3 sessions.")
        assert ar.validate_theses(theses(("c-T", t)), queue(), SOURCES) == {}

    @pytest.mark.parametrize("text,expected", [
        ("$27.48 and 1,200 and -3.5 and 12%", [(27.48, False), (1200.0, False), (-3.5, False), (12.0, True)]),
        ("Q3 S&P500 v2", []),
        (None, []),
        (".5% and -.25 and 0.42.", [(0.5, True), (-0.25, False), (0.42, False)]),
        ("v2.5 and S&P500.5", []),
    ])
    def test_number_extraction(self, text, expected):
        assert ar._numbers_in(text) == expected

    def test_leading_decimal_number_is_checked(self):
        """Review #5: '.5%' used to slip past the check (the lookbehind saw '.')."""
        assert any("quotes 0.5%" in e for e in self._errs("Analysts see .5% upside."))
        assert self._errs("Momentum of .42 leads.") == []
