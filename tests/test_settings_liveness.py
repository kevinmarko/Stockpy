"""
tests/test_settings_liveness.py
================================
Tests for ``scripts/settings_liveness.py`` — the static per-key liveness
classifier that answers "if I ``setattr`` this setting at runtime, does the
running process actually observe it?".

Three layers, in increasing order of what they'd catch:

``TestCaptureRules``
    Synthetic fixtures under ``tests/fixtures/settings_liveness/``, one
    minimal module per capture rule. On the real tree most of these rules fire
    zero times — a rule firing zero times is not evidence it is correct, only
    evidence nothing currently has that shape. These turn "I reasoned this
    rule is right" into "this rule is tested", including the two whose FALSE
    positives were the hardest part of the design (an escaping closure vs. a
    per-call worker closure; a fresh dependency factory vs. a memoized one).

``TestEveryCaptureRuleIsExercised``
    Meta-guard: every rule name the classifier can emit is produced by at
    least one fixture. Adding a rule without a fixture fails here.

``TestRealTreeRegressions``
    Pins the five cases that were actually got WRONG during prototyping,
    against the live codebase (not fixtures). These are the tests that catch
    the specific bugs if they ever reappear — most importantly that
    ``MARKET_DATA_PROVIDER`` is captured indirectly, one call hop out of
    ``CompositeProvider.__init__``. A naive local-``__init__``-only rule
    reports a frozen provider selection as live-safe: the dangerous direction.

``TestCommittedArtifactIsFresh``
    Drift guard: re-runs the classifier and asserts the committed
    ``docs/settings_liveness.json`` still matches, so a PR that silently
    changes classification behaviour fails CI instead of letting the
    committed file go stale.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import settings_liveness as sl

# xdist_group pins every test in this module to the same worker under
# `--dist loadgroup` (CI/Makefile) -- without it, the default `--dist load`
# distribution can split these tests across workers, silently rebuilding the
# module-scoped `model_fields`/`real_analysis`/`real_report` fixtures (each a
# full-repo AST scan) per worker and eating the whole consolidation win below.
pytestmark = pytest.mark.xdist_group("settings_liveness")

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "settings_liveness"
ARTIFACT = REPO_ROOT / sl.JSON_OUT_REL


@pytest.fixture(scope="module")
def model_fields() -> frozenset[str]:
    return sl.load_model_fields()


def _reads(model_fields: frozenset[str], *names: str) -> list[sl.Read]:
    """Run the classifier over the named fixture module(s) only."""
    files = [f"{n}.py" for n in names]
    return sl.analyze(str(FIXTURES), model_fields, files=files).reads


def _rules_for(model_fields: frozenset[str], name: str, key: str) -> list[str]:
    """Capture rules for the single read of ``key`` in fixture ``name``."""
    hits = [r for r in _reads(model_fields, name) if r.key == key]
    assert len(hits) == 1, f"expected exactly one {key} read in {name}.py, got {hits}"
    return hits[0].rules


def _analysis(model_fields: frozenset[str], *names: str) -> sl.Analysis:
    return sl.analyze(str(FIXTURES), model_fields, files=[f"{n}.py" for n in names])


def _partition(model_fields: frozenset[str], *names: str) -> dict:
    return sl.partition(_analysis(model_fields, *names), model_fields)


# ===========================================================================
# One synthetic fixture per capture rule
# ===========================================================================
class TestCaptureRules:
    def test_module_level_assignment_captures(self, model_fields):
        assert _rules_for(model_fields, "cap_module_level", "KELLY_CAP") == ["module_level"]

    def test_import_time_discard_is_annotated_not_reclassified(self, model_fields):
        """``if not settings.X:`` at import is still a once-per-process read
        (so still ``module_level``), but nothing retains the value — flagged
        so a reviewer can see why that entry is conservative."""
        hit = next(r for r in _reads(model_fields, "cap_module_level") if r.key == "LOG_LEVEL")
        assert hit.rules == ["module_level"]
        assert hit.discarded is True
        # ...and the bound read in the same file is NOT flagged.
        bound = next(r for r in _reads(model_fields, "cap_module_level") if r.key == "KELLY_CAP")
        assert bound.discarded is False

    def test_class_body_assignment_captures(self, model_fields):
        assert _rules_for(model_fields, "cap_class_body", "KELLY_FRACTION") == ["class_body"]

    def test_frozen_dataclass_default_captures(self, model_fields):
        assert _rules_for(model_fields, "cap_class_body", "VOL_TARGET") == [
            "class_body",
            "frozen_dataclass_default",
        ]

    def test_decorator_argument_captures(self, model_fields):
        rules = _rules_for(model_fields, "cap_decorator_and_default", "HMM_N_STATES")
        assert "decorator_arg" in rules

    def test_default_argument_captures(self, model_fields):
        rules = _rules_for(model_fields, "cap_decorator_and_default", "PILOTS_TOP_N")
        assert "default_arg" in rules

    def test_self_assignment_in_init_captures(self, model_fields):
        assert _rules_for(model_fields, "cap_init", "MAX_LEVERAGE") == ["init_self_assign"]

    def test_local_read_in_init_captures(self, model_fields):
        assert _rules_for(model_fields, "cap_init", "MAX_CORRELATION") == ["init_body"]

    def test_indirect_init_helper_captures_with_provenance(self, model_fields):
        """The single most important shape: the read is NOT lexically inside
        ``__init__``, it is one call hop out, and its result is frozen into a
        long-lived attribute."""
        hit = next(
            r for r in _reads(model_fields, "cap_init") if r.key == "MARKET_DATA_PROVIDER"
        )
        assert hit.rules == ["indirect_init_helper_d1"]
        assert hit.via, "provenance must name the __init__ call line that reaches this read"

    def test_cross_module_init_helper_captures(self, model_fields):
        """The caller's ``__init__`` names neither key; the imported helper
        does. Both must still be attributed to the caller."""
        reads = _reads(model_fields, "cap_cross_module", "crossmod_helper")
        captured = {
            r.key for r in reads if "cross_module_init_helper" in r.rules
        }
        assert captured == {"DATABASE_URL", "DB_POOL_SIZE"}
        # The helper's own reads, in isolation, are fresh -- the capture comes
        # from the caller storing the result, not from the helper itself.
        assert all(
            r.rules == []
            for r in _reads(model_fields, "crossmod_helper")
        )

    def test_post_construction_self_assignment_captures(self, model_fields):
        """The gap an adversarial review found: a regular method storing a
        setting on ``self`` had no applicable rule at all (init_* needs
        __init__, indirect_* needs a constructor call chain, closure/global
        need a bare Name target) and was reported live_safe."""
        assert _rules_for(
            model_fields, "cap_method_self_assign", "SENTIMENT_MAX_DOCUMENTS_PER_CYCLE"
        ) == ["method_self_assign"]

    def test_self_assignment_is_seen_through_arbitrary_expressions(self, model_fields):
        """``self._x = time.monotonic() + float(settings.Y)`` — the read is
        wrapped in a coercion nested inside a BinOp. A whitelist-based walk
        loses it; asking "does the enclosing statement assign to self?" does
        not."""
        assert _rules_for(
            model_fields,
            "cap_method_self_assign",
            "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",
        ) == ["method_self_assign"]

    def test_method_local_read_is_not_a_capture(self, model_fields):
        """The matching false positive: a read that is returned rather than
        stored must stay fresh."""
        assert (
            _rules_for(
                model_fields, "cap_method_self_assign", "SENTIMENT_INGESTION_LOOKBACK_DAYS"
            )
            == []
        )

    def test_global_assignment_captures(self, model_fields):
        assert _rules_for(model_fields, "cap_global_assign", "SNAPSHOT_HISTORY_DAYS") == [
            "global_assign"
        ]

    @pytest.mark.parametrize(
        "key", ["KELLY_CAP", "VOL_TARGET", "MAX_CORRELATION"], ids=["returned", "attribute", "registrar"]
    )
    def test_escaping_closure_captures(self, model_fields, key):
        assert _rules_for(model_fields, "cap_closure", key) == ["closure_value"]

    def test_non_escaping_worker_closure_does_not_capture(self, model_fields):
        """The false positive the closure rule must not produce: a worker
        closure that dies with its own call frame captures nothing for the
        process lifetime. If this ever fails, every ThreadPoolExecutor
        fan-out in the codebase starts reporting spurious captures."""
        assert _rules_for(model_fields, "cap_closure", "MAX_LEVERAGE") == []

    def test_memoized_function_and_cached_property_capture(self, model_fields):
        assert _rules_for(model_fields, "cap_memoized", "FINBERT_BATCH_SIZE") == [
            "memoized_singleton"
        ]
        assert _rules_for(model_fields, "cap_memoized", "NEWS_LOOKBACK_DAYS") == [
            "memoized_singleton"
        ]

    @pytest.mark.parametrize(
        "key",
        ["LLM_COMMENTARY_TIMEOUT_SECONDS", "RATIONALE_VERBOSITY", "ALERT_CHANNELS"],
        ids=["os.getenv", "os.environ.get", "os.environ[]"],
    )
    def test_os_environ_reads_are_never_live_patchable(self, model_fields, key):
        hit = next(r for r in _reads(model_fields, "cap_os_environ") if r.key == key)
        assert hit.form == "os_environ"
        assert hit.rules == ["os_environ"]

    @pytest.mark.parametrize(
        "key",
        ["KELLY_CAP", "KELLY_FRACTION", "VOL_TARGET", "DRY_RUN", "BARS_BACKFILL_DAYS"],
        ids=["function", "lambda", "lambda-in-dict", "property", "method"],
    )
    def test_fresh_reads_have_no_rules(self, model_fields, key):
        """Includes the two lambda shapes gui/help_content.py was refactored
        into, so a regression there would show up as KELLY_CAP going
        restart_required rather than silently."""
        assert _rules_for(model_fields, "fresh_reads", key) == []

    def test_alias_forms_all_resolve(self, model_fields):
        keys = {r.key for r in _reads(model_fields, "alias_forms")}
        assert keys == {"MAX_ORDER_RATE_PER_MIN", "MAX_PORTFOLIO_HEAT", "RISK_FREE_RATE"}

    def test_guard_factory_constant_resolves_and_is_fresh(self, model_fields):
        """The read site is a dynamic getattr, but the factory CALL passes a
        string constant — so the key IS statically knowable. Without this rule
        a live bearer token would be reported as never read at all."""
        part = _partition(model_fields, "factory_fresh")
        assert "DRY_RUN" in part["live_safe"]

    def test_guard_factory_with_memoized_inner_is_captured(self, model_fields):
        """Same shape, but @lru_cache freezes the first call's value. The
        inner function's own rules must ride along on the factory_param read;
        checking only the outer call site would report this live-safe."""
        hit = next(r for r in _reads(model_fields, "factory_memoized") if r.key == "LOG_LEVEL")
        assert hit.form == "factory_param"
        assert hit.rules == ["memoized_singleton"]
        part = _partition(model_fields, "factory_memoized")
        assert "LOG_LEVEL" in part["restart_required"]

    def test_factory_resolved_dynamic_site_does_not_poison(self, model_fields):
        """A memoized factory read is attributed (above) AND is a dynamic read
        in a capture context. It must not do both — poisoning there would
        double-count one understood fact and collapse the whole report."""
        part = _partition(model_fields, "factory_memoized")
        assert part["poisoned_dynamic_sites"] == []
        assert part["counts"]["live_safe"] == 0, (
            "factory_memoized.py has exactly one read; every other field is no_op"
        )

    def test_dynamic_read_in_capture_context_poisons_every_key(self, model_fields):
        """Unattributable AND capturing: no field can honestly be called
        live_safe or no_op while this exists."""
        part = _partition(model_fields, "dynamic_captured")
        assert part["poisoned_dynamic_sites"], "expected the import-time dynamic read to poison"
        assert part["live_safe"] == []
        assert part["no_op"] == []
        assert part["counts"]["restart_required"] == len(model_fields)

    def test_name_literal_only_field_is_not_reported_no_op(self, model_fields):
        """Zero attributable reads, but the name is right there feeding a
        name-driven dispatcher. Calling it no_op ("does nothing, ever") would
        be a lie, so it fails closed."""
        part = _partition(model_fields, "name_literal_only")
        key = "MULTIFACTOR_MICROCAP_THRESHOLD"
        assert key not in part["no_op"]
        assert key in part["restart_required"]
        assert part["restart_required"][key][0]["rules"] == [
            "dynamic_name_literal_unattributable"
        ]
        assert key in part["restart_required_via_name_literal_only"]

    def test_settings_snapshot_aborts_the_whole_run(self, model_fields):
        """A snapshot detaches every field at once, so a partial per-key
        answer would be worse than none."""
        with pytest.raises(sl.UnresolvedAnalysis, match="snapshot"):
            _analysis(model_fields, "snapshot")

    def test_unparseable_file_aborts_the_whole_run(self, model_fields, tmp_path):
        (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
        with pytest.raises(sl.UnresolvedAnalysis):
            sl.analyze(str(tmp_path), model_fields, files=["broken.py"])


class TestEveryCaptureRuleIsExercised:
    """Meta-guard: adding a capture rule without a fixture fails here."""

    _ALL_RULES = {
        "module_level",
        "class_body",
        "frozen_dataclass_default",
        "decorator_arg",
        "default_arg",
        "init_self_assign",
        "init_body",
        "method_self_assign",
        "indirect_init_helper_d1",
        "cross_module_init_helper",
        "global_assign",
        "closure_value",
        "memoized_singleton",
        "os_environ",
    }

    def test_fixtures_cover_every_rule(self, model_fields):
        # One combined run: cap_cross_module.py only resolves its helper when
        # crossmod_helper.py is in the same analysis, exactly as on the real
        # tree. snapshot.py is excluded because it raises by design.
        names = sorted(p.stem for p in FIXTURES.glob("*.py") if p.stem != "snapshot")
        seen: set[str] = set()
        for read in _reads(model_fields, *names):
            seen.update(read.rules)
        missing = self._ALL_RULES - seen
        assert not missing, f"capture rules with no fixture coverage: {sorted(missing)}"

    def test_rule_set_has_not_silently_grown(self, model_fields):
        """The inverse direction: a rule this test does not know about means
        the fixture suite is behind. ``indirect_init_helper_dN`` is built by
        an f-string, so it is matched on its stem."""
        source = (REPO_ROOT / "scripts" / "settings_liveness.py").read_text(encoding="utf-8")
        for rule in self._ALL_RULES:
            needle = "indirect_init_helper_d" if rule.startswith("indirect_init_helper_d") else rule
            assert needle in source, f"{rule} is asserted here but no longer emitted"


# ===========================================================================
# Real-tree regressions — the five cases actually got wrong while prototyping
# ===========================================================================
@pytest.fixture(scope="module")
def real_analysis(model_fields):
    return sl.analyze(str(REPO_ROOT), model_fields)


@pytest.fixture(scope="module")
def real_report(model_fields, real_analysis) -> dict:
    return sl.build_report(str(REPO_ROOT), model_fields, analysis=real_analysis)


class TestRealTreeRegressions:
    def test_market_data_provider_is_captured_indirectly(self, real_report):
        """data/market_data.py's CompositeProvider.__init__ does
        ``self._quote_provider = self._select_quote_provider()``; the read of
        MARKET_DATA_PROVIDER lives inside that helper, not inside __init__. A
        local-__init__-only rule would report a frozen provider selection —
        which can be a database-shaped choice — as live-safe."""
        sites = real_report["restart_required"]["MARKET_DATA_PROVIDER"]
        assert any(
            s["site"].startswith("data/market_data.py:")
            and any(r.startswith("indirect_init_helper_d") for r in s["rules"])
            and s["via"]
            for s in sites
        ), sites

    def test_fmp_analyst_enabled_is_live_safe_via_constant_getattr(self, real_report):
        """Zero ``settings.X`` reads — reached only by
        ``getattr(settings, "FMP_ANALYST_ENABLED", ...)``. An attribute-only
        walker would report this field unread."""
        assert "FMP_ANALYST_ENABLED" in real_report["live_safe"]

    @pytest.mark.parametrize(
        "key", ["FMP_QUOTES_ENABLED", "FMP_BARS_ENABLED", "FMP_FUNDAMENTALS_ENABLED"]
    )
    def test_fmp_capability_gates_are_wired_and_live_safe(self, real_report, key):
        """These were genuinely ``no_op`` before data/market_data.py's
        ``_effective_*_provider`` properties landed. A regression to ``no_op``
        means the wiring was lost."""
        assert key not in real_report["no_op"]
        assert key in real_report["live_safe"]

    @pytest.mark.parametrize(
        "key", ["DATABASE_URL", "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "MCP_DATABASE_URL_RO"]
    )
    def test_db_engine_settings_are_captured_cross_module(self, real_report, key):
        """HistoricalStore/TransactionsStore/SectorCorrelationStore/
        RunHistoryStore each call db_config.py's engine builders from their own
        __init__; the DSN and pool sizing are baked into a long-lived Engine."""
        assert key in real_report["restart_required"]
        assert any(
            "cross_module_init_helper" in s["rules"]
            for s in real_report["restart_required"][key]
        )

    def test_command_token_guard_factories_resolve_to_real_fields(
        self, real_analysis
    ):
        """api/auth.py resolves the bearer token by NAME at request time. A
        classifier that concluded these fields are never read would be badly
        wrong about a security control.

        Asserts the resolved FIELD NAMES, not just a count: a bug in
        ``_factory_owning`` or the form-4 value extraction that attributed
        these reads to the wrong fields would leave any count unchanged.
        """
        resolved = {r.key for r in real_analysis.reads if r.form == "factory_param"}
        assert resolved == {
            "ORCHESTRATOR_DAEMON_TOKEN",
            "FOLLOW_API_TOKEN",
            "AI_GENERATION_API_ENABLED",
            "UNIVERSE_SYNC_ENABLED",
        }, resolved

    def test_every_factory_call_site_passes_a_string_constant(
        self, real_analysis
    ):
        """Backs the caveat that claims it. A factory called with a NON-constant
        key would go unattributed AND (by design) no longer poison — so if that
        ever appears, the caveat is no longer true and this must be revisited."""
        factory_sites = [d for d in real_analysis.dynamic if d.get("resolved_by_factory")]
        resolved_count = sum(1 for r in real_analysis.reads if r.form == "factory_param")
        assert factory_sites, "expected at least one factory-keyed dynamic read"
        assert resolved_count >= 2 * len(factory_sites), (
            "a guard factory exists whose call sites did not all resolve to a "
            f"string constant: {factory_sites}"
        )

    def test_no_dynamic_site_poisons_the_real_tree(self, real_report):
        """api/auth.py's ``_guard`` and api/data_api.py's ``_dependency`` are
        nested functions produced by a factory CALLED at module scope. An
        import-time-based capture rule would mark both captured and fail all
        dynamic sites closed — deleting the ability to ever report a
        dynamically-keyed setting as live-safe."""
        assert real_report["poisoned_dynamic_sites"] == []
        by_site = {(d["file"], d["line"]): d for d in real_report["dynamic_sites"]}
        auth = [d for (f, _), d in by_site.items() if f == "api/auth.py"]
        data_api = [d for (f, _), d in by_site.items() if f == "api/data_api.py"]
        assert auth and all(d["rules"] == [] for d in auth)
        assert data_api and all(d["rules"] == [] for d in data_api)

    def test_kelly_tunables_are_live_safe(self, real_report):
        """gui/help_content.py's settings reads were made lazy (zero-arg
        callables) specifically so these would not be captured at import."""
        assert "KELLY_CAP" in real_report["live_safe"]
        assert "KELLY_FRACTION" in real_report["live_safe"]

    def test_every_field_lands_in_exactly_one_bucket(self, real_report, model_fields):
        live = set(real_report["live_safe"])
        restart = set(real_report["restart_required"])
        no_op = set(real_report["no_op"])
        assert live | restart | no_op == set(model_fields)
        assert not (live & restart) and not (live & no_op) and not (restart & no_op)

    def test_fixtures_never_leak_into_the_real_tree_run(self, real_report):
        """tests/ is skipped by the file walk; if that ever changed, the
        synthetic poison fixture would silently collapse the real report."""
        for site in real_report["dynamic_sites"]:
            assert not site["file"].startswith("tests/")


class TestCommittedArtifactIsFresh:
    def test_committed_json_matches_a_fresh_run(self, real_report):
        assert ARTIFACT.exists(), (
            f"{sl.JSON_OUT_REL} is missing — regenerate with "
            f"`python3 scripts/settings_liveness.py --write`"
        )
        committed = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        assert committed == real_report, (
            f"{sl.JSON_OUT_REL} is stale. Re-run "
            f"`python3 scripts/settings_liveness.py --write` and commit the result."
        )
