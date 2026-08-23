import argparse
import sys
from sqlalchemy.orm import sessionmaker
from db_config import create_db_engine
from data.paper_account_store import PaperPosition, PaperAccount, PaperOrder
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_purge(apply: bool, engine=None):
    if engine is None:
        engine = create_db_engine()
    Session = sessionmaker(bind=engine)
    
    with Session() as session:
        acc = session.query(PaperAccount).filter_by(id=1).first()
        if not acc:
            logger.error("No PaperAccount found.")
            return

        # Find corrupt paper positions: options (containing space) with avg_entry_price <= 0
        corrupt_positions = session.query(PaperPosition).filter(
            PaperPosition.symbol.like("% %"),
            PaperPosition.avg_entry_price <= 0
        ).all()
        
        if not corrupt_positions:
            logger.info("No corrupt paper positions found.")
            return

        logger.info(f"Found {len(corrupt_positions)} corrupt paper positions.")
        for pos in corrupt_positions:
            logger.info(f"Corrupt Position: {pos.symbol}, qty={pos.qty}, price={pos.avg_entry_price}")
            if apply:
                session.delete(pos)
                
        if apply:
            session.commit()
            logger.info("Purge committed successfully.")
        else:
            logger.info("Dry run complete. Use --apply to execute deletion.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the deletions")
    args = parser.parse_args()
    run_purge(args.apply)
