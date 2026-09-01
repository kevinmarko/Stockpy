"""
tests/test_measure_settings_census.py
=======================================
Freshness guard for ``scripts/measure_settings_census.py``'s two committed
artifacts, ``docs/settings_field_census.json`` and
``docs/settings_field_census.md`` — mirrors
``tests/test_settings_liveness.py::TestCommittedArtifactIsFresh``, which
existed for the sibling ``docs/settings_liveness.json`` artifact but had no
counterpart for the census.

This is not a hypothetical gap: on 2026-08-03 the committed census sat at
``meta.git_commit e7e64529`` / ``total_fields 318`` while live
``Settings.model_fields`` had already grown to 320 (``FMP_OPTIONS_CONTEXT_ENABLED``
and ``FMP_PEERS_ENABLED`` were both missing from the committed file) — and
nothing detected it. This test is what would have caught that.

Two fields need explicit handling, not a blanket exclusion:

- ``meta.git_commit`` changes on every commit by construction. Comparing it
  literally would fail this test on every single commit that touches ANY
  file in the repo, including ones with zero effect on what the census
  measures — that is not staleness, so it is excluded from the diff (see
  ``_without_git_commit``).
- ``meta.repo_root`` used to bake in an ABSOLUTE path to whichever checkout
  (routinely a ``.claude/worktrees/...`` clone) generated the file, which
  made the committed artifact worktree-dependent and its diffs noisy across
  machines. Fixed at the source instead of worked around here:
  ``collect_census()`` no longer emits ``repo_root`` at all — grepping the
  tree before removing it turned up no reader of it anywhere.

``read_forms.files_scanned`` (like ``files_scanned`` in
``docs/settings_liveness.json``) increments whenever ANY new production
``.py`` file is added at repo root or in a scanned package. That is correct
behaviour, not a bug — but it does mean this test can fail on a PR that has
nothing to do with settings (exactly what happened when ``settings_keysets.py``
was added and required regenerating ``docs/settings_liveness.json``). Every
failure message below therefore states the fix plainly: re-run
``python3 scripts/measure_settings_census.py --write`` and commit the result
— a ten-second fix, not a sign anything is actually wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import measure_settings_census as census

REPO_ROOT = Path(__file__).resolve().parent.parent
JSON_ARTIFACT = census.JSON_OUT
MD_ARTIFACT = census.MD_OUT

_REGEN_HINT = (
    "Re-run `python3 scripts/measure_settings_census.py --write` and commit "
    "the result. This can fire on an otherwise-unrelated PR (e.g. one that "
    "merely adds a new top-level module or package file) since the census "
    "walks the whole production tree, not just settings.py -- that's "
    "expected, and the fix above takes about ten seconds."
)


def _without_git_commit(meta: dict) -> dict:
    """``git_commit`` changes every commit by design -- it is not a signal
    that the census itself is stale, so it is excluded from equality checks."""
    return {k: v for k, v in meta.items() if k != "git_commit"}


@pytest.fixture(scope="module")
def fresh_census() -> dict:
    return census.collect_census()


class TestCommittedArtifactIsFresh:
    def test_committed_json_matches_a_fresh_run(self, fresh_census):
        assert JSON_ARTIFACT.exists(), (
            f"{JSON_ARTIFACT.relative_to(REPO_ROOT)} is missing. {_REGEN_HINT}"
        )
        committed = json.loads(JSON_ARTIFACT.read_text(encoding="utf-8"))

        committed_meta = _without_git_commit(committed.get("meta", {}))
        fresh_meta = _without_git_commit(fresh_census.get("meta", {}))
        assert committed_meta == fresh_meta, (
            "docs/settings_field_census.json's meta block no longer matches a "
            "fresh run (git_commit is deliberately excluded from this "
            f"comparison -- see this file's module docstring). {_REGEN_HINT}"
        )

        committed_body = {k: v for k, v in committed.items() if k != "meta"}
        fresh_body = {k: v for k, v in fresh_census.items() if k != "meta"}
        assert committed_body == fresh_body, (
            f"docs/settings_field_census.json is stale. {_REGEN_HINT}"
        )

    def test_committed_md_matches_a_fresh_run(self, fresh_census):
        assert MD_ARTIFACT.exists(), (
            f"{MD_ARTIFACT.relative_to(REPO_ROOT)} is missing. {_REGEN_HINT}"
        )
        committed_json = json.loads(JSON_ARTIFACT.read_text(encoding="utf-8"))

        # docs/settings_field_census.md is a pure function of the same payload
        # (render_markdown(data)) plus the commit hash printed in its header.
        # Substitute the COMMITTED commit into a fresh payload before
        # rendering, so the only possible mismatch is genuine data/render
        # drift -- never "which commit happened to be HEAD when this ran".
        normalized = dict(fresh_census)
        normalized["meta"] = dict(fresh_census["meta"])
        normalized["meta"]["git_commit"] = committed_json.get("meta", {}).get("git_commit")
        fresh_md = census.render_markdown(normalized)

        committed_md = MD_ARTIFACT.read_text(encoding="utf-8")
        assert fresh_md == committed_md, (
            f"docs/settings_field_census.md is stale. {_REGEN_HINT}"
        )

    def test_form_d_counts_match_allowlist(self, fresh_census):
        # Known aliases / dynamic names that are legitimately absent.
        benign = {"HOME", "PATH", "PWD", "USER", "TERM", "HTTPS_PROXY", "HTTP_PROXY",
                  "DATABASE_URL", "PYTEST_CURRENT_TEST", "CI", "RH_LOGIN_WORKER", "NO_VENV_REEXEC"}
                  
        # Temporary allowlist: removed once WP-A/B/C/D lands
        allowlist = {
            "FUNDAMENTALS_CACHE_TTL_SECONDS", "FUNDAMENTALS_NEG_CACHE_TTL_SECONDS",
            "FINNHUB_RATE_LIMIT_PER_MIN", "WATCHLIST", "LOG_LEVEL", "NTFY_TOPIC",
            "ALERT_NTFY_TOPIC", "ALERT_EMAIL_SMTP_HOST", "ALERT_EMAIL_SMTP_PORT",
            "ALERT_EMAIL_SMTP_PASSWORD", "ALERT_EMAIL_FROM", "ALERT_EMAIL_TO",
            "ALERT_SLACK_WEBHOOK_URL", "ALERT_CHANNELS", "PROMPT_REGISTRY_SIGNING_KEY",
            "QDRANT_URL", "QDRANT_COLLECTION",
            "GCLOUD_BIN", "NO_VENV_REEXEC"
        }
        
        actual = set(fresh_census["read_forms"]["form_d_os_environ"]["counts"].keys())
        # NTFY_TOPIC is in the A-D list but not actually referenced yet
        allowlist.discard("NTFY_TOPIC")
        
        assert actual == allowlist