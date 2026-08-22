#!/usr/bin/env python3
"""
Purge corrupt paper options.

Deletes `paper_positions` rows where the symbol parses as an option and `avg_entry_price <= 0`,
and reverses the corresponding cash impact to the paper account.

Usage:
  python scripts/purge_corrupt_paper_options.py [--apply]
"""

import argparse
import logging
import os
import re
import shutil
from datetime import datetime

from data.paper_account_store import PaperAccountStore, PaperPosition, PaperAccount
from db_config import resolve_database_url, session_scope

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_OPTION_SYMBOL_REGEX = re.compile(
    r"^[A-Z0-9]+\s+\d{4}-\d{2}-\d{2}\s+\$?\d+(?:\.\d+)?\s+(CALL|PUT)$",
    re.IGNORECASE,
)

def main():
    parser = argparse.ArgumentParser(description="Purge corrupt paper option positions.")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Defaults to dry-run.")
    args = parser.parse_args()

    db_url = resolve_database_url()
    is_sqlite = db_url.startswith("sqlite:///")
    if args.apply and is_sqlite:
        db_path = db_url.replace("sqlite:///", "")
        backup_path = f"{db_path}.pre-purge-{datetime.now().strftime('%Y%m%d%H%M%S')}.bak"
        if os.path.exists(db_path):
            shutil.copy2(db_path, backup_path)
            logger.info(f"Backed up database to {backup_path}")

    store = PaperAccountStore(db_url=db_url)
    
    with session_scope(store.Session) as session:
        acc = session.query(PaperAccount).filter_by(id=1).first()
        if not acc:
            logger.error("No PaperAccount found.")
            return
            
        initial_cash = acc.cash_balance
        positions = session.query(PaperPosition).all()
        
        to_delete = []
        cash_reversal = 0.0
        
        for pos in positions:
            sym = pos.symbol.upper().strip()
            if _OPTION_SYMBOL_REGEX.match(sym):
                if pos.avg_entry_price <= 0:
                    to_delete.append(pos)
                    # Reversing the cash impact:
                    # If we bought (qty > 0), the cash was debited (qty * entry_price * 100)
                    impact = pos.qty * pos.avg_entry_price * 100.0
                    cash_reversal += impact
        
        logger.info(f"Total positions before: {len(positions)}")
        logger.info(f"Found {len(to_delete)} corrupt option positions to delete.")
        
        if args.apply:
            for pos in to_delete:
                logger.info(f"Deleting {pos.symbol}: qty={pos.qty}, entry={pos.avg_entry_price}")
                session.delete(pos)
                
            acc.cash_balance += cash_reversal
            logger.info(f"Reversed cash impact: {cash_reversal:.2f}. New cash balance: {acc.cash_balance:.2f}")
            logger.info("Changes applied.")
        else:
            for pos in to_delete:
                logger.info(f"[DRY-RUN] Would delete {pos.symbol}: qty={pos.qty}, entry={pos.avg_entry_price}")
            logger.info(f"[DRY-RUN] Would reverse cash impact: {cash_reversal:.2f}. New cash balance: {acc.cash_balance + cash_reversal:.2f}")

if __name__ == "__main__":
    main()
