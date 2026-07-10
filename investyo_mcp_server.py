import os
import sys
import subprocess
import sqlite3
import json
from typing import List, Dict, Any
from mcp.server.fastmcp import FastMCP

# Initialize the FastMCP server for the Investyo Platform
mcp = FastMCP("InvestyoPlatform")

# ==========================================
# [1] RESOURCES (Read-Only Context)
# ==========================================

@mcp.resource("investyo://config/read_only_entry")
def get_read_only_entry() -> str:
    """
    Returns the specific platform entry that must remain strictly read-only.
    The AI will use this resource for context but cannot modify it.
    """
    config = {
        "entry_id": "historical_seed_001",
        "status": "read-only",
        "description": "Immutable historical baseline configuration for the Investyo Orchestrator.",
        "permissions": "locked"
    }
    return json.dumps(config, indent=2)

@mcp.resource("investyo://db/schema")
def get_database_schema() -> str:
    """
    Reads and returns the SQLite database schema for quant_platform.db.
    Provides the AI with real-time awareness of the database structure.
    """
    db_path = "quant_platform.db"
    if not os.path.exists(db_path):
        return "Error: quant_platform.db not found in the current directory."
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Query the sqlite_master table to get all table creation schemas
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table';")
        rows = cursor.fetchall()
        schema_definitions = "\n\n".join([row[0] for row in rows if row[0]])
        conn.close()
        return schema_definitions if schema_definitions else "Database is currently empty."
    except Exception as e:
        return f"Database connection error: {str(e)}"

# ==========================================
# [2] TOOLS (Actionable Functions)
# ==========================================

@mcp.tool()
def trigger_data_engine(symbol: str, timeframe: str = "1D") -> str:
    """
    Triggers the data_engine.py module to fetch market data.
    (Replaces the deprecated data_ingestion.py module).
    
    Args:
        symbol: The ticker symbol to fetch (e.g., AAPL).
        timeframe: The timeframe resolution (default: 1D).
    """
    try:
        # Execute the data engine script via subprocess using current virtual environment python
        result = subprocess.run(
            [sys.executable, "data_engine.py", "--symbol", symbol, "--timeframe", timeframe],
            capture_output=True,
            text=True,
            check=True
        )
        return f"Data ingestion successful for {symbol}:\n{result.stdout}"
    except subprocess.CalledProcessError as e:
        return f"Data ingestion failed. Exit code {e.returncode}:\n{e.stderr}"
    except FileNotFoundError:
        return "Error: data_engine.py not found. Ensure you are running the server from the project root."

@mcp.tool()
def generate_html_report(portfolio_id: str) -> str:
    """
    Triggers reporting_engine.py to generate an HTML summary via the reporting package.
    
    Args:
        portfolio_id: The ID of the portfolio to generate the report for.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "reporting.html_publisher", "--portfolio", portfolio_id],
            capture_output=True,
            text=True,
            check=True
        )
        return f"Report generated successfully:\n{result.stdout}"
    except subprocess.CalledProcessError as e:
        return f"Report generation failed:\n{e.stderr}"
    except FileNotFoundError:
        return "Error: reporting/html_publisher.py module not found."

@mcp.tool()
def run_platform_tests() -> str:
    """
    Runs the pytest test suite to ensure the synchronized branch is fully healthy.
    """
    try:
        result = subprocess.run(
            ["pytest"],
            capture_output=True,
            text=True,
            check=True
        )
        return f"Test suite passed successfully:\n{result.stdout}"
    except subprocess.CalledProcessError as e:
        return f"Test suite failed:\nStandard Output:\n{e.stdout}\nError Output:\n{e.stderr}"
    except FileNotFoundError:
        return "Error: pytest is not installed or not found in PATH."

# ==========================================
# [3] SERVER EXECUTION
# ==========================================

if __name__ == "__main__":
    # The server must run via stdio to communicate with the IDE/Host
    mcp.run(transport='stdio')
