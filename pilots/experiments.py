import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from experiments.registry import Experiment, Arm
from experiments.store import ExperimentStore, ExperimentConfig
from settings import settings

def list_experiments() -> List[Dict[str, Any]]:
    store = ExperimentStore()
    with store.Session() as session:
        exps = session.query(ExperimentConfig).all()
        return [
            {
                "id": exp.id,
                "name": exp.name,
                "unit": exp.unit,
                "arms": exp.arms,
                "allocation": exp.allocation,
                "started_at": exp.started_at.isoformat(),
                "min_samples_per_arm": exp.min_samples_per_arm,
                "status": exp.status
            } for exp in exps
        ]

def get_experiment_by_id(exp_id: str) -> Optional[Dict[str, Any]]:
    store = ExperimentStore()
    with store.Session() as session:
        exp = session.query(ExperimentConfig).filter_by(id=exp_id).first()
        if not exp:
            return None
        return {
            "id": exp.id,
            "name": exp.name,
            "unit": exp.unit,
            "arms": exp.arms,
            "allocation": exp.allocation,
            "started_at": exp.started_at.isoformat(),
            "min_samples_per_arm": exp.min_samples_per_arm,
            "status": exp.status
        }

def create_experiment(exp_id: str, name: str, unit: str, arms: List[Any], allocation: List[float], min_samples_per_arm: int):
    store = ExperimentStore(readonly=False)
    with store.Session() as session:
        # validate using registry
        registry_arms = [Arm(name=a.name, overrides=a.overrides) for a in arms]
        exp = Experiment(
            id=exp_id, name=name, unit=unit, arms=registry_arms, 
            allocation=allocation, started_at=datetime.now(timezone.utc), 
            min_samples_per_arm=min_samples_per_arm
        )
        
        db_exp = ExperimentConfig(
            id=exp.id,
            name=exp.name,
            unit=exp.unit,
            arms=[{"name": a.name, "overrides": a.overrides} for a in registry_arms],
            allocation=exp.allocation,
            started_at=exp.started_at,
            min_samples_per_arm=exp.min_samples_per_arm,
            status=exp.status
        )
        session.merge(db_exp)
        session.commit()
        return get_experiment_by_id(exp_id)

def stop_experiment(exp_id: str):
    store = ExperimentStore(readonly=False)
    with store.Session() as session:
        exp = session.query(ExperimentConfig).filter_by(id=exp_id).first()
        if exp:
            exp.status = "stopped"
            session.commit()
