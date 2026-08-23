from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from db_config import resolve_database_url, create_db_engine, create_readonly_db_engine

Base = declarative_base()

class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String, nullable=False, index=True)
    cycle_date = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    assigned_arm = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class ExperimentObservation(Base):
    __tablename__ = "experiment_observations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String, nullable=False, index=True)
    cycle_date = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    arm = Column(String, nullable=False)
    decision = Column(JSON, nullable=False)
    size = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class ExperimentStore:
    def __init__(self, db_url=None, readonly=False):
        url = db_url or resolve_database_url()
        self.engine = create_readonly_db_engine(url) if readonly else create_db_engine(url)
        if not readonly:
            Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record_run(self, experiment_id: str, cycle_date: str, symbol: str, assigned_arm: str):
        with self.Session() as session:
            run = ExperimentRun(
                experiment_id=experiment_id,
                cycle_date=cycle_date,
                symbol=symbol,
                assigned_arm=assigned_arm
            )
            session.add(run)
            session.commit()

    def record_observation(self, experiment_id: str, cycle_date: str, symbol: str, arm: str, decision: dict, size: float):
        with self.Session() as session:
            obs = ExperimentObservation(
                experiment_id=experiment_id,
                cycle_date=cycle_date,
                symbol=symbol,
                arm=arm,
                decision=decision,
                size=size
            )
            session.add(obs)
            session.commit()
