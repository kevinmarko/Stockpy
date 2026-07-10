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

@mcp.resource("investyo://ticker/{symbol}")
def get_ticker_context(symbol: str) -> str:
    """
    Returns a unified, markdown-formatted context for a given stock symbol.
    Fetches recent price history, corporate profile info, and ratios.
    """
    import yfinance as yf
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="10d")
        if history.empty:
            return f"No pricing data found for symbol: {symbol}"
        
        info = ticker.info
        name = info.get("longName", symbol)
        sector = info.get("sector", "N/A")
        pe = info.get("trailingPE", "N/A")
        pb = info.get("priceToBook", "N/A")
        
        summary = f"# Ticker Context: {symbol} ({name})\n"
        summary += f"- **Sector**: {sector}\n"
        summary += f"- **Trailing P/E**: {pe}\n"
        summary += f"- **Price-to-Book**: {pb}\n\n"
        summary += "## Recent Price History (Last 10 Days)\n"
        summary += history[['Open', 'High', 'Low', 'Close', 'Volume']].to_markdown()
        
        return summary
    except Exception as e:
        return f"Error retrieving context for {symbol}: {str(e)}"

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
def run_backtest(symbol: str, period: str = "1y") -> str:
    """
    Runs an event-driven Backtrader simulation for a specific stock symbol
    using the platform's InstitutionalStrategy and transaction cost models.
    
    Args:
        symbol: The stock symbol to backtest (e.g., AAPL).
        period: The backtest lookback period (default: 1y).
    """
    import io
    import contextlib
    import yfinance as yf
    from simulation_engine import run_backtrader_simulation
    
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            return f"Error: No historical data found for {symbol}."
        
        # Standardize column names to lowercase for Backtrader feed
        df.columns = [col.lower() for col in df.columns]
        
        # Capture stdout generated by Backtrader run
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            run_backtrader_simulation(df)
        
        return f"Backtest Results for {symbol} ({period}):\n\n" + f.getvalue()
    except Exception as e:
        return f"Backtest failed: {str(e)}"

@mcp.tool()
def read_platform_logs(lines: int = 50) -> str:
    """
    Retrieves execution logs from the SQLite database (ExecutionLogs table) 
    and checks the directory for any file ending in .log to return recent entries.
    
    Args:
        lines: The number of recent lines to retrieve (default: 50).
    """
    logs_summary = []
    
    # 1. Query ExecutionLogs from DB
    db_path = "quant_platform.db"
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, status, ticker_count, execution_time_seconds, error_message FROM ExecutionLogs ORDER BY id DESC LIMIT ?", (lines,))
            rows = cursor.fetchall()
            conn.close()
            
            if rows:
                logs_summary.append("### Database Execution Logs (Recent runs)")
                logs_summary.append("Timestamp | Status | Tickers | Duration (s) | Error")
                logs_summary.append("---|---|---|---|---")
                for row in rows:
                    err = row[4] if row[4] else "None"
                    logs_summary.append(f"{row[0]} | {row[1]} | {row[2]} | {row[3]:.2f} | {err}")
        except Exception as e:
            logs_summary.append(f"Could not read ExecutionLogs from DB: {str(e)}")
            
    # 2. Check local directory for log files
    log_files = [f for f in os.listdir(".") if f.endswith(".log")]
    if log_files:
        for log_file in log_files:
            try:
                with open(log_file, "r") as f:
                    content = f.readlines()
                recent_lines = content[-lines:]
                logs_summary.append(f"\n### File: {log_file} (Last {len(recent_lines)} lines)")
                logs_summary.append("```\n" + "".join(recent_lines) + "\n```")
            except Exception as e:
                logs_summary.append(f"Could not read log file {log_file}: {str(e)}")
                
    if not logs_summary:
        return "No execution logs found in the database or local directory."
        
    return "\n".join(logs_summary)

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
