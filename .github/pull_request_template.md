## Summary
<!-- What does this PR do? 1-3 bullets. -->
-

## Agent
<!-- Which agent authored this branch? -->
- [ ] Claude Code (`agent/claude-code/...`)
- [ ] Antigravity (`agent/antigravity/...`)

## Domain touched
<!-- Check all that apply -->
- [ ] signals / strategy (`signals/`, `strategy_engine.py`, `sizing/`)
- [ ] ML pipeline (`ml/`)
- [ ] data layer (`data/`, `data_engine.py`, `dto_models.py`)
- [ ] execution (`execution/`)
- [ ] GUI / reporting (`gui/`, `reporting_engine.py`, `diagnostics_and_visuals.py`)
- [ ] observability (`observability/`)
- [ ] config / schema (`config.py`, `database_setup.py`)
- [ ] tests
- [ ] other

## Test plan
- [ ] `pytest` passes locally
- [ ] No new lookahead bias introduced (perturbation tests cover changed indicators)
- [ ] `COLUMN_SCHEMA` updated if new fields added
- [ ] No fabricated metrics (NaN, not 0, for unavailable data)

## Conflicts with other agent?
<!-- List any files you touched that the other agent also owns, so the reviewer can sequence merges -->
