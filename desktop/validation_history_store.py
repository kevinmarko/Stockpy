"""SQLAlchemy-backed durable log of model validation runs.

Mirrors RunHistoryStore for model training and validation (CPCV) results.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import create_db_engine, resolve_database_url, session_scope

logger = logging.getLogger(__name__)

Base = declarative_base()


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    run_id = Column(String(64), primary_key=True)
    strategy_name = Column(String(128), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    oos_max_dd = Column(Float, nullable=True)
    oos_sortino = Column(Float, nullable=True)
    pbo = Column(Float, nullable=True)
    dsr = Column(Float, nullable=True)
    n_trials = Column(Integer, nullable=True)


class ValidationHistoryStore:
    def __init__(self, db_url: Optional[str] = None, *, readonly: bool = False) -> None:
        db_url = db_url or resolve_database_url()
        self._readonly = readonly
        if readonly:
            from db_config import create_readonly_db_engine
            self.engine = create_readonly_db_engine(db_url)
        else:
            self.engine = create_db_engine(db_url)
            Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_validation(self, strategy_name: str, metrics: Dict[str, float]) -> None:
        if self._readonly:
            raise RuntimeError("ValidationHistoryStore is read-only; cannot record.")

        with session_scope(self.Session) as session:
            run_id = str(uuid.uuid4())
            row = ValidationRun(
                run_id=run_id,
                strategy_name=strategy_name,
                oos_max_dd=metrics.get("oos_max_dd"),
                oos_sortino=metrics.get("oos_sortino"),
                pbo=metrics.get("pbo"),
                dsr=metrics.get("dsr"),
                n_trials=int(metrics.get("n_trials")) if metrics.get("n_trials") is not None else None,
            )
            session.add(row)

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            session = self.Session()
            try:
                rows = (
                    session.query(ValidationRun)
                    .order_by(ValidationRun.timestamp.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "run_id": r.run_id,
                        "strategy_name": r.strategy_name,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "oos_max_dd": r.oos_max_dd,
                        "oos_sortino": r.oos_sortino,
                        "pbo": r.pbo,
                        "dsr": r.dsr,
                        "n_trials": r.n_trials,
                    }
                    for r in rows
                ]
            finally:
                session.close()
        except Exception as exc:
            logger.warning("ValidationHistoryStore.get_recent: %s", exc)
            return []
