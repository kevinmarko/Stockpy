# Experiments Architecture

The A/B testing framework (`experiments/`) is designed to safely test modifications to trading strategies.

## Key Components

1. **Registry (`experiments/registry.py`)**: Defines `Experiment` and `Arm` structures. Ensures that all experiments have a defined unit, allocation, and fallback defaults.
2. **Assignment (`experiments/assignment.py`)**: Uses a deterministic hashing mechanism to assign items (e.g. `hash(experiment_id, symbol, cycle_date)`) to guarantee consistent cycle-over-cycle execution and reproducibility.
3. **Store (`experiments/store.py`)**: Records `ExperimentRun` (the arms assigned) and `ExperimentObservation` (the counterfactual shadow-decisions taken by model variants without risking capital).
4. **Compare (`experiments/compare.py`)**: Uses the deflated Sharpe family validation to confirm statistical significance and gating through `min_samples_per_arm`.
