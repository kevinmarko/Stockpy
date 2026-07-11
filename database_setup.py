"""
InvestYo Quant Platform - Database Setup & Initialization Script
================================================================
Step 6: Transitions local flat-file storage to an institutional SQLite schema.
Dynamically maps the COLUMN_SCHEMA definition from config.py to database fields.
"""

import os
import sys
import logging
from sqlalchemy import text, inspect
from sqlalchemy.orm import sessionmaker

# Ensure the parent directory is in the path to import config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config
from db_config import resolve_database_url, create_db_engine, session_scope, get_dbapi_connection

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("DatabaseSetup")

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DB_DIR, "quant_platform.db")


# Explicit mapping from COLUMN_SCHEMA formats to SQLite datatypes
PANDAS_TO_SQLITE_TYPES = {
    "string": "TEXT",
    "number": "REAL",
    "currency": "REAL",
    "currency_large": "REAL",
    "percent": "REAL"
}


def type_map(col_format: str, col_key: str) -> str:
    """
    Translates COLUMN_SCHEMA format strings into SQLite data types.
    """
    fmt = col_format.lower().strip()
    # Check special case for discrete integer columns
    if col_key in ["Target_Days", "Volume"]:
        return "INTEGER"
    return PANDAS_TO_SQLITE_TYPES.get(fmt, "TEXT")


def initialize_database(db_file: str = DB_FILE):
    """
    Establishes the connection to the SQLite database and initializes tables.
    """
    if "://" not in db_file:
        db_url = f"sqlite:///{os.path.abspath(db_file)}"
    else:
        db_url = db_file
    logger.info(f"Connecting to database: {db_url}")
    
    engine = create_db_engine(db_url)
    Session = sessionmaker(bind=engine)
    
    try:
        with session_scope(Session) as session:
            # Retrieve the raw DBAPI connection for raw sqlite compatibility in setup/migration
            raw_conn = session.connection().connection
            dbapi_conn = get_dbapi_connection(raw_conn)
            cursor = dbapi_conn.cursor()

            # 1. Create ExecutionLogs Table
            logger.info("Initializing 'ExecutionLogs' table...")
            create_execution_logs_sql = """
            CREATE TABLE IF NOT EXISTS ExecutionLogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                ticker_count INTEGER NOT NULL,
                execution_time_seconds REAL,
                error_message TEXT
            );
            """
            cursor.execute(create_execution_logs_sql)
            logger.info("'ExecutionLogs' table created successfully.")

            # 2. Create DailySignals Table
            logger.info("Generating 'DailySignals' table schema from config.COLUMN_SCHEMA...")
            
            # Base columns
            columns_sql = [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "timestamp TEXT DEFAULT CURRENT_TIMESTAMP"
            ]
            
            # Dynamically build columns based on config.py COLUMN_SCHEMA definitions
            for col in config.COLUMN_SCHEMA:
                key = col["key"]
                col_type = type_map(col["format"], key)
                # Double-quote the column name to prevent syntax issues with spaces/symbols (e.g., "P/E", "Market Cap")
                columns_sql.append(f'"{key}" {col_type}')

            create_daily_signals_sql = f"""
            CREATE TABLE IF NOT EXISTS DailySignals (
                {",\n            ".join(columns_sql)}
            );
            """
            
            logger.debug(f"Executing SQL:\n{create_daily_signals_sql}")
            cursor.execute(create_daily_signals_sql)

            # F-07 FIX: Migrate schema — add any new COLUMN_SCHEMA columns missing from existing DB
            migrate_daily_signals_schema(cursor, dbapi_conn)
            
            # 3. Create Transactions Table for standardized trade journaling
            logger.info("Initializing 'Transactions' table...")
            create_transactions_sql = """
            CREATE TABLE IF NOT EXISTS Transactions (
                transaction_id TEXT PRIMARY KEY,
                execution_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                fill_price REAL NOT NULL,
                commission REAL DEFAULT 0.0,
                slippage REAL DEFAULT 0.0
            );
            """
            cursor.execute(create_transactions_sql)
            logger.info("'Transactions' table created successfully.")
    except Exception as e:
        # Unwrap SQLAlchemy OperationalError to raise raw sqlite3.OperationalError for tests
        if hasattr(e, "orig") and e.orig is not None:
            raise e.orig
        raise
        
    logger.info("Database initialization complete.")


def migrate_daily_signals_schema(cursor, conn):
    """
    F-07 FIX: Inspects existing DailySignals columns and issues ALTER TABLE statements
    to add any new columns defined in config.COLUMN_SCHEMA that are missing.
    This ensures the schema stays synchronized with config.py across re-runs without
    dropping or truncating existing data.
    """
    cursor.execute("PRAGMA table_info(DailySignals);")
    existing_cols = {row[1] for row in cursor.fetchall()}  # row[1] = column name

    added = []
    for col in config.COLUMN_SCHEMA:
        key      = col["key"]
        col_type = type_map(col["format"], key)
        if key not in existing_cols:
            try:
                cursor.execute(f'ALTER TABLE DailySignals ADD COLUMN "{key}" {col_type};')
                added.append(key)
                logger.info(f"Migration: Added column '{key}' ({col_type}) to DailySignals.")
            except Exception as e:
                logger.warning(f"Could not add column '{key}': {e}")

    if added:
        try:
            conn.commit()
        except Exception:
            pass
        logger.info(f"Schema migration complete. Added {len(added)} new columns: {added}")
    else:
        logger.info("Schema migration: DailySignals is already up-to-date.")


if __name__ == "__main__":
    initialize_database()
