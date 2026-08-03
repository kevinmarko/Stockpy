# `tests/fixtures/settings_liveness/`

Synthetic modules exercising every capture rule in
`scripts/settings_liveness.py`, one shape per file.

**These files are never imported or executed.** They exist to be parsed with
`ast` by `tests/test_settings_liveness.py`. They deliberately live under
`tests/`, which `scripts/settings_liveness.py`'s file walk skips, so they can
never contaminate the real-tree run that produces `docs/settings_liveness.json`.

Why they exist: on the real tree most capture rules fire zero times (a rule
firing zero times is not evidence it is correct — only evidence nothing in
this codebase currently has that shape). Each fixture turns "I reasoned this
rule is right" into "this rule is tested", including the two rules whose
*false positives* were the hardest part of the design:

* `cap_closure.py` — an escaping closure must classify `closure_value`; an
  ordinary per-call worker closure that dies with its call must NOT.
* `factory_fresh.py` vs `factory_memoized.py` — a guard/dependency factory's
  inner function is fresh (evaluated per request) unless it is memoized.

Every fixture references a real `Settings` field name, because the classifier
keys off `Settings.model_fields` membership rather than any value.
