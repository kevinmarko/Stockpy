# Task tracker — `ml/registry.yaml` comment preservation

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 1 | Find the writer; confirm by reading code that it round-trips through `yaml.safe_dump` | ✅ | `ml/registry_io.py::_dump_registry` — `yaml.safe_dump` + a hardcoded, drifted `_REGISTRY_HEADER`. Single choke point for all 3 callers + `load_registry`'s self-sync. |
| 2 | Check whether `ruamel.yaml` is already a dependency | ✅ | Not in `requirements*.txt`; `import ruamel.yaml` → `ModuleNotFoundError`. No new dependency added. |
| 3 | Write the regression test FIRST and prove it fails unfixed | ✅ | 8 of 10 fail against the original writer (2 are controls that should pass either way). |
| 4 | Implement the surgical splice writer | ✅ | `_splice_registry_text` + verified fallback that preserves the real on-disk header. |
| 5 | Prove the test passes after the fix | ✅ | 10/10 pass. |
| 6 | No regressions in existing registry suites | ✅ | `test_registry_load.py` + `test_train_lgbm.py` + `test_train_meta_labelers.py` + new file → **84 passed**. |
| 7 | Genuine-bug lint clean | ✅ | `ruff check --select=F821,F822,F823,E9` → All checks passed. |
| 8 | Restore the 4 lost comment lines in the operator's working tree WITHOUT discarding the training results | ✅ | Main checkout back to 37 comment lines; header diff vs HEAD now empty; all value updates intact. |
| 9 | Restore the same 4 lines in the `LOCAL_DATA_ROOT` runtime copy | ✅ | `~/.stockpy_local/ml_models/registry.yaml` 33 → 37 lines; parsed data provably unchanged. |
| 10 | Branch + PR artifacts with task-scoped names | ✅ | `fix-registry-yaml-comment-preservation`; `.claude/registry_yaml_comment_preservation_*.md`. |

## Follow-up: the two items flagged as out of scope in the first commit

| # | Task | Status | Evidence |
|---|------|--------|----------|
| 11 | Recover the truncated `options_meta_labeler.notes` **without inventing an ending** | ✅ | Full text recovered verbatim from commit `ff718ea3`; rewritten through PyYAML so the ` #4` is quoted and round-trips. |
| 12 | Root-cause the truncation | ✅ | A **third, distinct** bug class: ` #` inside an *unquoted* plain scalar is read as a comment, so PyYAML discards the rest **on READ**. Truncated at `e7b2ac74` — the same audit pass CLAUDE.md records for the header loss. |
| 13 | Guard the ` #` class | ✅ | `_unquoted_hash_offenders` detector + tests. Proven: flags exactly 1 line in `ff718ea3` (intact-but-vulnerable), 0 in the fixed file. |
| 14 | Correct my earlier claim that `watch_rules.yaml` has "no doc header today" | ✅ | Wrong. It is 90 lines, **76 of them documentation**, and a single `update_watch_rules` call collapsed it 4246 → 247 bytes (measured). |
| 15 | Fix the `watch_rules.yaml` writer | ✅ | `_write_watch_rules` → `yaml_comment_io.splice_sequence_section`. Comments there are interleaved among rules, so header-only preservation was not sufficient and is not what is tested. |
| 16 | Single source of truth for the shared helper | ✅ | New `yaml_comment_io.py`; `ml/registry_io.py` imports `leading_comment_block` rather than keeping a second copy. |
| 17 | No regressions | ✅ | 23 new tests pass. `test_investyo_mcp_server.py`'s 21 failures proven **byte-identical** pre-existing (same names, before and after). Genuine-bug lint clean. |
