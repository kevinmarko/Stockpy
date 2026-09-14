"""
Regression tests: ``ml/registry.yaml`` comment preservation across a metrics write.
==================================================================================

Background
----------
``ml/registry.yaml`` carries a ~37-line schema-documentation header comment block
explaining every field (including *why* ``cpcv_mean_oos_max_dd`` is null for
``meta_labeler_*`` roles).  PyYAML does not preserve comments, so a writer that
does ``yaml.safe_load -> mutate -> yaml.safe_dump`` erodes that documentation on
every retrain.

This has now bitten the same file twice:

1. The "Forecast Backfill Meta-Labeler Bridge" work (CLAUDE.md) recorded that
   "the file's entire ~35-line schema-documentation header comment block had been
   silently dropped (a ``yaml.dump``/``yaml.safe_dump`` re-serialization
   artifact, not a deliberate edit)".
2. A real ``lgbm_ranker`` training run on 2026-09-04 silently deleted the 4-line
   ``meta_labeler_*`` explanation (37 comment lines -> 33), because
   ``_dump_registry`` re-emitted a *hardcoded copy* of the header
   (``_REGISTRY_HEADER``) that had drifted out of sync with the real file.

These tests pin the invariant that closes the class: a metrics update must leave
every ``#`` line that was present before the write still present after it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_REGISTRY = REPO_ROOT / "ml" / "registry.yaml"


def _comment_lines(text: str) -> list[str]:
    """Every comment line in the file, in order, stripped of trailing whitespace."""
    return [ln.rstrip() for ln in text.splitlines() if ln.lstrip().startswith("#")]


@pytest.fixture()
def tmp_registry(tmp_path: Path) -> Path:
    """A byte-for-byte copy of the real repo registry in an isolated temp dir."""
    dst = tmp_path / "registry.yaml"
    shutil.copyfile(REPO_REGISTRY, dst)
    return dst


# ──────────────────────────────────────────────────────────────────────────────
# The core invariant
# ──────────────────────────────────────────────────────────────────────────────

def test_metrics_update_preserves_every_comment_line(tmp_registry: Path):
    """A metrics update must not drop a single ``#`` line from the registry."""
    from ml.registry_io import update_model_metrics

    before_text = tmp_registry.read_text(encoding="utf-8")
    before_comments = _comment_lines(before_text)
    assert before_comments, "fixture precondition: the registry has comments to preserve"

    update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-09-04",
        cpcv_dsr=1.7294176083023635e-16,
        pbo=0.2,
        n_train=460,
        path=tmp_registry,
    )

    after_comments = _comment_lines(tmp_registry.read_text(encoding="utf-8"))

    missing = [c for c in before_comments if c not in after_comments]
    assert not missing, (
        f"{len(missing)} comment line(s) were silently dropped by the registry "
        f"write. First few: {missing[:6]}"
    )
    assert after_comments == before_comments, (
        "comment block changed (order or content) across a metrics write"
    )


def test_metrics_update_preserves_meta_labeler_max_dd_explanation(tmp_registry: Path):
    """Pin the exact 4 lines the 2026-09-04 lgbm_ranker retrain silently deleted."""
    from ml.registry_io import update_model_metrics

    sentinel = "meta_labeler_* roles: their CPCV returns are discrete per-event R-multiples"
    assert sentinel in tmp_registry.read_text(encoding="utf-8"), (
        "fixture precondition: the repo registry still documents the meta_labeler_* null"
    )

    update_model_metrics(
        "lgbm_ranker", trained_date="2026-09-04", cpcv_dsr=0.5, pbo=0.2,
        n_train=460, path=tmp_registry,
    )

    assert sentinel in tmp_registry.read_text(encoding="utf-8")


