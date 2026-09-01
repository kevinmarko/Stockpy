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

class TestFormDOsEnvironIsFullyAllowlisted:
    """Gate for ``scripts/measure_settings_census.py``'s ``form_d_os_environ``
    measurement -- the census's count of ``Settings``-field-shaped names read
    via a bare ``os.environ.get(...)``/``os.environ[...]`` instead of through
    ``settings.settings.X`` (Form D in the census's own terminology; see
    ``read_forms.form_d_os_environ`` in ``docs/settings_field_census.json``).

    This is the trailing gate for the trading-safety-audit os.environ-bypass
    sweep (PR #962 and its work packages): once every WP-A through WP-Z fix
    lands, exactly two fields should remain in Form D, both for structural
    reasons that make a ``settings.X`` read impossible or actively wrong at
    the read site -- not oversights:

    - ``GCLOUD_BIN`` -- read in ``mcp_remote_adapter.py``, a standalone stdio
      proxy script explicitly configured via ``claude_desktop_config.json``'s
      ``env`` block (per that file's own module docstring), never via
      ``.env``/``settings.py``. It is also a declared ``Settings`` field
      (``settings.py``'s ``GCLOUD_BIN`` field), so
      ``scripts/auditor/stockpy_codebase_auditor.py::check_configuration``'s
      separate ``undeclared_env_var`` check never flags it at all -- it does
      not need (and does not have) an entry in that check's own ``benign``
      allowlist.
    - ``NO_VENV_REEXEC`` -- read in ``scripts/_bootstrap.py``, BEFORE
      ``settings.py`` can safely be imported (deciding whether to re-exec
      under the ``.venv`` interpreter) -- the same category as ``main.py``'s
      /``main_orchestrator.py``'s own top-of-file reexec guards, which read
      raw env vars for the identical reason. ``settings.py`` documents this
      explicitly in its own comment block just above the ``WATCHLIST`` field
      (search for ``RH_LOGIN_WORKER and KEY are deliberately NOT declared
      here``), which names this test file as the enforcement mechanism.
      ``scripts/auditor/stockpy_codebase_auditor.py::check_configuration``'s
      ``benign`` allowlist independently carries the same two-field
      reasoning (``RH_LOGIN_WORKER`` and ``NO_VENV_REEXEC`` are both listed
      there) for its own, differently-scoped check.

    A regression here means a fix in one of the other os.environ-bypass work
    packages (``data/market_data.py``, ``data/portfolio_sync.py``,
    ``alerting.py``, etc.) was reverted, or a brand-new bypass was
    introduced -- in either case, re-run
    ``python3 scripts/measure_settings_census.py --json`` and grep the
    ``read_forms.form_d_os_environ.counts`` object to find the offending
    field and its reading module, then either fix the read site to use
    ``settings.settings.X`` or (if it is a third, equally-structural case
    like the two above) extend this allowlist with the same class of
    justification.
    """

    ALLOWED_OS_ENVIRON_FIELDS = frozenset({"GCLOUD_BIN", "NO_VENV_REEXEC"})

    def test_form_d_os_environ_counts_only_the_two_allowlisted_fields(self, fresh_census):
        form_d = fresh_census["read_forms"]["form_d_os_environ"]
        counted_fields = set(form_d["counts"].keys())
        assert counted_fields == self.ALLOWED_OS_ENVIRON_FIELDS, (
            "scripts/measure_settings_census.py's form_d_os_environ measurement "
            f"found {sorted(counted_fields)}, expected exactly "
            f"{sorted(self.ALLOWED_OS_ENVIRON_FIELDS)}. A Settings-field-shaped "
            "name bypassing settings.settings.X via a bare os.environ read was "
            "either introduced or left unfixed -- see this class's docstring "
            "for the two structurally-justified exceptions and how to "
            "investigate a new one."
        )

    def test_form_d_os_environ_distinct_fields_matches_allowlist_size(self, fresh_census):
        # Belt-and-suspenders on the census's own reported distinct_fields
        # count, independent of the counts dict's keys checked above.
        form_d = fresh_census["read_forms"]["form_d_os_environ"]
        assert form_d["distinct_fields"] == len(self.ALLOWED_OS_ENVIRON_FIELDS)


