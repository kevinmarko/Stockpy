## Summary
<!-- What does this PR do? 1-3 bullets. -->
-

## Domain touched
<!-- Check all that apply -->
- [ ] signals / strategy (`signals/`, `strategy_engine.py`, `sizing/`)
- [ ] ML pipeline (`ml/`)
- [ ] data layer (`data/`, `data_engine.py`, `dto_models.py`)
- [ ] execution (`execution/`)
- [ ] GUI / reporting (`webapp/`, `shared/`, `reporting/`, `diagnostics_and_visuals.py`)
- [ ] observability (`observability/`)
- [ ] config / schema (`config.py`, `database_setup.py`)
- [ ] tests
- [ ] other

## Test plan
<!-- There is no GitHub CI (GitHub Actions is retired). Paste the `make local-ci` result table here. -->
- [ ] `make local-ci` passes (table pasted above; say which steps were skipped and why)
- [ ] No new lookahead bias introduced (perturbation tests cover changed indicators)
- [ ] `COLUMN_SCHEMA` updated if new fields added
- [ ] No fabricated metrics (NaN, not 0, for unavailable data)