def test_metrics_update_still_writes_the_real_values(tmp_registry: Path):
    """Comment preservation must not come at the cost of the actual update."""
    from ml.registry_io import load_registry, update_model_metrics

    update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-09-04",
        cpcv_dsr=1.7294176083023635e-16,
        pbo=0.2,
        n_train=460,
        path=tmp_registry,
    )

    entry = load_registry(tmp_registry)["models"]["lgbm_ranker"]
    assert entry["trained_date"] == "2026-09-04"
    assert entry["cpcv_dsr"] == pytest.approx(1.7294176083023635e-16)
    assert entry["pbo"] == pytest.approx(0.2)
    assert entry["n_train"] == 460
    assert entry["deployable"] is False


def test_untouched_models_keep_their_exact_original_text(tmp_registry: Path):
    """Only the updated model's block may be re-serialized; siblings stay byte-identical."""
    from ml.registry_io import update_model_metrics

    before = tmp_registry.read_text(encoding="utf-8")
    data = yaml.safe_load(before)
    others = [k for k in data["models"] if k != "lgbm_ranker"]
    assert others, "fixture precondition: the registry has more than one model"

    update_model_metrics(
        "lgbm_ranker", trained_date="2026-09-04", cpcv_dsr=0.5, pbo=0.2,
        n_train=460, path=tmp_registry,
    )
    after = tmp_registry.read_text(encoding="utf-8")

    for key in others:
        block_before = _model_block(before, key)
        assert block_before, f"could not locate block for {key}"
        assert block_before in after, f"model block for '{key}' was re-serialized/altered"


def test_inline_comment_inside_an_untouched_model_survives(tmp_registry: Path):
    """A hand-added inline comment on a model we don't touch must survive a write."""
    from ml.registry_io import update_model_metrics

    text = tmp_registry.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    other = next(k for k in data["models"] if k != "lgbm_ranker")
    marker = "  # HAND-WRITTEN NOTE: do not delete me"
    text = text.replace(f"  {other}:\n", f"{marker}\n  {other}:\n", 1)
    tmp_registry.write_text(text, encoding="utf-8")

    update_model_metrics(
        "lgbm_ranker", trained_date="2026-09-04", cpcv_dsr=0.5, pbo=0.2,
        n_train=460, path=tmp_registry,
    )

    assert marker in tmp_registry.read_text(encoding="utf-8")


def test_bootstrap_header_constant_matches_the_repo_registry_header():
    """``_REGISTRY_HEADER`` is only used to bootstrap a brand-new file — but a
    stale copy is exactly what caused the 2026-09-04 loss, so pin it in sync."""
    from ml.registry_io import _REGISTRY_HEADER

    repo_comments = _comment_lines(REPO_REGISTRY.read_text(encoding="utf-8"))
    constant_comments = _comment_lines(_REGISTRY_HEADER)
    assert constant_comments == repo_comments, (
        "ml/registry_io.py::_REGISTRY_HEADER has drifted from ml/registry.yaml's "
        "real header — update the constant (or the file) so a fresh bootstrap "
        "write emits the current documentation."
    )


def test_fresh_file_bootstrap_emits_the_header(tmp_path: Path):
    """Writing to a path that does not exist yet still produces a documented file."""
    from ml.registry_io import _dump_registry

    target = tmp_path / "nested" / "registry.yaml"
    _dump_registry({"models": {"m": {"role": "r"}}}, target)

    text = target.read_text(encoding="utf-8")
    assert _comment_lines(text), "bootstrap write emitted no header comments"
    assert yaml.safe_load(text)["models"]["m"]["role"] == "r"


def _model_block(text: str, key: str) -> str:
    """Extract the raw text block for ``models.<key>`` (indent-2 mapping key)."""
    lines = text.splitlines(keepends=True)
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"  {key}:"):
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln.strip() and not ln.startswith("    ") and not ln.lstrip().startswith("#"):
            end = j
            break
    return "".join(lines[start:end])


