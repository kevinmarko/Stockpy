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
            #
            # docs/CONFIG_SCHEMA_PLAN.md Phase C2 finding (re-verified at
            # implementation time, not just the plan's original snapshot):
            # NO PRODUCTION CODE ANYWHERE IN THIS REPO WRITES A ROW TO
            # DailySignals. This table is schema-created and schema-migrated
            # (see migrate_daily_signals_schema() below) on every run of this
            # module, but nothing ever populates it.
            #
            # Verification performed (broader than a literal grep, per the
            # plan's own instruction to search harder):
            #   - Literal grep for "DailySignals" across all production *.py
            #     (excluding tests/): only this module (schema owner),
            #     investyo_mcp_server.py (4 references, ALL read-only SELECT/
            #     PRAGMA queries -- SELECT COUNT(*), SELECT * ... ORDER BY
            #     date DESC LIMIT 1, SELECT MAX(date), SELECT symbol,
            #     composite_score, action, conviction ... -- confirmed by
            #     reading the surrounding function bodies, not just the
            #     matched lines), scripts/preflight_check.py (checks the DB
            #     *file* exists/non-empty, not this table specifically), and
            #     gui/panels/launcher.py (a UI label string, not code).
            #   - Dynamic/f-string SQL construction: grepped for
            #     "INSERT INTO" and ".to_sql(" across all production *.py --
            #     the only INSERT hits are ExecutionLogs (this module),
            #     forecast_errors (forecasting/forecast_tracker.py -- an
            #     unrelated table), and account_snapshots/account_positions
            #     (data/historical_store.py -- also unrelated tables). Zero
            #     ".to_sql(" call sites anywhere in production code.
            #   - git history: `git log -p --all -S "INSERT INTO DailySignals"
            #     -- '*.py'` across ALL branches returns zero hits -- no
            #     writer ever existed and was later removed.
            #   - investyo_mcp_server.py's read queries even reference
            #     columns ("symbol" lowercase, "date", "composite_score",
            #     "conviction") that don't match this table's actual schema
            #     (COLUMN_SCHEMA's keys are "Symbol" capitalized, there is no
            #     "date"/"composite_score"/"conviction" key at all) -- these
            #     reads would themselves error against the table this
            #     function actually creates, further evidence the table was
            #     never wired up end-to-end.
            #
            # transactions_store.py's `trades` table and
            # data/historical_store.py's price_bars/account_snapshots/
            # fundamentals_history/macro_history tables -- both of which
            # post-date this module's original "Step 6" framing (see the
            # module docstring above: "Transitions local flat-file storage to
            # an institutional SQLite schema") -- appear to have superseded
            # whatever DailySignals was originally meant to persist.
            #
            # Per the plan's explicit instruction, this docstring note is
            # NOT accompanied by deleting the schema-creation code below --
            # that is a product/scope decision for a human, not this
            # characterization pass. FOLLOW-UP DECISION NEEDED (flagged in
            # this PR's description): keep as dead-but-harmless schema,
            # wire up a real writer, or remove the table entirely.
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

    docs/CONFIG_SCHEMA_PLAN.md Phase C3: this migration is, and remains,
    ADDITIVE ONLY -- it has never handled renamed or removed COLUMN_SCHEMA
    keys (a renamed key leaves the old column permanently orphaned; a
    removed key's column is silently orphaned forever with no warning).
    After the additive ADD COLUMN loop below, a non-destructive,
    warning-only orphan detector logs any live DailySignals column that no
    longer has a matching COLUMN_SCHEMA key. Columns are NEVER auto-dropped
    -- SQLite's ALTER TABLE DROP COLUMN has been available since 3.35, but a
    destructive schema change is a human decision, never an automatic one
    (CONSTRAINT #4/#6 posture; matches this codebase's "historical/runtime
    data is never destroyed automatically" convention).
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
        except Exception as e:
            logger.error(f"Schema migration commit FAILED: {e}", exc_info=True)
        else:
            logger.info(f"Schema migration complete. Added {len(added)} new columns: {added}")
    else:
        logger.info("Schema migration: DailySignals is already up-to-date.")

    # docs/CONFIG_SCHEMA_PLAN.md Phase C3 -- non-destructive orphan detection.
    # Re-read the table's columns post-migration (rather than reusing
    # existing_cols, which predates the ADD COLUMN loop above) and diff
    # against the CURRENT COLUMN_SCHEMA key set, excluding the two base
    # columns this module always creates itself (id/timestamp -- never part
    # of COLUMN_SCHEMA, never orphaned).
    try:
        cursor.execute("PRAGMA table_info(DailySignals);")
        current_cols = {row[1] for row in cursor.fetchall()}
        current_schema_keys = {c["key"] for c in config.COLUMN_SCHEMA}
        orphaned = sorted(current_cols - current_schema_keys - {"id", "timestamp"})
        if orphaned:
            logger.warning(
                "DailySignals has %d orphaned column(s) no longer in COLUMN_SCHEMA: %s. "
                "These are never dropped automatically (SQLite ALTER TABLE DROP COLUMN "
                "is available since 3.35 but intentionally not used here to avoid "
                "destructive migrations); review and drop manually if confirmed obsolete.",
                len(orphaned), orphaned,
            )
    except Exception as e:
        # Detection is observability-only -- never let it block/fail the
        # (already-successful) additive migration above (CONSTRAINT #6).
        logger.warning(f"Orphaned-column detection skipped due to error: {e}")


if __name__ == "__main__":
    initialize_database()
