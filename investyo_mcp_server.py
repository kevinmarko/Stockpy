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
# [2] PROMPTS (Context Templates)
# ==========================================

@mcp.prompt("investyo_registry")
def investyo_registry_prompt(prompt_id: str) -> str:
    """
    Fetches an official AI instruction prompt from the InvestYo Prompt Registry.
    Valid prompt_ids include: 'master_preprompt', 'gravity_system', etc.
    """
    from prompt_registry import get_registry
    registry = get_registry()
    body = registry.get(prompt_id)
    return f"Here is the official prompt from the registry for '{prompt_id}':\n\n{body}"

@mcp.tool()
def list_registry_prompts() -> str:
    """
    Lists all available prompts in the InvestYo prompt registry baseline.
    """
    from prompt_registry.cache import list_baseline_ids
    ids = list_baseline_ids()
    return "Available Prompt IDs in the registry:\n" + "\n".join(f"- {pid}" for pid in ids)

# ==========================================
# [3] TOOLS (Actionable Functions)
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

@mcp.tool()
def query_investyo_db(sql_query: str) -> str:
    """
    Executes a SELECT query against the quant_platform.db.
    Will reject any query that is not a SELECT statement for safety.
    """
    if not sql_query.strip().upper().startswith("SELECT"):
        return "Error: Only SELECT queries are permitted via this tool."
    
    db_path = "quant_platform.db"
    if not os.path.exists(db_path):
        return "Error: quant_platform.db not found."
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
        conn.close()
        
        if not rows:
            return "Query executed successfully, but returned 0 rows."
        
        result_lines = [", ".join(columns)]
        for row in rows:
            result_lines.append(", ".join(str(val) for val in row))
        
        return "Query Results:\n" + "\n".join(result_lines)
    except Exception as e:
        return f"Database query failed: {str(e)}"

@mcp.tool()
def trigger_forecasting(symbol: str) -> str:
    """
    Triggers the forecasting_engine.py for a specific symbol.
    """
    try:
        result = subprocess.run(
            [sys.executable, "forecasting_engine.py", "--symbol", symbol],
            capture_output=True,
            text=True,
            check=True
        )
        return f"Forecasting successful for {symbol}:\n{result.stdout}"
    except subprocess.CalledProcessError as e:
        return f"Forecasting failed. Exit code {e.returncode}:\n{e.stderr}"
    except FileNotFoundError:
        return "Error: forecasting_engine.py not found."

@mcp.tool()
def trigger_macro_engine() -> str:
    """
    Triggers the macro_engine.py to run the macro-economic analysis pipeline.
    """
    try:
        result = subprocess.run(
            [sys.executable, "macro_engine.py"],
            capture_output=True,
            text=True,
            check=True
        )
        return f"Macro engine run successful:\n{result.stdout}"
    except subprocess.CalledProcessError as e:
        return f"Macro engine run failed:\n{e.stderr}"
    except FileNotFoundError:
        return "Error: macro_engine.py not found."

# ==========================================
# [4] SERVER EXECUTION
# ==========================================

if __name__ == "__main__":
    # The server must run via stdio to communicate with the IDE/Host
    mcp.run(transport='stdio')