def test_realistic_retrain_diff_is_confined_to_the_changed_scalar_lines(tmp_registry: Path):
    """A retrain must not produce spurious re-formatting noise in a git-tracked file."""
    from ml.registry_io import update_model_metrics

    before = tmp_registry.read_text(encoding="utf-8")
    spec = yaml.safe_load(before)["models"]["lgbm_ranker"]

    update_model_metrics(
        "lgbm_ranker",
        trained_date="2026-09-04",
        cpcv_dsr=1.7294176083023635e-16,
        pbo=spec["pbo"],
        n_train=460,
        artifact_file=spec["artifact_file"],
        hyperparameters=spec["hyperparameters"],
        train_window=spec["train_window"],
        features=spec["features"],
        cpcv_mean_oos_sharpe=spec["cpcv_mean_oos_sharpe"],
        cpcv_mean_oos_max_dd=spec["cpcv_mean_oos_max_dd"],
        path=tmp_registry,
    )
    after = tmp_registry.read_text(encoding="utf-8")

    changed = {
        ln.strip()
        for ln in set(before.splitlines()) ^ set(after.splitlines())
    }
    unexpected = {
        c for c in changed
        if not c.startswith(("trained_date:", "cpcv_dsr:", "n_train:"))
    }
    assert not unexpected, f"write produced unrelated diff noise: {sorted(unexpected)}"


def test_unsplicable_file_falls_back_but_still_keeps_its_real_header(tmp_path: Path):
    """A shape the splicer declines must NOT resurrect the hardcoded header constant."""
    from ml.registry_io import _dump_registry

    target = tmp_path / "registry.yaml"
    target.write_text(
        "# REAL ON-DISK HEADER\n# second documentation line\n\nmodels: {a: {role: r}}\n",
        encoding="utf-8",
    )

    _dump_registry({"models": {"a": {"role": "r"}, "b": {"role": "q"}}}, target)

    text = target.read_text(encoding="utf-8")
    assert "# REAL ON-DISK HEADER" in text
    assert "# second documentation line" in text
    assert "InvestYo ML Model Registry" not in text, (
        "fallback overwrote the file's real header with the bootstrap constant"
    )
    assert yaml.safe_load(text)["models"]["b"]["role"] == "q"


def test_no_op_write_leaves_the_file_byte_identical(tmp_registry: Path):
    from ml.registry_io import _dump_registry, load_registry

    before = tmp_registry.read_text(encoding="utf-8")
    _dump_registry(load_registry(tmp_registry), tmp_registry)
    assert tmp_registry.read_text(encoding="utf-8") == before


# ──────────────────────────────────────────────────────────────────────────────
# A THIRD, distinct data-loss class on the same file: ' #' inside a plain scalar
# ──────────────────────────────────────────────────────────────────────────────
#
# `ml/registry.yaml`'s `options_meta_labeler.notes` was hand-written as an
# UNQUOTED (plain) scalar containing "(CONSTRAINT #4)". In YAML, ' #' inside a
# plain scalar starts a comment — so PyYAML's *reader* silently discarded
# everything from ' #4);' onward at parse time, and the next safe_dump wrote the
# already-truncated value back. The sentence was permanently cut mid-clause:
#
#     "...rather than a fabricated 0.0/1.0 (CONSTRAINT"
#
# The full text was recovered verbatim from commit ff718ea3 and rewritten
# through PyYAML, which quotes it correctly so it round-trips.
#
# The writer cannot defend against this: the loss happens on READ, before
# `_dump_registry` ever sees the value. The realistic window to catch it is the
# commit after a hand-edit, before the next retrain makes it permanent — which
# is what these tests do.

def _scalar_closes_on_line(fragment: str, quote: str) -> bool:
    """Does a quoted scalar opened by ``quote`` close within ``fragment``?

    Handles YAML's escapes: `''` inside a single-quoted scalar and `\\"` inside a
    double-quoted one are literal characters, not terminators.
    """
    i = 0
    while i < len(fragment):
        ch = fragment[i]
        if quote == "'" and ch == "'":
            if i + 1 < len(fragment) and fragment[i + 1] == "'":
                i += 2          # escaped '' — keep going
                continue
            return True
        if quote == '"':
            if ch == "\\":
                i += 2          # escaped char — skip it
                continue
            if ch == '"':
                return True
        i += 1
    return False


