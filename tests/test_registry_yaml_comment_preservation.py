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
