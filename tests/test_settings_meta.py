"""
tests/test_settings_meta.py
===========================
Unit tests for ``pilots/settings_meta.py`` — the shared per-field liveness /
safety metadata helper behind all five ``/settings/*`` editors in
``api/pilots_api.py``.

The endpoint-level wiring is covered in ``tests/test_pilots_api_tunables.py``.
This file covers the module's own logic in isolation, concentrating on the two
properties that make the whole feature trustworthy rather than merely present:

1. **It never claims a change will apply live unless it actually can.** The
   static classification is a necessary but NOT sufficient condition — a writer
   must also exist — and the two are ANDed.
2. **It never raises and never fabricates.** A missing/corrupt/wrong-shaped
   artifact degrades to "needs a restart" with no invented capture sites.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

import pilots.settings_meta as settings_meta


@pytest.fixture(autouse=True)
def _clear_cache():
    settings_meta.reset_cache()
    yield
    settings_meta.reset_cache()


def _artifact(tmp_path, payload: dict):
    p = tmp_path / "settings_liveness.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


_SAMPLE = {
    "live_safe": ["LIVE_ONE", "LIVE_TWO"],
    "no_op": ["DEAD_ONE"],
    "restart_required": {
        "PINNED_ONE": [
            {"site": "processing_engine.py:36", "rules": ["module_level"]},
            {"site": "other.py:99", "rules": ["init_body"]},
        ],
        "NO_PROSE": [{"site": "weird.py:1", "rules": ["some_unknown_rule"]}],
    },
}


# ---------------------------------------------------------------------------
# Artifact parsing
# ---------------------------------------------------------------------------


class TestLoadLiveness:
    def test_parses_the_three_buckets(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert d["live_safe"] == frozenset({"LIVE_ONE", "LIVE_TWO"})
        assert d["no_op"] == frozenset({"DEAD_ONE"})
        assert set(d["restart_required"]) == {"PINNED_ONE", "NO_PROSE"}
        assert d["loaded"] is True

    def test_missing_file_degrades_never_raises(self, tmp_path):
        d = settings_meta.load_liveness(path=tmp_path / "nope.json")
        assert d["loaded"] is False
        assert d["live_safe"] == frozenset()

    def test_corrupt_json_degrades_never_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert settings_meta.load_liveness(path=p)["loaded"] is False

    def test_wrong_top_level_type_degrades(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert settings_meta.load_liveness(path=p)["loaded"] is False

    def test_one_broken_bucket_does_not_discard_the_others(self, tmp_path):
        """Dead-letter per bucket, matching this repo's per-key resilience
        convention — a malformed `restart_required` must not cost us the
        `live_safe` list we could read perfectly well."""
        p = _artifact(tmp_path, {"live_safe": ["A"], "restart_required": "not-a-dict"})
        d = settings_meta.load_liveness(path=p)
        assert d["live_safe"] == frozenset({"A"})
        assert d["restart_required"] == {}


# ---------------------------------------------------------------------------
# Classification / capture sites / reasons
# ---------------------------------------------------------------------------


class TestClassification:
    def test_each_bucket_reports_its_own_classification(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert settings_meta.classification("LIVE_ONE", data=d) == "live_safe"
        assert settings_meta.classification("DEAD_ONE", data=d) == "no_op"
        assert settings_meta.classification("PINNED_ONE", data=d) == "restart_required"

    def test_an_unmentioned_field_is_unknown(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert settings_meta.classification("WHO", data=d) == settings_meta.CLASSIFICATION_UNKNOWN

    def test_unknown_maps_to_needs_restart_not_to_applies_immediately(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert (
            settings_meta.applies_for("WHO", pinned=frozenset(), data=d)
            == settings_meta.APPLIES_NEXT_RESTART
        )


class TestCaptureSites:
    def test_live_safe_field_reports_empty_list_not_none(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        sites = settings_meta.capture_sites("LIVE_ONE", data=d)
        assert sites == []
        assert sites is not None

    def test_restart_required_field_reports_its_sites_in_order(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert settings_meta.capture_sites("PINNED_ONE", data=d) == [
            "processing_engine.py:36",
            "other.py:99",
        ]

    def test_duplicate_sites_are_collapsed(self, tmp_path):
        p = _artifact(
            tmp_path,
            {
                "live_safe": [],
                "no_op": [],
                "restart_required": {
                    "D": [
                        {"site": "a.py:1", "rules": ["module_level"]},
                        {"site": "a.py:1", "rules": ["module_level"]},
                    ]
                },
            },
        )
        d = settings_meta.load_liveness(path=p)
        assert settings_meta.capture_sites("D", data=d) == ["a.py:1"]


class TestRestartReason:
    def test_none_for_a_field_that_needs_no_restart(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert settings_meta.restart_reason("LIVE_ONE", data=d) is None
        assert settings_meta.restart_reason("DEAD_ONE", data=d) is None

    def test_names_the_real_capture_site(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        reason = settings_meta.restart_reason("PINNED_ONE", data=d)
        assert "processing_engine.py:36" in reason

    def test_a_site_with_no_known_rule_says_so_instead_of_guessing(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        reason = settings_meta.restart_reason("NO_PROSE", data=d)
        assert reason
        assert "captured this value" in reason

    def test_unreadable_artifact_admits_the_gap_rather_than_inventing_a_site(self, tmp_path):
        d = settings_meta.load_liveness(path=tmp_path / "missing.json")
        reason = settings_meta.restart_reason("ANYTHING", data=d)
        assert "could not be read" in reason

    def test_a_field_absent_from_a_loaded_artifact_is_not_told_the_report_is_unreadable(
        self, tmp_path
    ):
        """The regression this guards: an artifact that parsed FINE but simply
        has never mentioned this field (e.g. a tunable added to an editor
        since the last `--write` run) was reporting the exact same "the
        report could not be read" sentence as a genuinely corrupt/missing
        file -- false, since every other field on the same request classifies
        correctly from the same data. The two situations need different
        prose."""
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert d["loaded"] is True
        reason = settings_meta.restart_reason("A_BRAND_NEW_FIELD", data=d)
        assert "could not be read" not in reason
        assert "not listed in the settings-liveness report" in reason
        assert "settings_liveness.py --write" in reason


# ---------------------------------------------------------------------------
# The live-apply AND, and the env-pin downgrade
# ---------------------------------------------------------------------------


class TestAppliesFor:
    def test_live_safe_applies_immediately_when_a_writer_exists(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert (
            settings_meta.applies_for("LIVE_ONE", pinned=frozenset(), data=d, live_apply=True)
            == settings_meta.APPLIES_IMMEDIATELY
        )

    def test_live_safe_needs_a_restart_when_no_writer_exists(self, tmp_path):
        """The crux. `live_safe` alone does NOT mean a change is observed — it
        means nothing captures the value. Without a writer the change only
        reaches .env, so claiming `immediately` would be a false promise."""
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert (
            settings_meta.applies_for("LIVE_ONE", pinned=frozenset(), data=d, live_apply=False)
            == settings_meta.APPLIES_NEXT_RESTART
        )

    def test_no_effect_is_unchanged_by_the_writer_being_absent(self, tmp_path):
        """A field nothing reads does nothing either way — downgrading it to
        `next_daemon_restart` would imply a restart makes it work."""
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        for live_apply in (True, False):
            assert (
                settings_meta.applies_for(
                    "DEAD_ONE", pinned=frozenset(), data=d, live_apply=live_apply
                )
                == settings_meta.APPLIES_NO_EFFECT
            )

    def test_env_pin_wins_over_every_classification(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        for key in ("LIVE_ONE", "DEAD_ONE", "PINNED_ONE", "UNKNOWN_KEY"):
            assert (
                settings_meta.applies_for(key, pinned=frozenset({key}), data=d)
                == settings_meta.APPLIES_ENV_PINNED
            )

    def test_pin_matching_is_case_insensitive(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert (
            settings_meta.applies_for("LIVE_ONE", pinned=frozenset({"LIVE_ONE"}), data=d)
            == settings_meta.APPLIES_ENV_PINNED
        )


class TestLiveApplyAvailable:
    def test_reports_false_when_the_writer_module_is_absent(self):
        with mock.patch.object(settings_meta, "WRITER_MODULE", "definitely_not_a_module_xyz"):
            assert settings_meta.live_apply_available() is False

    def test_reports_true_for_a_module_that_does_exist(self):
        with mock.patch.object(settings_meta, "WRITER_MODULE", "json"):
            assert settings_meta.live_apply_available() is True

    def test_never_raises_on_a_broken_import_system(self):
        with mock.patch("importlib.util.find_spec", side_effect=RuntimeError("boom")):
            assert settings_meta.live_apply_available() is False


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


class TestFieldMetadata:
    def test_shape(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        meta = settings_meta.field_metadata(
            "LIVE_ONE", pinned=frozenset(), stored=frozenset(), data=d
        )
        assert set(meta) == {
            "applies",
            "restart_reason",
            "capture_sites",
            "env_pinned",
            "dangerous",
            "source",
        }

    def test_source_reflects_runtime_store_membership(self, tmp_path):
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        assert (
            settings_meta.field_metadata(
                "LIVE_ONE", pinned=frozenset(), stored=frozenset({"LIVE_ONE"}), data=d
            )["source"]
            == settings_meta.SOURCE_RUNTIME_STORE
        )
        assert (
            settings_meta.field_metadata(
                "LIVE_ONE", pinned=frozenset(), stored=frozenset(), data=d
            )["source"]
            == settings_meta.SOURCE_ENV_FILE
        )

    def test_live_safe_without_a_writer_explains_which_of_the_two_it_is(self, tmp_path):
        """A restart claim with no capture site would look like an unexplained
        contradiction. Say plainly that the field isn't captured but this build
        cannot apply it live."""
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        meta = settings_meta.field_metadata(
            "LIVE_ONE", pinned=frozenset(), stored=frozenset(), data=d, live_apply=False
        )
        assert meta["applies"] == settings_meta.APPLIES_NEXT_RESTART
        assert meta["capture_sites"] == []
        assert "no way to push a change into the live process" in meta["restart_reason"]

    def test_env_pinned_field_still_reports_its_capture_sites(self, tmp_path):
        """The pin is the immediate blocker, but the capture site is still true
        and still matters once the pin is removed."""
        d = settings_meta.load_liveness(path=_artifact(tmp_path, _SAMPLE))
        meta = settings_meta.field_metadata(
            "PINNED_ONE", pinned=frozenset({"PINNED_ONE"}), stored=frozenset(), data=d
        )
        assert meta["applies"] == settings_meta.APPLIES_ENV_PINNED
        assert meta["env_pinned"] is True
        assert meta["capture_sites"] == ["processing_engine.py:36", "other.py:99"]


class TestSummarizeApplies:
    def test_unanimous_screen_reports_that_state(self):
        out = settings_meta.summarize_applies(["immediately", "immediately"])
        assert out["applies"] == "immediately"
        assert out["applies_counts"]["immediately"] == 2

    def test_disagreement_reports_mixed(self):
        out = settings_meta.summarize_applies(["immediately", "next_daemon_restart"])
        assert out["applies"] == "mixed"

    def test_empty_screen_reports_the_conservative_state(self):
        assert settings_meta.summarize_applies([])["applies"] == settings_meta.APPLIES_NEXT_RESTART

    def test_counts_cover_every_state_even_at_zero(self):
        counts = settings_meta.summarize_applies(["immediately"])["applies_counts"]
        assert set(counts) == set(settings_meta.APPLIES_STATES)
        assert counts["no_effect"] == 0


class TestPerRequestFactsAreNeverCached:
    def test_env_pinned_keys_delegates_on_every_call(self):
        with mock.patch(
            "runtime_flags.real_environment_keys", side_effect=[frozenset({"A"}), frozenset({"B"})]
        ) as m:
            assert settings_meta.env_pinned_keys() == frozenset({"A"})
            assert settings_meta.env_pinned_keys() == frozenset({"B"})
        assert m.call_count == 2

    def test_env_pinned_keys_degrades_rather_than_raising(self):
        with mock.patch("runtime_flags.real_environment_keys", side_effect=OSError("nope")):
            assert settings_meta.env_pinned_keys() == frozenset()

    def test_runtime_store_keys_returns_nothing_on_a_store_level_error(self):
        with mock.patch("runtime_flags.load_store", return_value=({"A": 1}, "corrupt")):
            assert settings_meta.runtime_store_keys() == frozenset()

    def test_runtime_store_keys_degrades_rather_than_raising(self):
        with mock.patch("runtime_flags.load_store", side_effect=OSError("nope")):
            assert settings_meta.runtime_store_keys() == frozenset()


class TestIsDangerous:
    def test_matches_the_real_keyset(self):
        from settings_keysets import DANGEROUS_KEYS

        assert settings_meta.is_dangerous("ADVISORY_ONLY") is True
        assert settings_meta.is_dangerous("KELLY_FRACTION") is False
        for key in DANGEROUS_KEYS:
            assert settings_meta.is_dangerous(key) is True


def test_module_stays_a_light_leaf_off_the_heavy_engines():
    """``pilots/`` modules must not drag a calculation engine onto the API read
    path (same guard as ``tests/test_pilots_forecast_skill.py``)."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(settings_meta.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "processing_engine",
        "strategy_engine",
        "forecasting_engine",
        "macro_engine",
        "technical_options_engine",
        "main_orchestrator",
        "pandas",
        "numpy",
    }
    assert not (imported & forbidden), imported & forbidden

    # It must also never import `settings` — `runtime_flags` is imported BY
    # settings.py, so a cycle here would break `import settings` app-wide.
    assert "settings" not in imported
