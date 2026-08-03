"""
tests/test_settings_keysets.py
==============================
Structural tests for ``settings_keysets.py`` — the committed ``BOOTSTRAP_KEYS``
/ ``DANGEROUS_KEYS`` constants for the runtime settings store.

These deliberately do NOT test "the set contains what I typed". Each class
pins a property that would be violated by a *real* change somewhere else in
the tree:

``TestNamesAreRealFields``
    Every name resolves to a live ``Settings.model_fields`` entry. A typo, or
    a field renamed/removed in ``settings.py``, fails here instead of silently
    classifying nothing.

``TestModuleIsADependencyFreeLeaf``
    AST-asserts the module imports nothing but ``__future__``. This is the
    load-bearing constraint: a later ``runtime_flags.py`` will be a stdlib-only
    leaf imported BY ``settings.py`` and will import ``BOOTSTRAP_KEYS`` from
    here, so a single ``from settings import settings`` added to
    ``settings_keysets.py`` would create an import cycle at that point. Cheap
    to assert now, expensive to discover later.

``TestSetRelationships``
    The partition properties — including the two overlaps that are EXPECTED,
    pinned to their exact contents so a new one cannot appear by accident.

``TestExistingEditorsAreNotBootstrap``
    None of the 133 keys the five shipped ``api/pilots_api.py`` editors expose
    is bootstrap-only. True today; pinned going forward.

``TestLivenessCrossReference``
    Cross-checks every ``BOOTSTRAP_KEYS`` field against
    ``docs/settings_liveness.json``. Two fields disagree, knowingly — the
    expectation is per-key and explicit, so the disagreement is asserted
    rather than suppressed, and a CHANGE in either direction fails.

``TestHandSetMarkerDrift``
    Re-derives the "never GUI-writable / hand-set in .env only" marker set
    from the CURRENT ``settings.py`` and asserts it equals
    ``HAND_SET_ONLY_KEYS``, then cross-checks the committed census artifact
    (which has its own freshness guard,
    ``tests/test_measure_settings_census.py::TestCommittedArtifactIsFresh``)
    as a secondary confirmation. This is the test that fires when someone
    adds a hand-set-only field to ``settings.py`` and forgets this module.

``TestDangerousKeysAlreadyExposedByShippedEditors``
    Pins the exact set of ``DANGEROUS_KEYS`` that ALREADY have a live,
    shipped, un-confirmed write path. Not a bug to fix here — a finding to
    keep visible until the confirmation UI lands.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import settings_keysets as ks
from settings import Settings
import gui.env_io as env_io
import api.pilots_api as pilots_api


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "settings_keysets.py"
LIVENESS_JSON = REPO_ROOT / "docs" / "settings_liveness.json"
CENSUS_JSON = REPO_ROOT / "docs" / "settings_field_census.json"

MODEL_FIELDS = set(Settings.model_fields)

# The five independently-shipped scoped settings editors in api/pilots_api.py.
# Each already has its own GET/PUT pair and its own write-permission gate.
EDITOR_INDEXES = {
    "_TUNABLE_INDEX": pilots_api._TUNABLE_INDEX,
    "_SENTIMENT_INDEX": pilots_api._SENTIMENT_INDEX,
    "_SECTOR_SELECTION_INDEX": pilots_api._SECTOR_SELECTION_INDEX,
    "_FMP_INDEX": pilots_api._FMP_INDEX,
    "_ETF_TRANSMISSION_INDEX": pilots_api._ETF_TRANSMISSION_INDEX,
}
ALL_EDITOR_KEYS = set().union(*(set(idx) for idx in EDITOR_INDEXES.values()))


def _liveness_status(payload: dict, key: str) -> str:
    """Return ``"live_safe"`` / ``"restart_required"`` / ``"no_op"`` / ``"absent"``.

    ``docs/settings_liveness.json`` stores ``live_safe``/``no_op`` as lists and
    ``restart_required`` as a dict keyed by field name (its value is the list
    of capture sites), so membership has to be tested per-shape.
    """
    if key in payload["restart_required"]:
        return "restart_required"
    if key in payload["live_safe"]:
        return "live_safe"
    if key in payload["no_op"]:
        return "no_op"
    return "absent"


class TestNamesAreRealFields:
    """Every classified name must be a live ``Settings`` field."""

    @pytest.mark.parametrize(
        "set_name",
        ["BOOTSTRAP_KEYS", "DANGEROUS_KEYS", "HAND_SET_ONLY_KEYS", "SAFETY_CRITICAL_KEYS"],
    )
    def test_all_names_are_settings_fields(self, set_name):
        keys = getattr(ks, set_name)
        unknown = sorted(keys - MODEL_FIELDS)
        assert not unknown, (
            f"{set_name} names {len(unknown)} field(s) that do not exist in "
            f"Settings.model_fields: {unknown}. Either a typo, or settings.py "
            f"renamed/removed the field and settings_keysets.py was not updated "
            f"— a stale entry classifies nothing and fails silently in production."
        )

    def test_sets_are_non_empty_frozensets(self):
        for name in ("BOOTSTRAP_KEYS", "DANGEROUS_KEYS", "HAND_SET_ONLY_KEYS", "SAFETY_CRITICAL_KEYS"):
            value = getattr(ks, name)
            assert isinstance(value, frozenset), f"{name} must be a frozenset, got {type(value)}"
            assert value, f"{name} is empty"

    def test_dangerous_keys_is_exactly_the_union_of_its_two_sources(self):
        assert ks.DANGEROUS_KEYS == ks.HAND_SET_ONLY_KEYS | ks.SAFETY_CRITICAL_KEYS

    def test_every_classified_key_carries_a_written_reason(self):
        """The reason strings are not decoration — the confirmation UI will
        show them, and requiring one per key is what stops a key being added
        to either set without anyone stating why."""
        assert set(ks.BOOTSTRAP_KEY_REASONS) == ks.BOOTSTRAP_KEYS
        assert set(ks.SAFETY_CRITICAL_KEY_REASONS) == ks.SAFETY_CRITICAL_KEYS
        for key, reason in {**ks.BOOTSTRAP_KEY_REASONS, **ks.SAFETY_CRITICAL_KEY_REASONS}.items():
            assert isinstance(reason, str) and len(reason) > 60, (
                f"{key}'s reason is missing or too short to be an explanation: {reason!r}"
            )


class TestModuleIsADependencyFreeLeaf:
    """``settings_keysets.py`` must stay importable from inside ``settings.py``'s
    own import, because ``runtime_flags.py`` (imported BY ``settings.py``) will
    import ``BOOTSTRAP_KEYS`` from it."""

    def test_module_imports_nothing_but_future(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        offending = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module != "__future__":
                    offending.append(f"line {node.lineno}: from {node.module} import ...")
            elif isinstance(node, ast.Import):
                offending.append(
                    f"line {node.lineno}: import {', '.join(a.name for a in node.names)}"
                )
        assert not offending, (
            "settings_keysets.py must import nothing but __future__ — it is a leaf "
            "that runtime_flags.py (itself imported by settings.py) will import, so "
            "any import of settings/gui.env_io/third-party here becomes an import "
            f"cycle or a hard dependency at settings-import time. Found: {offending}"
        )


class TestSetRelationships:
    """Partition properties, including the overlaps that are deliberate."""

    def test_bootstrap_and_dangerous_are_disjoint(self):
        """Orthogonal classifications that happen to be disjoint today.

        Nothing forbids a field being both (bootstrap-only AND requiring
        confirmation would just mean "the store refuses it, and any .env
        editor that offers it should confirm"). Pinned so that if the two ever
        do overlap, it is a decision someone made and documented here, not a
        collision nobody noticed.
        """
        overlap = sorted(ks.BOOTSTRAP_KEYS & ks.DANGEROUS_KEYS)
        assert not overlap, (
            f"BOOTSTRAP_KEYS and DANGEROUS_KEYS now overlap on {overlap}. That may "
            f"be correct — but decide it deliberately and update this test's "
            f"docstring with the reasoning before changing the assertion."
        )

    def test_secret_keys_and_dangerous_are_disjoint(self):
        """Deliberate: secrets are handled by a strictly stricter mechanism.

        ``SECRET_KEYS`` fields are masked on read and raise ``SecretWriteError``
        on write — they are not editable at all. Marking one DANGEROUS ("you
        may do this, but confirm it") would advertise a confirmable path to a
        value the write layer is going to refuse anyway. That is why the 8
        marker-carrying fields that are ALSO secrets are excluded from
        ``HAND_SET_ONLY_KEYS``.
        """
        overlap = sorted(set(env_io.SECRET_KEYS) & ks.DANGEROUS_KEYS)
        assert not overlap, (
            f"DANGEROUS_KEYS now includes credential(s) already covered by "
            f"SECRET_KEYS: {overlap}. Secrets are never editable, so a "
            f"confirmation gate on them is the wrong shape of protection."
        )

    def test_bootstrap_secret_overlap_is_exactly_the_two_db_dsns(self):
        """BOOTSTRAP_KEYS ∩ SECRET_KEYS is NOT empty, and should not be.

        The two classifications answer different questions about different
        mechanisms: ``SECRET_KEYS`` governs the ``.env`` writer, ``BOOTSTRAP_KEYS``
        governs the runtime store. Both DB DSNs may embed credentials (secret)
        AND would split the process across two databases if overridden live
        (bootstrap). Pinned exactly so a third overlap has to be justified.
        """
        assert sorted(ks.BOOTSTRAP_KEYS & set(env_io.SECRET_KEYS)) == [
            "DATABASE_URL",
            "MCP_DATABASE_URL_RO",
        ]

    def test_hand_set_only_keys_are_absent_from_both_env_io_write_lists(self):
        """Source (a)'s whole premise: these fields are hand-set in .env only.

        If one shows up in ALLOWED_KEYS, the marker comment in settings.py has
        gone stale and the census's ``comment_claim_holds`` would be False —
        this catches that from the other direction.
        """
        allowed = set(env_io.ALLOWED_KEYS)
        secret = set(env_io.SECRET_KEYS)
        assert not (ks.HAND_SET_ONLY_KEYS & allowed), sorted(ks.HAND_SET_ONLY_KEYS & allowed)
        assert not (ks.HAND_SET_ONLY_KEYS & secret), sorted(ks.HAND_SET_ONLY_KEYS & secret)


class TestExistingEditorsAreNotBootstrap:
    """No shipped editor exposes a bootstrap-only field."""

    def test_editor_indexes_have_the_expected_sizes(self):
        """Guard the guard: if an index were renamed or emptied, the real
        invariant below would pass vacuously."""
        assert {name: len(idx) for name, idx in EDITOR_INDEXES.items()} == {
            # 46 -> 49: the "RLHF Calibration" _TUNABLE_GROUPS entry added
            # RLHF_CALIBRATION_AUTO_APPROVE_ENABLED/_CONFIDENCE_THRESHOLD/
            # _AUTO_EXPORT_SFT_ENABLED.
            "_TUNABLE_INDEX": 49,
            "_SENTIMENT_INDEX": 33,
            "_SECTOR_SELECTION_INDEX": 11,
            "_FMP_INDEX": 24,
            "_ETF_TRANSMISSION_INDEX": 19,
        }
        assert len(ALL_EDITOR_KEYS) == 136

    def test_no_editor_exposes_a_bootstrap_key(self):
        offenders = {
            name: sorted(set(idx) & ks.BOOTSTRAP_KEYS)
            for name, idx in EDITOR_INDEXES.items()
            if set(idx) & ks.BOOTSTRAP_KEYS
        }
        assert not offenders, (
            f"A shipped pilots_api editor now exposes a bootstrap-only field: "
            f"{offenders}. Either the field does not belong in BOOTSTRAP_KEYS, or "
            f"the editor should not be serving it — resolve, do not relax."
        )


class TestLivenessCrossReference:
    """Cross-check against ``docs/settings_liveness.json``.

    That artifact has its own freshness guard
    (``tests/test_settings_liveness.py::TestCommittedArtifactIsFresh`` re-runs
    the classifier and diffs), so reading the committed file here is safe.
    """

    # Expected liveness status per BOOTSTRAP key. The default is
    # "restart_required"; the two exceptions are stated with their reason
    # rather than skipped, because a bootstrap key flipping status in EITHER
    # direction is information someone should look at.
    EXPECTED = {
        "OUTPUT_DIR": "restart_required",
        "DATABASE_URL": "restart_required",
        "MCP_DATABASE_URL_RO": "restart_required",
        "DB_POOL_SIZE": "restart_required",
        "DB_MAX_OVERFLOW": "restart_required",
        # --- knowing disagreement, investigated, NOT a bug in either analysis ---
        # scripts/settings_liveness.py classifies a read by whether a LATER
        # read would observe a setattr. Both port reads in
        # desktop/orchestrator_daemon.py sit in an ordinary function body, so
        # the classifier's answer ("a fresh read happens") is correct on its
        # own terms. What it does not model — and does not claim to; its own
        # caveat list is explicit that it analyses read SITES — is that the
        # value's CONSUMPTION is a one-shot irreversible side effect: uvicorn
        # binds the socket once and never re-reads. Meanwhile
        # gui/daemon_client._base_url() genuinely does re-read the port on
        # every call, and api/pilots_api.py drives that client. So a live
        # override does not merely fail to take effect; it desynchronises the
        # server, the client, and output/daemon.json inside one process.
        # Neither analysis is buggy — BOOTSTRAP_KEYS is answering a question
        # the liveness classifier does not ask.
        "ORCHESTRATOR_API_PORT": "live_safe",
        "PILOTS_API_PORT": "live_safe",
    }

    @staticmethod
    @pytest.fixture(scope="class")
    def liveness():
        return json.loads(LIVENESS_JSON.read_text(encoding="utf-8"))

    def test_expectation_table_covers_every_bootstrap_key(self):
        assert set(self.EXPECTED) == ks.BOOTSTRAP_KEYS, (
            "Every BOOTSTRAP_KEYS field needs an explicit expected liveness "
            "status here — that is what makes the two known live_safe entries a "
            "recorded decision rather than a silently-relaxed assertion."
        )

    def test_bootstrap_keys_have_the_expected_liveness_status(self, liveness):
        actual = {k: _liveness_status(liveness, k) for k in sorted(ks.BOOTSTRAP_KEYS)}
        assert actual == self.EXPECTED, (
            "A BOOTSTRAP_KEYS field changed liveness classification. Investigate "
            "before updating this table: a field moving restart_required -> "
            "live_safe means a capture site was removed, and a field moving "
            "live_safe -> restart_required may mean the port reasoning above no "
            "longer needs to be spelled out by hand."
        )

    def test_no_bootstrap_key_is_classified_dead(self, liveness):
        """A bootstrap key classified ``no_op`` ("nothing reads it") would mean
        the reasoning above rests on a read site that no longer exists."""
        dead = sorted(k for k in ks.BOOTSTRAP_KEYS if _liveness_status(liveness, k) == "no_op")
        assert not dead, f"BOOTSTRAP_KEYS fields with no reader at all: {dead}"


class TestHandSetMarkerDrift:
    """Source (a) must track ``settings.py``'s marker comments exactly.

    The authoritative check re-derives the markers from the CURRENT
    ``settings.py`` using the census script's own detector, rather than
    trusting the committed ``docs/settings_field_census.json`` outright.
    That artifact does now have its own freshness guard (see
    ``TestLivenessCrossReference`` above for the sibling pattern against
    ``docs/settings_liveness.json``), but re-deriving here keeps a
    marker-drift failure and a stale-census failure independently
    diagnosable instead of one test's fix masking the other's cause. The
    committed artifact is still cross-checked below as a secondary,
    redundant confirmation.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def live_markers():
        from scripts import measure_settings_census as census

        model_fields = Settings.model_fields
        parsed = census.parse_settings_source(model_fields)
        return census.collect_hand_set_markers(model_fields, env_io, parsed)

    def test_hand_set_only_keys_match_a_live_scan_of_settings_py(self, live_markers):
        derived = {
            row["field"]
            for row in live_markers["marked_fields"]
            if row["comment_claim_holds"] and not row["currently_in_secret_keys"]
        }
        assert derived == ks.HAND_SET_ONLY_KEYS, (
            "settings.py's 'never GUI-writable / hand-set in .env only' markers no "
            "longer match HAND_SET_ONLY_KEYS.\n"
            f"  in settings.py but not classified: {sorted(derived - ks.HAND_SET_ONLY_KEYS)}\n"
            f"  classified but no longer marked:   {sorted(ks.HAND_SET_ONLY_KEYS - derived)}\n"
            "A newly marked field means the platform author declared 'a GUI bug must "
            "never flip this on' — add it to HAND_SET_ONLY_KEYS."
        )

    def test_no_marker_contradictions_in_settings_py(self, live_markers):
        """A contradiction = a field claiming 'never GUI-writable' while sitting
        in ALLOWED_KEYS. Zero today; source (a) is only trustworthy while that
        holds."""
        assert live_markers["contradictions"] == [], live_markers["contradictions"]

    def test_committed_census_agrees_with_the_live_scan(self, live_markers):
        """Secondary check. A failure here with the live scan passing means
        ``docs/settings_field_census.json`` is stale, not that this module is
        wrong — regenerate with
        ``python3 scripts/measure_settings_census.py --write``."""
        census = json.loads(CENSUS_JSON.read_text(encoding="utf-8"))
        committed = {
            row["field"]
            for row in census["hand_set_markers"]["marked_fields"]
            if row["comment_claim_holds"] and not row["currently_in_secret_keys"]
        }
        assert committed == ks.HAND_SET_ONLY_KEYS, (
            "docs/settings_field_census.json disagrees with HAND_SET_ONLY_KEYS. If "
            "the live-scan test above passed, the committed census artifact is "
            "stale — regenerate it rather than editing settings_keysets.py."
        )


class TestDangerousKeysAlreadyExposedByShippedEditors:
    """THE FINDING this module exists to make visible.

    Five ``DANGEROUS_KEYS`` fields already have a live, shipped write path with
    no confirmation step. Wiring confirmation into those already-live editors
    is a separate task; this test's job is to keep the exact list honest and
    to fail loudly if a SIXTH is added before that lands.
    """

    ALREADY_EXPOSED = {
        "_TUNABLE_INDEX": ["ADVISORY_ONLY", "CORS_ALLOWED_ORIGINS", "DRY_RUN"],
        "_FMP_INDEX": ["FMP_BARS_ADJUSTMENT", "FMP_BARS_ENABLED"],
    }

    def test_exact_set_of_already_exposed_dangerous_keys(self):
        actual = {
            name: sorted(set(idx) & ks.DANGEROUS_KEYS)
            for name, idx in EDITOR_INDEXES.items()
            if set(idx) & ks.DANGEROUS_KEYS
        }
        assert actual == self.ALREADY_EXPOSED, (
            "The set of DANGEROUS_KEYS reachable through an ALREADY-SHIPPED, "
            "un-confirmed editor write path changed.\n"
            f"  expected: {self.ALREADY_EXPOSED}\n"
            f"  actual:   {actual}\n"
            "Growing this set means adding an un-gated live write path to a "
            "safety-critical field. Shrinking it means confirmation-gating landed "
            "— update this table and say so."
        )

    def test_hand_set_only_keys_are_exposed_by_no_editor(self):
        """Source (a) is the half of DANGEROUS_KEYS with no current exposure —
        the confirmation classification is pre-positioned for a future editor.
        If one of these ever appears in an editor, that is a much bigger
        finding than the five above (those are ALLOWED_KEYS tunables; these are
        fields settings.py says must never be GUI-writable at all)."""
        exposed = sorted(ks.HAND_SET_ONLY_KEYS & ALL_EDITOR_KEYS)
        assert not exposed, (
            f"A pilots_api editor now exposes hand-set-only field(s) {exposed}, "
            f"which settings.py explicitly marks as never GUI-writable. This "
            f"contradicts gui/env_io.py's EXCLUDED_FROM_GUI classification — treat "
            f"as a security regression, not a test to update."
        )