def _unquoted_hash_offenders(text: str) -> list[tuple[int, str]]:
    """Lines where a ' #' sits inside an UNQUOTED scalar and will be eaten.

    Two shapes are checked, both unambiguous:
      * ``key: <unquoted value containing ' #'>``
      * a plain-scalar continuation line (indented, no ``key:``, not a comment)

    A ``#`` on a plain-scalar continuation line always terminates the scalar, so
    there is no such thing as a legitimate trailing comment there — no false
    positives. Continuation lines of a *quoted* scalar (whose opening quote sits
    on an earlier line) are tracked and skipped, since ``#`` is literal there.
    """
    offenders: list[tuple[int, str]] = []
    open_quote: str | None = None

    for n, raw in enumerate(text.splitlines(), start=1):
        if open_quote is not None:
            if _scalar_closes_on_line(raw, open_quote):
                open_quote = None
            continue  # inside a quoted scalar — '#' is literal, nothing to flag

        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue

        key, sep, value = stripped.partition(": ")
        if sep and not key.startswith(("'", '"')):
            candidate = value.strip()
        elif not sep and raw.startswith("  "):
            candidate = stripped  # plain-scalar continuation
        else:
            continue

        if candidate.startswith(("'", '"')):
            quote = candidate[0]
            if not _scalar_closes_on_line(candidate[1:], quote):
                open_quote = quote  # multi-line quoted scalar starts here
            continue  # quoted — safe either way

        if " #" in candidate:
            offenders.append((n, raw.rstrip()))
    return offenders


def test_no_unquoted_hash_scalars_in_the_committed_registry():
    """A ' #' in an unquoted scalar is silently truncated on the next read."""
    offenders = _unquoted_hash_offenders(REPO_REGISTRY.read_text(encoding="utf-8"))
    assert not offenders, (
        "These lines contain ' #' inside an unquoted YAML scalar. PyYAML will "
        "read that as a comment and silently discard the rest of the value on "
        "the next load — quote the scalar (wrap the value in single quotes), or "
        "move the text to its own full-line comment:\n"
        + "\n".join(f"  line {n}: {t}" for n, t in offenders)
    )


def test_the_detector_actually_catches_the_original_defect():
    """Pin the detector against the exact text that was lost, so it can't rot."""
    bad = (
        "models:\n"
        "  m:\n"
        "    notes: Not yet trained on any paper trades (n_train=0) -- cpcv_dsr/pbo are\n"
        "      null rather than a fabricated 0.0/1.0 (CONSTRAINT #4); this is not evaluated.\n"
    )
    assert _unquoted_hash_offenders(bad), "detector missed the real defect"
    # ...and that PyYAML really does eat it, so the test is guarding something real.
    assert yaml.safe_load(bad)["models"]["m"]["notes"].endswith("(CONSTRAINT")

    good = yaml.safe_dump({"models": {"m": {"notes": "a 0.0/1.0 (CONSTRAINT #4); ok"}}})
    assert not _unquoted_hash_offenders(good), "detector flags correctly-quoted output"
    assert yaml.safe_load(good)["models"]["m"]["notes"].endswith("#4); ok")


def test_options_meta_labeler_note_is_no_longer_truncated():
    data = yaml.safe_load(REPO_REGISTRY.read_text(encoding="utf-8"))
    notes = data["models"]["options_meta_labeler"]["notes"]
    assert not notes.rstrip().endswith("(CONSTRAINT"), "note is still truncated"
    assert notes.rstrip().endswith('not "evaluated and failed."')


def test_restored_note_survives_a_real_metrics_write(tmp_registry: Path):
    """The restored text must survive the writer, not just sit in the file."""
    from ml.registry_io import load_registry, update_model_metrics

    before = load_registry(tmp_registry)["models"]["options_meta_labeler"]["notes"]
    assert before.rstrip().endswith('not "evaluated and failed."')

    update_model_metrics(
        "lgbm_ranker", trained_date="2026-09-04", cpcv_dsr=0.5, pbo=0.2,
        n_train=460, path=tmp_registry,
    )

    after = load_registry(tmp_registry)["models"]["options_meta_labeler"]["notes"]
    assert after == before, "the restored note did not survive a registry write"
