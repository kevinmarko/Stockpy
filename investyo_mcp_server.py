import os
import re
import sys
import subprocess
import sqlite3
import json
from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP

# Initialize the FastMCP server for the Investyo Platform
mcp = FastMCP("InvestyoPlatform")


def _active_universe() -> list:
    """
    Returns the active ticker universe from settings.DEFAULT_TICKERS.
    Dead-letter safe: falls back to a small hardcoded default list only
    if settings cannot be read for any reason.
    """
    try:
        from settings import settings
        tickers = list(settings.DEFAULT_TICKERS)
        if not tickers:
            return ["AAPL", "MSFT", "JNJ", "AGNC"]
        return [str(t).upper() for t in tickers]
    except Exception:
        return ["AAPL", "MSFT", "JNJ", "AGNC"]


def _db_query(sql: str, params: tuple = ()):
    """
    Executes a read query against the platform database, transparently
    supporting both the local SQLite file and a configured Postgres/Supabase
    DATABASE_URL (the dual-backend seam in db_config.py).

    Returns a (columns: list[str], rows: list[tuple]) tuple.
    Dead-letter safe: raises only if BOTH backends fail (callers already
    wrap this in try/except per the codebase convention).
    """
    try:
        from db_config import resolve_database_url
        db_url = resolve_database_url()
    except Exception:
        db_url = "sqlite:///quant_platform.db"

    if db_url.startswith("sqlite"):
        # Local sqlite fast path - preserve existing raw sqlite3 behavior.
        db_path = "quant_platform.db"
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"{db_path} not found.")
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return columns, rows
        finally:
            conn.close()
    else:
        # Postgres/Supabase backend via SQLAlchemy.
        from sqlalchemy import text
        from db_config import create_db_engine
        engine = create_db_engine(db_url)
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            columns = list(result.keys())
            rows = [tuple(row) for row in result.fetchall()]
        return columns, rows


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
    try:
        from db_config import resolve_database_url
        db_url = resolve_database_url()
    except Exception:
        db_url = "sqlite:///quant_platform.db"

    if db_url.startswith("sqlite"):
        db_path = "quant_platform.db"
        if not os.path.exists(db_path):
            return "Error: quant_platform.db not found in the current directory."
        try:
            _, rows = _db_query("SELECT sql FROM sqlite_master WHERE type='table';")
            schema_definitions = "\n\n".join([row[0] for row in rows if row[0]])
            return schema_definitions if schema_definitions else "Database is currently empty."
        except Exception as e:
            return f"Database connection error: {str(e)}"
    else:
        try:
            _, rows = _db_query(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position;"
            )
            if not rows:
                return "Database is currently empty."
            tables: Dict[str, List[str]] = {}
            for table_name, column_name, data_type in rows:
                tables.setdefault(table_name, []).append(f"{column_name} {data_type}")
            lines = []
            for table_name, cols in tables.items():
                lines.append(f"TABLE {table_name} (\n  " + ",\n  ".join(cols) + "\n)")
            return "\n\n".join(lines)
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
    Refreshes persisted OHLCV bars for a symbol IN-PROCESS via the platform's
    HistoricalStore (DB-cached, incremental fetch through the market-data provider).
    No subprocess: data_engine.py has no CLI entrypoint.

    Args:
        symbol: The ticker symbol to fetch (e.g., AAPL).
        timeframe: Cosmetic only — HistoricalStore bars are DAILY resolution (default: 1D).
    """
    try:
        from data.historical_store import HistoricalStore
        from data.market_data import get_provider
        from settings import settings

        sym = symbol.upper().strip()
        df = HistoricalStore().get_bars(
            sym, lookback_days=settings.BARS_BACKFILL_DAYS, provider=get_provider()
        )
        if df is None or df.empty:
            return (
                f"Bar refresh for {sym} returned no rows (provider unavailable or "
                f"unknown symbol). No data was fabricated."
            )
        last_date = df.index[-1]
        last_str = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)
        return (
            f"Bar refresh successful for {sym} (daily bars): {len(df)} rows persisted, "
            f"last bar date {last_str}."
        )
    except Exception as e:
        return f"Data ingestion failed for {symbol}: {str(e)}"

@mcp.tool()
def generate_html_report(portfolio_id: str) -> str:
    """
    Runs the advisory orchestrator (main.py) end-to-end, which internally calls
    reporting/html_publisher.py::write_html_report -> diagnostics_and_visuals.generate_html_report
    to produce the daily HTML report. (The old reporting_engine.py this tool used to
    reference was deleted 2026-07-09; there is no standalone reporting-only entrypoint —
    the report is a side effect of a full advisory run.)

    Args:
        portfolio_id: Currently ignored — main.py's advisory report always covers the
            full active universe/held account, not a specific portfolio_id.
    """
    try:
        from settings import settings

        result = subprocess.run(
            [sys.executable, "main.py"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        report_path = settings.OUTPUT_DIR / "daily_report.html"
        report_exists = report_path.exists()

        if result.returncode != 0:
            return (
                f"Advisory run failed (exit {result.returncode}); HTML report was "
                f"{'still' if report_exists else 'NOT'} found at {report_path}.\n"
                f"stderr:\n{result.stderr[-2000:]}"
            )
        if report_exists:
            return (
                f"Advisory run completed and HTML report generated at: {report_path}\n"
                f"(portfolio_id '{portfolio_id}' is currently ignored by this pipeline.)"
            )
        return (
            "Advisory run completed (exit 0) but no daily_report.html was found at "
            f"{report_path} — report generation may have failed non-fatally. Check logs."
        )
    except subprocess.TimeoutExpired:
        return "Report generation timed out after 15 minutes."
    except Exception as e:
        return f"Report generation failed: {str(e)}"

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
    Executes a read-only SELECT (or WITH-CTE SELECT) query against the platform database.
    Will reject any query that is not a SELECT/WITH statement for safety, and caps
    results at 1000 rows to avoid dumping an entire table.
    """
    stripped_upper = sql_query.strip().upper()
    if not (stripped_upper.startswith("SELECT") or stripped_upper.startswith("WITH")):
        return "Error: Only SELECT queries are permitted via this tool (WITH-CTE SELECT statements are also allowed)."

    # A leading WITH must not be a bypass for a trailing mutation smuggled in
    # after the CTE (e.g. "WITH x AS (SELECT 1) INSERT INTO T VALUES (1)" is
    # valid SQLite syntax). Scan the whole statement, not just the prefix.
    _MUTATION_KEYWORDS = (
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
        "CREATE", "REPLACE", "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM",
    )
    if any(re.search(rf"\b{kw}\b", stripped_upper) for kw in _MUTATION_KEYWORDS):
        return "Error: Only SELECT queries are permitted via this tool (WITH-CTE SELECT statements are also allowed)."

    MAX_ROWS = 1000

    try:
        columns, rows = _db_query(sql_query)

        if not rows:
            return "Query executed successfully, but returned 0 rows."

        truncated = len(rows) > MAX_ROWS
        if truncated:
            rows = rows[:MAX_ROWS]

        result_lines = [", ".join(columns)]
        for row in rows:
            result_lines.append(", ".join(str(val) for val in row))

        output = "Query Results:\n" + "\n".join(result_lines)
        if truncated:
            output += f"\n\n[Note: results truncated to the first {MAX_ROWS} rows.]"
        return output
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
    try:
        _, rows = _db_query(
            "SELECT timestamp, status, ticker_count, execution_time_seconds, error_message "
            "FROM ExecutionLogs ORDER BY id DESC LIMIT ?",
            (lines,),
        )

        if rows:
            logs_summary.append("### Database Execution Logs (Recent runs)")
            logs_summary.append("Timestamp | Status | Tickers | Duration (s) | Error")
            logs_summary.append("---|---|---|---|---")
            for row in rows:
                err = row[4] if row[4] else "None"
                logs_summary.append(f"{row[0]} | {row[1]} | {row[2]} | {row[3]:.2f} | {err}")
    except FileNotFoundError:
        pass  # No local DB and no configured remote backend - nothing to report.
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
def execute_paper_trade(
    symbol: str,
    side: str,
    price: float,
    shares: float,
    strategy: Optional[str] = None,
    notes: Optional[str] = None,
    conviction: Optional[float] = None
) -> str:
    """
    Submits a simulated paper trade (records a new open trade) or closes an open trade in the TransactionsStore.
    
    Args:
        symbol: The stock ticker (e.g. AAPL).
        side: The trade direction: 'buy'/'long' to open a long position, 'sell'/'short' to open a short position, or 'close' to close the position.
        price: Execution price for entry or exit.
        shares: Number of shares.
        strategy: Optional strategy identifier (e.g. 'RSI2').
        notes: Optional custom notes.
        conviction: Optional signal conviction level [0, 1].
    """
    from transactions_store import TransactionsStore
    from datetime import datetime
    
    store = TransactionsStore()
    symbol_upper = symbol.upper().strip()
    side_lower = side.lower().strip()
    
    if side_lower in ["buy", "long", "sell", "short"]:
        db_side = "long" if side_lower in ["buy", "long"] else "short"
        try:
            trade_id = store.record_trade(
                symbol=symbol_upper,
                side=db_side,
                entry_ts=datetime.now(),
                entry_price=price,
                shares=shares,
                strategy=strategy,
                notes=notes,
                conviction=conviction
            )
            return f"Paper trade recorded successfully. Opened {db_side} position for {symbol_upper}: {shares} shares at ${price:.2f}. Trade ID: {trade_id}."
        except Exception as e:
            return f"Failed to record paper trade: {str(e)}"
            
    elif side_lower == "close":
        try:
            df = store.open_trades_df()
            if df.empty or symbol_upper not in df['symbol'].values:
                return f"No open paper trades found for symbol: {symbol_upper} to close."
            
            symbol_trades = df[df['symbol'] == symbol_upper]
            trade_id = int(symbol_trades.iloc[-1]['trade_id'])
            
            store.close_trade(trade_id=trade_id, exit_ts=datetime.now(), exit_price=price)
            return f"Closed paper trade ID {trade_id} for {symbol_upper} at ${price:.2f} successfully."
        except Exception as e:
            return f"Failed to close paper trade: {str(e)}"
    else:
        return f"Invalid side: '{side}'. Must be one of: buy, long, sell, short, close."

@mcp.tool()
def update_watch_rules(
    action: str,
    symbol: str,
    alert_on: Optional[str] = None,
    threshold: Optional[float] = None,
    priority: Optional[str] = None,
    label: Optional[str] = None
) -> str:
    """
    Safely adds, updates, or removes watch rules in watch_rules.yaml.
    
    Args:
        action: 'add', 'update', or 'remove'.
        symbol: The ticker symbol (e.g. TSLA, or '*' for wildcard).
        alert_on: Rule trigger type (e.g. 'conviction_above', 'conviction_below', 'action_change').
        threshold: Trigger threshold (float between 0.0 and 1.0, required for conviction triggers).
        priority: Notification priority ('min', 'low', 'default', 'high', 'urgent', 'max').
        label: Custom human-readable label for notifications.
    """
    import yaml
    
    yaml_path = "watch_rules.yaml"
    if not os.path.exists(yaml_path):
        return f"Error: {yaml_path} not found."
        
    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f) or {"rules": []}
    except Exception as e:
        return f"Failed to read watch_rules.yaml: {str(e)}"
        
    rules = data.get("rules", [])
    symbol_upper = symbol.upper().strip()
    action_lower = action.lower().strip()
    
    if action_lower == "remove":
        new_rules = [r for r in rules if str(r.get("symbol")).upper().strip() != symbol_upper]
        if len(new_rules) == len(rules):
            return f"No watch rules found for symbol: {symbol_upper}."
        data["rules"] = new_rules
        try:
            with open(yaml_path, "w") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            return f"Successfully removed all watch rules for {symbol_upper}."
        except Exception as e:
            return f"Failed to write watch_rules.yaml: {str(e)}"
            
    elif action_lower in ["add", "update"]:
        if not alert_on:
            return "Error: 'alert_on' is required to add or update a rule."
        
        new_rule = {"symbol": symbol_upper if symbol_upper != "*" else "*", "alert_on": alert_on}
        if threshold is not None:
            new_rule["threshold"] = float(threshold)
        if priority:
            new_rule["priority"] = priority
        if label:
            new_rule["label"] = label
            
        if action_lower == "update":
            rules = [r for r in rules if str(r.get("symbol")).upper().strip() != symbol_upper]
            
        rules.append(new_rule)
        data["rules"] = rules
        
        try:
            with open(yaml_path, "w") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            return f"Successfully {action_lower}ed watch rule for {symbol_upper}."
        except Exception as e:
            return f"Failed to write watch_rules.yaml: {str(e)}"
    else:
        return f"Invalid action: '{action}'. Must be one of: add, update, remove."

@mcp.tool()
def update_universe_tickers(action: str, symbol: str) -> str:
    """
    Adds or removes a stock symbol from the active trading universe configured in the .env file.
    
    Args:
        action: 'add' or 'remove'.
        symbol: The ticker symbol to modify (e.g. TSLA).
    """
    import json
    from gui import env_io

    symbol_upper = symbol.upper().strip()
    action_lower = action.lower().strip()

    try:
        raw_val = env_io.get_value("DEFAULT_TICKERS", "[]")
    except Exception as e:
        return f"Failed to read DEFAULT_TICKERS setting: {str(e)}"

    try:
        current_tickers = json.loads(raw_val)
        if not isinstance(current_tickers, list):
            current_tickers = [current_tickers]
    except Exception:
        current_tickers = [t.strip() for t in raw_val.split(",") if t.strip()]

    current_tickers = [str(t).upper() for t in current_tickers]

    if action_lower == "add":
        if symbol_upper in current_tickers:
            return f"{symbol_upper} is already in the trading universe."
        current_tickers.append(symbol_upper)
    elif action_lower == "remove":
        if symbol_upper not in current_tickers:
            return f"{symbol_upper} is not in the trading universe."
        current_tickers.remove(symbol_upper)
    else:
        return f"Invalid action: '{action}'. Must be one of: add, remove."

    # Dedup while preserving order
    deduped = list(dict.fromkeys(current_tickers))

    try:
        env_io.write_setting("DEFAULT_TICKERS", deduped)
        return f"Successfully {action_lower}ed {symbol_upper} from the active universe. Current tickers: {deduped}"
    except env_io.SecretWriteError as e:
        return f"Failed to update universe: DEFAULT_TICKERS write blocked ({str(e)})."
    except env_io.DisallowedKeyError as e:
        return f"Failed to update universe: DEFAULT_TICKERS is not an allowed key ({str(e)})."
    except Exception as e:
        return f"Failed to write DEFAULT_TICKERS setting: {str(e)}"

@mcp.tool()
def plot_equity_curve(symbol: str, period: str = "1y") -> str:
    """
    Runs a Backtrader simulation on the given stock symbol and generates a PNG plot
    of its equity curve over time, saving it to the artifacts directory.
    
    Args:
        symbol: The stock symbol to simulate (e.g. AAPL).
        period: The lookback period (default: 1y).
    """
    import io
    import contextlib
    import yfinance as yf
    import backtrader as bt
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from simulation_engine import InstitutionalStrategy
    
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            return f"Error: No data found for {symbol}."
            
        df.columns = [col.lower() for col in df.columns]
        
        cerebro = bt.Cerebro()
        cerebro.addstrategy(InstitutionalStrategy)
        
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data)
        
        cerebro.broker.setcash(100000.0)
        cerebro.broker.setcommission(commission=0.001)
        cerebro.broker.set_slippage_perc(perc=0.0005)
        
        cerebro.addanalyzer(bt.analyzers.TimeReturn, _name='timereturn')
        
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            results = cerebro.run()
            
        strat = results[0]
        time_return = strat.analyzers.timereturn.get_analysis()
        
        import numpy as np
        dates = sorted(time_return.keys())
        returns = [time_return[d] for d in dates]
        equity = 100000.0 * np.cumprod(1.0 + np.array(returns))
        
        if len(equity) == 0:
            return f"Error: Simulation did not produce any equity results. This may happen if the lookback period ('{period}') is too short to compute indicators (e.g. 50-day SMA requires at least 50 bars)."
        
        plt.figure(figsize=(10, 5))
        plt.plot(dates, equity, label="Strategy Equity", color="blue", linewidth=2)
        plt.title(f"Equity Curve - {symbol.upper()} ({period})")
        plt.xlabel("Date")
        plt.ylabel("Portfolio Value ($)")
        plt.grid(True, linestyle="--", alpha=0.5)
        plt.legend()
        plt.tight_layout()
        
        from settings import settings
        artifact_dir = str(settings.OUTPUT_DIR / "artifacts")
        os.makedirs(artifact_dir, exist_ok=True)
        img_name = f"equity_curve_{symbol.lower()}.png"
        img_path = os.path.join(artifact_dir, img_name)
        plt.savefig(img_path)
        plt.close()
        
        markdown_response = (
            f"### Equity Curve for {symbol.upper()} ({period})\n"
            f"Successfully simulated InstitutionalStrategy. Final Portfolio Value: ${equity[-1]:,.2f}\n\n"
            f"![Equity Curve for {symbol.upper()}](file://{img_path})\n"
        )
        return markdown_response
        
    except Exception as e:
        return f"Plot generation failed: {str(e)}"

@mcp.tool()
def get_portfolio_summary() -> str:
    """
    Summarizes the active paper trading portfolio: calculates current holdings,
    realized and unrealized P&L, win rate, and total portfolio performance metrics.
    """
    from transactions_store import TransactionsStore
    import yfinance as yf
    import pandas as pd
    
    try:
        store = TransactionsStore()
        open_df = store.open_trades_df()
        closed_df = store.closed_trades_df()
        
        summary = ["# Paper Portfolio Summary\n"]
        
        # 1. Open Positions (Holdings)
        unrealized_pl = 0.0
        holdings_value = 0.0
        
        if not open_df.empty:
            summary.append("## Current Holdings")
            holdings_rows = []
            unique_symbols = open_df['symbol'].unique().tolist()
            current_prices = {}
            if unique_symbols:
                tickers = yf.Tickers(" ".join(unique_symbols))
                for sym in unique_symbols:
                    try:
                        current_prices[sym] = tickers.tickers[sym].history(period="1d")['Close'].iloc[-1]
                    except Exception:
                        current_prices[sym] = None
            
            for _, row in open_df.iterrows():
                symbol = row['symbol']
                side = row['side']
                entry_price = row['entry_price']
                shares = row['shares']
                curr_price = current_prices.get(symbol)
                
                if curr_price is not None:
                    value = curr_price * shares
                    if side == "long":
                        pl = (curr_price - entry_price) * shares
                    else:
                        pl = (entry_price - curr_price) * shares
                else:
                    curr_price = 0.0
                    value = 0.0
                    pl = 0.0
                    
                unrealized_pl += pl
                holdings_value += value
                
                holdings_rows.append({
                    "Trade ID": row['trade_id'],
                    "Symbol": symbol,
                    "Side": side.upper(),
                    "Shares": shares,
                    "Avg Cost": f"${entry_price:.2f}",
                    "Current Price": f"${curr_price:.2f}" if curr_price > 0 else "N/A",
                    "Value": f"${value:,.2f}" if value > 0 else "N/A",
                    "Unrealized P&L": f"${pl:+,.2f}"
                })
            
            summary.append(pd.DataFrame(holdings_rows).to_markdown(index=False) + "\n")
        else:
            summary.append("No open positions.\n")
            
        # 2. Closed Positions (History Summary)
        realized_pl = 0.0
        win_count = 0
        total_closed = len(closed_df)
        
        if not closed_df.empty:
            for _, row in closed_df.iterrows():
                side = row['side']
                entry_price = row['entry_price']
                exit_price = row['exit_price']
                shares = row['shares']
                
                if side == "long":
                    pl = (exit_price - entry_price) * shares
                else:
                    pl = (entry_price - exit_price) * shares
                    
                realized_pl += pl
                if pl > 0:
                    win_count += 1
                    
            win_rate = (win_count / total_closed) * 100 if total_closed > 0 else 0.0
            
            summary.append("## Closed Trades Analytics")
            summary.append(f"- **Total Closed Trades**: {total_closed}")
            summary.append(f"- **Win Rate**: {win_rate:.1f}%")
            summary.append(f"- **Realized P&L**: ${realized_pl:+,.2f}\n")
        else:
            summary.append("## Closed Trades Analytics\nNo closed trades recorded yet.\n")
            
        # 3. Overall Performance
        total_pl = realized_pl + unrealized_pl
        summary.append("## Account Metrics")
        summary.append(f"- **Net Profit/Loss**: ${total_pl:+,.2f}")
        summary.append(f"- **Total Unrealized P&L**: ${unrealized_pl:+,.2f}")
        summary.append(f"- **Total Open Holdings Value**: ${holdings_value:,.2f}")
        
        return "\n".join(summary)
    except Exception as e:
        return f"Failed to retrieve portfolio summary: {str(e)}"

@mcp.tool()
def plot_portfolio_equity(period: str = "1y") -> str:
    """
    Runs the InstitutionalStrategy on all active universe tickers, merges their equity curves
    into a unified portfolio equity curve (equally weighted), overlays the SPY benchmark,
    and saves the PNG plot to artifacts.
    """
    import os
    import yfinance as yf
    import backtrader as bt
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from simulation_engine import InstitutionalStrategy

    current_tickers = _active_universe()

    try:
        portfolio_curves = []
        
        for symbol in current_tickers:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            if df.empty:
                continue
            df.columns = [col.lower() for col in df.columns]
            
            cerebro = bt.Cerebro()
            cerebro.addstrategy(InstitutionalStrategy)
            data = bt.feeds.PandasData(dataname=df)
            cerebro.adddata(data)
            cerebro.broker.setcash(100000.0)
            cerebro.broker.setcommission(commission=0.001)
            cerebro.broker.set_slippage_perc(perc=0.0005)
            cerebro.addanalyzer(bt.analyzers.TimeReturn, _name='timereturn')
            
            import io
            import contextlib
            f = io.StringIO()
            with contextlib.redirect_stdout(f):
                results = cerebro.run()
                
            strat = results[0]
            time_return = strat.analyzers.timereturn.get_analysis()
            
            dates = sorted(time_return.keys())
            returns = [time_return[d] for d in dates]
            series = pd.Series(returns, index=pd.to_datetime(dates))
            portfolio_curves.append(series)
            
        if not portfolio_curves:
            return "Error: No tickers could be simulated."
            
        combined_returns = pd.concat(portfolio_curves, axis=1).mean(axis=1)
        portfolio_equity = 100000.0 * np.cumprod(1.0 + combined_returns.values)
        portfolio_series = pd.Series(portfolio_equity, index=combined_returns.index)
        
        spy = yf.Ticker("SPY")
        spy_df = spy.history(period=period)
        spy_returns = spy_df['Close'].pct_change().dropna()
        spy_aligned = spy_returns.reindex(portfolio_series.index).fillna(0.0)
        spy_equity = 100000.0 * np.cumprod(1.0 + spy_aligned.values)
        spy_series = pd.Series(spy_equity, index=portfolio_series.index)
        
        plt.figure(figsize=(12, 6))
        plt.plot(portfolio_series.index, portfolio_series.values, label="InvestYo Portfolio Strategy", color="blue", linewidth=2)
        plt.plot(spy_series.index, spy_series.values, label="SP500 (SPY)", color="orange", linestyle="--", linewidth=1.5)
        plt.title(f"Portfolio Strategy vs. SPY Benchmark ({period})")
        plt.xlabel("Date")
        plt.ylabel("Portfolio Value ($)")
        plt.grid(True, linestyle="--", alpha=0.5)
        plt.legend()
        plt.tight_layout()
        
        from settings import settings
        artifact_dir = str(settings.OUTPUT_DIR / "artifacts")
        os.makedirs(artifact_dir, exist_ok=True)
        img_path = os.path.join(artifact_dir, "portfolio_equity_vs_spy.png")
        plt.savefig(img_path)
        plt.close()
        
        port_ret = (portfolio_series.iloc[-1] / 100000.0 - 1.0) * 100
        spy_ret = (spy_series.iloc[-1] / 100000.0 - 1.0) * 100
        
        markdown_response = (
            f"### Portfolio Strategy Performance vs SPY Benchmark ({period})\n"
            f"- **Unified Strategy Return**: {port_ret:+.2f}%\n"
            f"- **SPY Benchmark Return**: {spy_ret:+.2f}%\n\n"
            f"![Portfolio vs SPY](file://{img_path})\n"
        )
        return markdown_response
        
    except Exception as e:
        return f"Portfolio plot generation failed: {str(e)}"

@mcp.tool()
def get_universe_status() -> str:
    """
    Returns a status dashboard of the current trading universe, active watch rules,
    macro economic environment status, and database stats.
    """
    import os
    import yaml

    status = ["# InvestYo Universe Status Dashboard\n"]

    current_tickers = _active_universe()

    status.append("## Active Trading Universe")
    status.append(", ".join(f"`{t}`" for t in current_tickers) + "\n")
    
    yaml_path = "watch_rules.yaml"
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, "r") as f:
                data = yaml.safe_load(f)
            rules = data.get("rules", []) if data else []
            if rules:
                status.append("## Active Watch Rules")
                status.append("Symbol | Alert Trigger | Threshold | Priority | Label")
                status.append("---|---|---|---|---")
                for r in rules:
                    threshold = f"{r.get('threshold'):.2f}" if r.get('threshold') is not None else "N/A"
                    status.append(f"`{r.get('symbol')}` | {r.get('alert_on')} | {threshold} | {r.get('priority', 'default')} | {r.get('label', 'N/A')}")
                status.append("")
            else:
                status.append("## Active Watch Rules\nNo watch rules configured.\n")
        except Exception as e:
            status.append(f"## Active Watch Rules\nFailed to parse rules: {str(e)}\n")
            
    try:
        _, signals_rows = _db_query("SELECT COUNT(*) FROM DailySignals")
        signals_count = signals_rows[0][0] if signals_rows else 0

        _, trades_rows = _db_query("SELECT COUNT(*) FROM trades")
        trades_count = trades_rows[0][0] if trades_rows else 0

        _, logs_rows = _db_query("SELECT COUNT(*) FROM ExecutionLogs")
        logs_count = logs_rows[0][0] if logs_rows else 0

        status.append("## Database Metrics")
        status.append(f"- **Daily Signals Table Rows**: {signals_count}")
        status.append(f"- **Trades Table Rows**: {trades_count}")
        status.append(f"- **Execution Logs Table Rows**: {logs_count}")
    except Exception as e:
        status.append(f"## Database Metrics\nError querying DB stats: {str(e)}")
            
    return "\n".join(status)

@mcp.tool()
def trigger_forecasting(symbol: str) -> str:
    """
    Runs the platform's real per-symbol forecast IN-PROCESS via the advisory engine
    (engine.advisory.evaluate), which internally runs the full ARIMA/Monte-Carlo/
    Holt-Winters/CNN-LSTM blended ensemble. There is no forecasting_engine.py CLI entrypoint.

    Args:
        symbol: The ticker symbol to forecast (e.g., AAPL).
    """
    try:
        from engine.advisory import evaluate
        from data.market_data import get_provider

        sym = symbol.upper().strip()
        rec = evaluate(sym, position=None, market=get_provider(), snapshot=None)

        forecast_str = f"${rec.forecast:,.2f}" if rec.forecast is not None else "unavailable"
        return (
            f"# Forecast: {sym}\n\n"
            f"- **30-day blended forecast**: {forecast_str}\n"
            f"- **Action**: {rec.action}\n"
            f"- **Conviction**: {rec.conviction:.2f}\n"
            f"- **Strategy**: {rec.strategy}\n"
            f"- **Data quality**: {rec.data_quality}\n"
            f"- **Rationale**: {rec.rationale}\n"
        )
    except Exception as e:
        return f"Forecasting failed for {symbol}: {str(e)}"

@mcp.tool()
def trigger_macro_engine() -> str:
    """
    Runs the macro-economic regime pipeline in-process (macro_engine.py has no
    CLI entrypoint, so shelling to `python macro_engine.py` used to silently
    no-op while reporting success).
    """
    try:
        from settings import settings
        from data_engine import DataEngine
        from macro_engine import MacroEngine

        de = DataEngine(fred_api_key=settings.FRED_API_KEY)
        engine = MacroEngine(de)
        macro_raw = de.fetch_macro_raw()
        sahm_val = engine.calculate_sahm_rule()
        macro_df = engine.run_macro_killswitch(macro_raw, sahm_val)
        regime = macro_df["market_regime"].iloc[0] if not macro_df.empty else "UNKNOWN"
        return (
            f"Macro engine run successful:\n"
            f"VIX={macro_raw.get('VIXCLS')}, Sahm={sahm_val}, regime={regime}"
        )
    except Exception as e:
        return f"Macro engine run failed: {str(e)}"

# ==========================================
# [4] PHASE 1 — DATA & INGESTION MANAGEMENT
# ==========================================

@mcp.tool()
def trigger_edgar_backfill(tickers: str = "all", since: str = "2015-01-01") -> str:
    """
    Triggers the SEC EDGAR PIT fundamentals backfill script.

    Args:
        tickers: Comma-separated ticker list (e.g., "AAPL,MSFT") or "all" for the full universe.
        since: Earliest filing date to backfill from (default: 2015-01-01).
    """
    try:
        from settings import settings

        tickers_stripped = tickers.strip().lower()
        if tickers_stripped in ("", "all"):
            ticker_list = [t.upper() for t in settings.DEFAULT_TICKERS]
            if not ticker_list:
                return (
                    "EDGAR backfill aborted: tickers='all' was requested but "
                    "settings.DEFAULT_TICKERS is empty — no universe to resolve. "
                    "Pass explicit tickers or configure DEFAULT_TICKERS."
                )
        else:
            ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

        cmd = [
            sys.executable, "scripts/backfill_edgar_fundamentals.py",
            "--since", since,
            "--tickers", ",".join(ticker_list),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return f"EDGAR backfill completed successfully:\n{output}"
        return f"EDGAR backfill exited with code {result.returncode}:\n{output}"
    except subprocess.TimeoutExpired:
        return "EDGAR backfill timed out after 10 minutes. Consider running with fewer tickers."
    except Exception as e:
        return f"EDGAR backfill failed: {str(e)}"


@mcp.tool()
def trigger_full_pipeline(tickers: str = "") -> str:
    """
    Orchestrates a complete data refresh cycle: price fetch, EDGAR fundamentals,
    macro indicators, and signal aggregation for the given tickers.

    Args:
        tickers: Comma-separated ticker list. If empty, uses the active universe.
    """
    from settings import settings

    steps = []
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else None
    if not ticker_list:
        ticker_list = [t.upper() for t in settings.DEFAULT_TICKERS]

    # Step 1: Price bars — in-process via HistoricalStore (data_engine.py has no CLI entrypoint)
    try:
        if not ticker_list:
            steps.append("❌ bar_refresh: no tickers resolved (universe and DEFAULT_TICKERS both empty)")
        else:
            from data.historical_store import HistoricalStore
            from data.market_data import get_provider

            provider = get_provider()
            store = HistoricalStore()
            ok_count = 0
            fail_syms = []
            for sym in ticker_list:
                try:
                    df = store.get_bars(sym, lookback_days=settings.BARS_BACKFILL_DAYS, provider=provider)
                    if df is not None and not df.empty:
                        ok_count += 1
                    else:
                        fail_syms.append(sym)
                except Exception:
                    fail_syms.append(sym)
            if ok_count > 0:
                msg = f"✅ bar_refresh: {ok_count}/{len(ticker_list)} symbols OK"
                if fail_syms:
                    msg += f" (no data for: {', '.join(fail_syms)})"
                steps.append(msg)
            else:
                steps.append(f"❌ bar_refresh: no bars fetched for any of {ticker_list}")
    except Exception as e:
        steps.append(f"❌ bar_refresh: {str(e)}")

    # Step 2: EDGAR fundamentals — --tickers is required by the real script
    try:
        cmd = [
            sys.executable, "scripts/backfill_edgar_fundamentals.py",
            "--since", "2020-01-01",
            "--tickers", ",".join(ticker_list) if ticker_list else "",
        ]
        if not ticker_list:
            steps.append("❌ edgar_backfill: no tickers resolved (universe and DEFAULT_TICKERS both empty)")
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            steps.append(
                f"✅ edgar_backfill: OK ({', '.join(ticker_list)})" if result.returncode == 0
                else f"❌ edgar_backfill: {result.stderr[:200]}"
            )
    except Exception as e:
        steps.append(f"❌ edgar_backfill: {str(e)}")

    # Step 3: Macro engine — in-process via MacroEngine (macro_engine.py has no CLI entrypoint)
    try:
        from data_engine import DataEngine
        from macro_engine import MacroEngine

        de = DataEngine(fred_api_key=settings.FRED_API_KEY)
        engine = MacroEngine(de)
        macro_raw = de.fetch_macro_raw()
        sahm_val = engine.calculate_sahm_rule()
        macro_df = engine.run_macro_killswitch(macro_raw, sahm_val)
        regime = macro_df["market_regime"].iloc[0] if not macro_df.empty else "UNKNOWN"
        steps.append(
            f"✅ macro_engine: OK (VIX={macro_raw.get('VIXCLS')}, "
            f"Sahm={sahm_val}, regime={regime})"
        )
    except Exception as e:
        steps.append(f"❌ macro_engine: {str(e)}")

    return "# Full Pipeline Refresh\n\n" + "\n".join(steps)


@mcp.tool()
def get_pit_coverage_report() -> str:
    """
    Returns a markdown table showing PIT fundamental data coverage per symbol:
    rows, earliest and latest report dates.
    """
    try:
        from data.historical_store import HistoricalStore
        from validation.pit_fundamentals import generate_coverage_report

        store = HistoricalStore()
        df = generate_coverage_report(store)
        if df.empty:
            return "No PIT fundamental data found in the database."
        return "# PIT Fundamentals Coverage Report\n\n" + df.to_markdown(index=False)
    except Exception as e:
        return f"Coverage report failed: {str(e)}"


# ==========================================
# [5] PHASE 2 — QUANTITATIVE RESEARCH & ML
# ==========================================

@mcp.tool()
def run_validation_harness(strategy_name: str = "", start_date: str = "2020-01-01", end_date: str = "2024-12-31") -> str:
    """
    Triggers the StrategyValidationHarness (scripts/refresh_validations.py) and returns
    structured results including Sharpe ratio, max drawdown, DSR, PBO, and deployability.

    Args:
        strategy_name: Comma-separated strategy name(s) registered in STRATEGY_REGISTRY
            (e.g. "rsi2_mean_reversion" or "rsi2_mean_reversion,macd_trend"). Leave empty,
            or pass "default"/"all", to validate EVERY registered strategy.
        start_date: Backtest start date (YYYY-MM-DD).
        end_date: Backtest end date (YYYY-MM-DD).
    """
    try:
        name_stripped = strategy_name.strip().lower()
        cmd = [
            sys.executable, "-m", "scripts.refresh_validations",
            "--start", start_date,
            "--end", end_date,
            "--json",
        ]
        if name_stripped not in ("", "default", "all"):
            cmd.extend(["--strategies", strategy_name.strip()])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        label = strategy_name.strip() if name_stripped not in ("", "default", "all") else "ALL REGISTERED STRATEGIES"

        if result.returncode == 0:
            return f"# Validation Harness Results: {label}\n\n{result.stdout}"
        return (
            f"Validation harness failed (exit {result.returncode}) for {label}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    except subprocess.TimeoutExpired:
        return "Validation harness timed out after 10 minutes."
    except Exception as e:
        return f"Validation harness error: {str(e)}"


@mcp.tool()
def run_pit_audit(symbol: str, decision_date: str) -> str:
    """
    Runs a Point-in-Time audit for a symbol at a given decision date.
    Returns PASS, FAIL, or UNVERIFIABLE with full reasoning.

    Args:
        symbol: Stock ticker (e.g., AAPL).
        decision_date: The date the investment decision was made (YYYY-MM-DD).
    """
    try:
        from data.historical_store import HistoricalStore
        from validation.pit_fundamentals import audit_from_historical_store

        store = HistoricalStore()
        result = audit_from_historical_store(store, symbol, decision_date)
        return (
            f"# PIT Audit: {symbol} @ {decision_date}\n\n"
            f"- **Verdict**: {result.verdict}\n"
            f"- **Report Date**: {result.report_date or 'N/A'}\n"
            f"- **Fields Checked**: {', '.join(result.fields_checked) if result.fields_checked else 'default'}\n"
            f"- **Reason**: {result.reason or 'N/A'}\n"
            f"- **Error**: {result.error or 'None'}\n"
        )
    except Exception as e:
        return f"PIT audit failed: {str(e)}"


@mcp.tool()
def run_lookahead_check(symbol: str, decision_date: str) -> str:
    """
    Verifies that querying fundamentals at decision_date is strictly isolated
    from future filings by injecting and testing against a lookahead payload.

    Args:
        symbol: Stock ticker (e.g., AAPL).
        decision_date: The date to verify isolation for (YYYY-MM-DD).
    """
    try:
        from data.historical_store import HistoricalStore
        from validation.pit_fundamentals import audit_no_lookahead_sample

        store = HistoricalStore()
        is_isolated = audit_no_lookahead_sample(store, symbol, decision_date)
        verdict = "✅ ISOLATED (no lookahead bias)" if is_isolated else "❌ CONTAMINATED (lookahead detected!)"
        return f"# Lookahead Check: {symbol} @ {decision_date}\n\n**Result**: {verdict}"
    except Exception as e:
        return f"Lookahead check failed: {str(e)}"


@mcp.tool()
def get_signal_breakdown(symbol: str) -> str:
    """
    Returns the full composite signal decomposition for a ticker,
    including individual signal scores, factor weights, and final conviction.

    Args:
        symbol: Stock ticker (e.g., AAPL).
    """
    try:
        columns, rows = _db_query(
            """SELECT * FROM DailySignals
               WHERE symbol = ?
               ORDER BY date DESC LIMIT 1""",
            (symbol.upper(),)
        )
        if not rows:
            return f"No signals found for {symbol.upper()} in the database."

        row = rows[0]
        data = dict(zip(columns, row))
        lines = [f"# Signal Breakdown: {symbol.upper()} ({data.get('date', 'N/A')})\n"]

        # Separate signal columns from metadata
        meta_keys = {"symbol", "date", "id", "created_at"}
        signal_keys = [k for k in columns if k not in meta_keys]

        for key in signal_keys:
            val = data.get(key)
            if val is not None:
                lines.append(f"- **{key}**: {val}")

        return "\n".join(lines)
    except Exception as e:
        return f"Signal breakdown failed: {str(e)}"


@mcp.tool()
def compare_strategies(strategy_a: str, strategy_b: str, start_date: str = "2020-01-01", end_date: str = "2024-12-31") -> str:
    """
    Runs two strategies through the validation harness side-by-side
    and returns a comparison table.

    Args:
        strategy_a: First strategy name.
        strategy_b: Second strategy name.
        start_date: Backtest start date (YYYY-MM-DD).
        end_date: Backtest end date (YYYY-MM-DD).
    """
    results = {}
    for name in [strategy_a, strategy_b]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.refresh_validations",
                 "--strategies", name, "--start", start_date, "--end", end_date,
                 "--json"],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                results[name] = result.stdout
            else:
                results[name] = f"FAILED: {result.stderr[:200]}"
        except Exception as e:
            results[name] = f"ERROR: {str(e)}"

    lines = [f"# Strategy Comparison: {strategy_a} vs {strategy_b}\n"]
    lines.append(f"**Period**: {start_date} → {end_date}\n")
    for name, output in results.items():
        lines.append(f"## {name}\n```\n{output}\n```\n")

    return "\n".join(lines)


@mcp.tool()
def get_model_registry_status() -> str:
    """
    Reads ml/registry.yaml and returns model health: last training date,
    feature importance, OOS metrics, and staleness warnings.
    """
    import yaml
    from datetime import datetime, timedelta

    registry_path = "ml/registry.yaml"
    if not os.path.exists(registry_path):
        return "Error: ml/registry.yaml not found."

    try:
        with open(registry_path, "r") as f:
            registry = yaml.safe_load(f)

        if not registry:
            return "Registry is empty."

        lines = ["# ML Model Registry Status\n"]
        now = datetime.now()
        stale_threshold = timedelta(days=30)

        models = registry if isinstance(registry, list) else registry.get("models", [registry])
        if isinstance(models, dict):
            models = [models]

        for model in models:
            name = model.get("name", model.get("model_name", "unknown"))
            trained = model.get("last_trained", model.get("trained_at", "N/A"))
            lines.append(f"## {name}")
            lines.append(f"- **Last Trained**: {trained}")

            # Check staleness
            if trained != "N/A":
                try:
                    trained_dt = datetime.fromisoformat(str(trained).replace("Z", "+00:00").split("+")[0])
                    age = now - trained_dt
                    if age > stale_threshold:
                        lines.append(f"- ⚠️ **STALE**: Model is {age.days} days old (threshold: 30 days)")
                    else:
                        lines.append(f"- ✅ Fresh ({age.days} days old)")
                except Exception:
                    pass

            # Feature importance
            features = model.get("feature_importance", model.get("top_features", {}))
            if features:
                lines.append("- **Top Features**:")
                items = list(features.items())[:10] if isinstance(features, dict) else features[:10]
                for item in items:
                    if isinstance(item, tuple):
                        lines.append(f"  - `{item[0]}`: {item[1]}")
                    else:
                        lines.append(f"  - {item}")

            # Metrics
            metrics = model.get("metrics", model.get("oos_metrics", {}))
            if metrics:
                lines.append("- **OOS Metrics**:")
                for k, v in metrics.items():
                    lines.append(f"  - `{k}`: {v}")

            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"Registry status failed: {str(e)}"


@mcp.tool()
def trigger_model_retraining(model_name: str = "all") -> str:
    """
    Triggers ML model retraining via scripts/retrain_models.py.

    Args:
        model_name: Specific model to retrain, or "all" for full retrain.
    """
    try:
        cmd = [sys.executable, "scripts/retrain_models.py"]
        if model_name.strip().lower() != "all":
            cmd.extend(["--model", model_name])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode == 0:
            return f"# Model Retraining Complete\n\n{result.stdout}"
        return f"Retraining failed (exit {result.returncode}):\n{result.stderr}"
    except subprocess.TimeoutExpired:
        return "Model retraining timed out after 15 minutes."
    except Exception as e:
        return f"Retraining error: {str(e)}"


# ==========================================
# [6] PHASE 3 — EXECUTION & ALERTING
# ==========================================

@mcp.tool()
def generate_daily_signals(top_n: int = 10) -> str:
    """
    Runs the full signal aggregation pipeline and returns the top N tickers
    ranked by composite conviction score.

    Args:
        top_n: Number of top signals to return (default: 10).
    """
    try:
        # Get the latest date's signals
        _, date_rows = _db_query("SELECT MAX(date) FROM DailySignals")
        latest_date = date_rows[0][0] if date_rows else None
        if not latest_date:
            return "No signals in the database. Run the full pipeline first."

        _, rows = _db_query(
            """SELECT symbol, composite_score, action, conviction
               FROM DailySignals
               WHERE date = ?
               ORDER BY composite_score DESC
               LIMIT ?""",
            (latest_date, top_n)
        )

        if not rows:
            return f"No signals found for date {latest_date}."

        lines = [f"# Daily Signals — {latest_date}\n"]
        lines.append("| Rank | Symbol | Score | Action | Conviction |")
        lines.append("|------|--------|-------|--------|------------|")
        for i, (sym, score, action, conviction) in enumerate(rows, 1):
            score_str = f"{score:.1f}" if score is not None else "N/A"
            conv_str = f"{conviction:.2f}" if conviction is not None else "N/A"
            action_str = action or "HOLD"
            lines.append(f"| {i} | `{sym}` | {score_str} | {action_str} | {conv_str} |")

        return "\n".join(lines)
    except Exception as e:
        return f"Signal generation failed: {str(e)}"


@mcp.tool()
def get_execution_queue() -> str:
    """
    Reads the latest execution_queue.json and returns the gated order intents
    with their risk-gate verdicts.
    """
    queue_path = "output/execution_queue.json"
    if not os.path.exists(queue_path):
        return "No execution queue file found at output/execution_queue.json. The pipeline may not have generated orders yet."

    try:
        with open(queue_path, "r") as f:
            queue = json.load(f)

        if not queue:
            return "Execution queue is empty (no orders pending)."

        lines = ["# Execution Queue\n"]

        orders = queue if isinstance(queue, list) else queue.get("orders", [queue])
        lines.append("| Symbol | Side | Shares | Price | Gated | Reason |")
        lines.append("|--------|------|--------|-------|-------|--------|")

        for order in orders:
            sym = order.get("symbol", "?")
            side = order.get("side", "?")
            shares = order.get("shares", "?")
            price = order.get("price", "?")
            allowed = "✅" if order.get("allow_place", False) else "🚫"
            reason = order.get("gate_reason", order.get("reason", "N/A"))
            lines.append(f"| `{sym}` | {side} | {shares} | {price} | {allowed} | {reason} |")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to read execution queue: {str(e)}"


@mcp.tool()
def get_trade_journal(symbol: str = "", last_n: int = 20) -> str:
    """
    Returns the last N paper trades, optionally filtered by symbol,
    with P&L, entry/exit info, and strategy tags.

    Args:
        symbol: Filter by ticker (leave empty for all).
        last_n: Number of recent trades to return (default: 20).
    """
    try:
        from transactions_store import TransactionsStore
        store = TransactionsStore()

        # Closed trades
        closed_df = store.closed_trades_df()
        open_df = store.open_trades_df()

        if symbol:
            sym = symbol.upper().strip()
            if not closed_df.empty:
                closed_df = closed_df[closed_df["symbol"] == sym]
            if not open_df.empty:
                open_df = open_df[open_df["symbol"] == sym]

        lines = [f"# Trade Journal" + (f" — {symbol.upper()}" if symbol else "") + "\n"]

        # Open positions
        if not open_df.empty:
            lines.append("## Open Positions")
            lines.append(open_df.tail(last_n).to_markdown(index=False) + "\n")
        else:
            lines.append("## Open Positions\nNone.\n")

        # Closed trades
        if not closed_df.empty:
            recent = closed_df.tail(last_n)
            lines.append("## Recent Closed Trades")
            lines.append(recent.to_markdown(index=False) + "\n")

            # Summary stats
            if "entry_price" in recent.columns and "exit_price" in recent.columns:
                total_pl = 0.0
                wins = 0
                for _, row in recent.iterrows():
                    if row["side"] == "long":
                        pl = (row["exit_price"] - row["entry_price"]) * row.get("shares", 1)
                    else:
                        pl = (row["entry_price"] - row["exit_price"]) * row.get("shares", 1)
                    total_pl += pl
                    if pl > 0:
                        wins += 1
                win_rate = (wins / len(recent)) * 100 if len(recent) > 0 else 0
                lines.append(f"**Win Rate**: {win_rate:.1f}% | **Total P&L**: ${total_pl:+,.2f}")
        else:
            lines.append("## Recent Closed Trades\nNone.\n")

        return "\n".join(lines)
    except Exception as e:
        return f"Trade journal failed: {str(e)}"


@mcp.tool()
def configure_alerts(
    channels: Optional[str] = None,
    signal_fired: Optional[bool] = None,
    model_stale: Optional[bool] = None,
    pipeline_failed: Optional[bool] = None,
    pit_audit_failed: Optional[bool] = None,
) -> str:
    """
    Configures which events trigger notifications and which channels to use.

    Args:
        channels: Comma-separated alert channels (e.g., "ntfy,email,slack"). Leave empty to keep current.
        signal_fired: Enable/disable alerts when a signal exceeds conviction threshold.
        model_stale: Enable/disable alerts when a model is > 30 days old.
        pipeline_failed: Enable/disable alerts when the daily pipeline fails.
        pit_audit_failed: Enable/disable alerts when a PIT audit returns FAIL.
    """
    try:
        from alerting_mcp.notifier import get_alert_config, save_alert_config

        config = get_alert_config()

        if channels is not None:
            config["channels"] = [ch.strip().lower() for ch in channels.split(",") if ch.strip()]

        events = config.get("events", {})
        if signal_fired is not None:
            events["signal_fired"] = signal_fired
        if model_stale is not None:
            events["model_stale"] = model_stale
        if pipeline_failed is not None:
            events["pipeline_failed"] = pipeline_failed
        if pit_audit_failed is not None:
            events["pit_audit_failed"] = pit_audit_failed
        config["events"] = events

        save_alert_config(config)

        lines = ["# Alert Configuration Updated\n"]
        lines.append(f"**Active Channels**: {', '.join(config['channels'])}\n")
        lines.append("**Event Subscriptions**:")
        for event, enabled in config["events"].items():
            status = "✅ Enabled" if enabled else "❌ Disabled"
            lines.append(f"- `{event}`: {status}")

        return "\n".join(lines)
    except Exception as e:
        return f"Alert configuration failed: {str(e)}"


@mcp.tool()
def send_test_alert(title: str = "Test Alert", message: str = "This is a test notification from InvestYo.") -> str:
    """
    Sends a test notification to all active alert channels to verify configuration.

    Args:
        title: Alert title.
        message: Alert message body.
    """
    try:
        from alerting_mcp.notifier import send

        results = send(title, message, priority="default")
        lines = ["# Test Alert Results\n"]
        for channel, success in results.items():
            status = "✅ Delivered" if success else "❌ Failed"
            lines.append(f"- **{channel}**: {status}")

        return "\n".join(lines)
    except Exception as e:
        return f"Test alert failed: {str(e)}"


# ==========================================
# [8] ADVISORY & MARKET INTELLIGENCE (READ-ONLY)
# ==========================================
# All tools in this section are strictly READ-ONLY analytics wrappers over the
# platform's advisory / options / regime / coverage engines. They NEVER place,
# submit, or simulate any broker order (advisory-only platform). Each is
# dead-letter safe (try/except -> error string, never raises) and returns human
# markdown plus a compact machine-readable JSON block (real values only; NaN/None
# serialized as null, never fabricated).


@mcp.tool()
def get_recommendation(symbol: str) -> str:
    """
    Runs the platform's PRIMARY output — the holding-aware advisory engine — for
    one symbol and returns its BUY/SELL/HOLD recommendation, conviction, strategy,
    suggested position %, 30-day forecast, data quality, key indicators, and the
    full plain-English rationale. READ-ONLY: no Robinhood login, no order code.
    """
    import json
    import math

    try:
        from engine.advisory import evaluate
        from data.market_data import get_provider

        sym = symbol.upper().strip()
        # position=None, snapshot=None -> clean read-only non-held recommendation.
        rec = evaluate(sym, position=None, market=get_provider(), snapshot=None)

        def _num(v):
            try:
                if v is None:
                    return None
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
            except (TypeError, ValueError):
                return None

        forecast = _num(getattr(rec, "forecast", None))
        conviction = _num(getattr(rec, "conviction", None))
        pct = _num(getattr(rec, "suggested_position_pct", None))

        lines = [f"# Advisory Recommendation — {rec.symbol}\n"]
        lines.append(f"- **Action**: {rec.action}")
        lines.append(f"- **Strategy**: {rec.strategy}")
        lines.append(
            f"- **Conviction**: {conviction:.3f}" if conviction is not None else "- **Conviction**: N/A"
        )
        lines.append(
            f"- **Suggested Position %**: {pct * 100:.2f}%" if pct is not None else "- **Suggested Position %**: N/A"
        )
        lines.append(
            f"- **30-Day Forecast**: ${forecast:,.2f}" if forecast is not None else "- **30-Day Forecast**: unavailable"
        )
        lines.append(f"- **Data Quality**: {rec.data_quality}")

        ki = getattr(rec, "key_indicators", {}) or {}
        ki_clean = {}
        if isinstance(ki, dict) and ki:
            lines.append("\n## Key Indicators")
            for k, v in ki.items():
                nv = _num(v)
                ki_clean[k] = nv
                lines.append(f"- **{k}**: {nv:.4f}" if nv is not None else f"- **{k}**: N/A")

        lines.append("\n## Rationale")
        lines.append(getattr(rec, "rationale", "") or "(no rationale provided)")

        payload = {
            "symbol": rec.symbol,
            "action": rec.action,
            "strategy": rec.strategy,
            "conviction": conviction,
            "suggested_position_pct": pct,
            "forecast_30d": forecast,
            "data_quality": rec.data_quality,
            "key_indicators": ki_clean,
        }
        lines.append("\n```json")
        lines.append(json.dumps(payload, indent=2))
        lines.append("```")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to compute recommendation for {symbol}: {str(e)}"


@mcp.tool()
def get_options_directive(symbol: str) -> str:
    """
    Runs the premium-selling directive engine (build_premium_directive) for one
    symbol and returns the hydrated directive — Strategy/Action, Net Premium,
    GARCH sigma, IVR proxy, trend bias, short/long strikes + deltas, ATM Greeks —
    plus the integrity-validator verdict. If a regime gates it to Cash/Wait, that
    is shown honestly. READ-ONLY analytics; NaN values render as N/A. No order code.
    """
    import json
    import math

    try:
        from technical_options_engine import build_premium_directive, validate_directive_integrity
        from data.market_data import get_provider

        sym = symbol.upper().strip()
        provider = get_provider()

        bars = provider.get_intraday_bars(sym)
        if bars is None or bars.empty:
            return f"No bar data available for {sym}; cannot build options directive."

        # Spot price + staleness from the latest quote, falling back to the last
        # bar Close when the quote is unavailable (bars still let us build sigma).
        spot_price = None
        is_stale = True
        try:
            q = provider.get_latest_quote(sym)
            if q is not None and q.price is not None and float(q.price) > 0:
                spot_price = float(q.price)
                is_stale = bool(getattr(q, "is_stale", True))
        except Exception:
            spot_price = None
        if spot_price is None:
            spot_price = float(bars["Close"].iloc[-1])
            is_stale = True

        directive = build_premium_directive(
            sym, bars, spot_price=spot_price, is_stale=is_stale
        )
        if not isinstance(directive, dict) or not directive:
            return f"Options directive engine returned no result for {sym}."

        def _num(v):
            try:
                if v is None:
                    return None
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
            except (TypeError, ValueError):
                return None

        def _fmt(key, money=False, pct=False):
            nv = _num(directive.get(key))
            if nv is None:
                # Non-numeric fields (Strategy, Action, Trend_Bias) pass through raw.
                raw = directive.get(key)
                return str(raw) if raw not in (None, "") else "N/A"
            if money:
                return f"${nv:,.2f}"
            if pct:
                return f"{nv:.4f}"
            return f"{nv:.4f}"

        lines = [f"# Options Premium Directive — {sym}\n"]
        lines.append(f"- **Strategy**: {directive.get('Strategy', 'N/A')}")
        lines.append(f"- **Action**: {directive.get('Action', 'N/A')}")
        lines.append(f"- **Trend Bias**: {directive.get('Trend_Bias', 'N/A')}")
        lines.append(f"- **Price**: {_fmt('Price', money=True)}")
        lines.append(f"- **Stale Quote**: {directive.get('Stale', is_stale)}")
        lines.append(f"- **Net Premium**: {_fmt('Net_Premium', money=True)}")
        lines.append(f"- **Realizable Daily Theta**: {_fmt('Realizable_Daily_Theta', money=True)}")
        lines.append(f"- **Sigma (GJR-GARCH, annualized)**: {_fmt('Sigma_GARCH')}")
        lines.append(f"- **IVR Proxy**: {_fmt('IVR_Proxy')}")
        lines.append(f"- **Aroon Oscillator**: {_fmt('Aroon_Oscillator')}")
        lines.append(f"- **Coppock Curve**: {_fmt('Coppock_Curve')}")

        lines.append("\n## Legs")
        lines.append(f"- **Short Strike / Delta**: {_fmt('Short_Strike', money=True)} / {_fmt('Short_Delta')}")
        lines.append(f"- **Long Strike / Delta**: {_fmt('Long_Strike', money=True)} / {_fmt('Long_Delta')}")

        lines.append("\n## ATM Greeks")
        lines.append(f"- **Delta**: {_fmt('ATM_Delta')}")
        lines.append(f"- **Gamma**: {_fmt('ATM_Gamma')}")
        lines.append(f"- **Vega**: {_fmt('ATM_Vega')}")
        lines.append(f"- **Theta (daily)**: {_fmt('ATM_Theta_Daily')}")

        # Integrity validation
        integrity = {}
        try:
            integrity = validate_directive_integrity(directive) or {}
        except Exception as ie:
            integrity = {"ok": None, "issues": [f"validator error: {ie}"]}
        ok = integrity.get("ok")
        issues = integrity.get("issues", []) or []
        lines.append("\n## Integrity")
        lines.append(f"- **OK**: {ok}")
        if issues:
            for iss in issues:
                lines.append(f"  - {iss}")
        else:
            lines.append("  - (no issues)")

        payload = {
            "symbol": sym,
            "strategy": directive.get("Strategy"),
            "action": directive.get("Action"),
            "trend_bias": directive.get("Trend_Bias"),
            "price": _num(directive.get("Price")),
            "net_premium": _num(directive.get("Net_Premium")),
            "realizable_daily_theta": _num(directive.get("Realizable_Daily_Theta")),
            "sigma_garch": _num(directive.get("Sigma_GARCH")),
            "ivr_proxy": _num(directive.get("IVR_Proxy")),
            "short_strike": _num(directive.get("Short_Strike")),
            "short_delta": _num(directive.get("Short_Delta")),
            "long_strike": _num(directive.get("Long_Strike")),
            "long_delta": _num(directive.get("Long_Delta")),
            "atm_delta": _num(directive.get("ATM_Delta")),
            "atm_gamma": _num(directive.get("ATM_Gamma")),
            "atm_vega": _num(directive.get("ATM_Vega")),
            "atm_theta_daily": _num(directive.get("ATM_Theta_Daily")),
            "integrity_ok": ok,
            "integrity_issues": list(issues),
        }
        lines.append("\n```json")
        lines.append(json.dumps(payload, indent=2))
        lines.append("```")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to build options directive for {symbol}: {str(e)}"


@mcp.tool()
def get_regime_status() -> str:
    """
    Reports the current macro regime, VIX, recession telemetry (Sahm Rule, HY OAS,
    yield curve), HMM risk-on probability, macro-regime-gate state, and the global
    kill-switch state — WITHOUT a live FRED call, by reading the persisted
    output/state_snapshot.json. Missing values render as "unavailable" and are
    never fabricated. READ-ONLY.
    """
    import json
    import math
    import os

    try:
        # Resolve the snapshot path via settings.OUTPUT_DIR when possible.
        snap_path = None
        try:
            from settings import settings as _settings
            snap_path = os.path.join(str(_settings.OUTPUT_DIR), "state_snapshot.json")
        except Exception:
            snap_path = os.path.join("output", "state_snapshot.json")

        snap = None
        if snap_path and os.path.exists(snap_path):
            try:
                with open(snap_path, "r", encoding="utf-8") as fh:
                    snap = json.load(fh)
            except Exception:
                snap = None

        # Kill switch — checked live (cheap file-existence probe, no engine work).
        kill_active = None
        try:
            from execution.kill_switch import GlobalKillSwitch
            kill_active = bool(GlobalKillSwitch().is_active())
        except Exception:
            kill_active = None

        def _num(v):
            try:
                if v is None:
                    return None
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
            except (TypeError, ValueError):
                return None

        def _badge_vix(v):
            if v is None:
                return "unavailable"
            if v > 30:
                return f"🔴 {v:.2f} (elevated)"
            if v > 20:
                return f"🟡 {v:.2f}"
            return f"🟢 {v:.2f}"

        def _badge_sahm(v):
            if v is None:
                return "unavailable"
            if v >= 0.5:
                return f"🔴 {v:.2f} (recession trigger)"
            if v >= 0.3:
                return f"🟡 {v:.2f}"
            return f"🟢 {v:.2f}"

        def _badge_oas(v):
            if v is None:
                return "unavailable"
            if v > 6:
                return f"🔴 {v:.2f}% (credit stress)"
            if v > 4:
                return f"🟡 {v:.2f}%"
            return f"🟢 {v:.2f}%"

        def _badge_hmm(v):
            if v is None:
                return "unavailable (HMM did not run)"
            if v < 0.3:
                return f"🔴 {v * 100:.1f}% risk-on"
            if v < 0.6:
                return f"🟡 {v * 100:.1f}% risk-on"
            return f"🟢 {v * 100:.1f}% risk-on"

        lines = ["# Macro Regime & Risk Status\n"]

        if snap is None:
            lines.append(
                "_State snapshot unavailable — run the pipeline (`main.py` / "
                "`main_orchestrator.py`) to generate `output/state_snapshot.json`._\n"
            )
            regime = None
            vix = sahm = oas = ycurve = hmm = None
            gate = None
        else:
            regime = snap.get("market_regime") or snap.get("regime")
            vix = _num(snap.get("vix"))
            sahm = _num(snap.get("sahm_rule"))
            oas = _num(snap.get("high_yield_oas"))
            ycurve = _num(snap.get("yield_curve"))
            hmm = _num(snap.get("hmm_risk_on_probability"))
            gate = snap.get("macro_regime_gate_enabled")
            ts = snap.get("timestamp", "unknown")
            lines.append(f"_Snapshot timestamp: {ts}_\n")
            lines.append(f"- **Market Regime**: {regime or 'unavailable'}")
            lines.append(f"- **VIX**: {_badge_vix(vix)}")
            lines.append(f"- **Sahm Rule**: {_badge_sahm(sahm)}")
            lines.append(f"- **High-Yield OAS**: {_badge_oas(oas)}")
            lines.append(
                f"- **Yield Curve (10Y-2Y)**: {ycurve:.2f}" if ycurve is not None else "- **Yield Curve (10Y-2Y)**: unavailable"
            )
            lines.append(f"- **HMM Risk-On Probability**: {_badge_hmm(hmm)}")
            lines.append(
                f"- **Macro Regime Gate**: {'🟢 ENABLED' if gate else '🔴 DISABLED' if gate is not None else 'unavailable'}"
            )

        lines.append(
            f"- **Global Kill Switch**: "
            + ("🔴 ACTIVE" if kill_active else "🟢 inactive" if kill_active is not None else "unavailable")
        )

        payload = {
            "snapshot_available": snap is not None,
            "market_regime": (snap.get("market_regime") or snap.get("regime")) if snap else None,
            "vix": _num(snap.get("vix")) if snap else None,
            "sahm_rule": _num(snap.get("sahm_rule")) if snap else None,
            "high_yield_oas": _num(snap.get("high_yield_oas")) if snap else None,
            "yield_curve": _num(snap.get("yield_curve")) if snap else None,
            "hmm_risk_on_probability": _num(snap.get("hmm_risk_on_probability")) if snap else None,
            "macro_regime_gate_enabled": snap.get("macro_regime_gate_enabled") if snap else None,
            "kill_switch_active": kill_active,
        }
        lines.append("\n```json")
        lines.append(json.dumps(payload, indent=2))
        lines.append("```")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to read regime status: {str(e)}"


@mcp.tool()
def get_portfolio_coverage() -> str:
    """
    Reports the portfolio/watchlist coverage report (holdings ∪ watchlists) with
    each symbol's CoverageStatus (FULL/STALE/QUOTES_ONLY/EQUITY_ONLY/UNCOVERED),
    cost-basis delta, and forecast availability. Tries a cached Robinhood account
    snapshot first (no forced login); degrades to snapshot=None when unavailable.
    READ-ONLY analytics; no order code; dead-letter safe.
    """
    import json
    import math

    try:
        from data.portfolio_sync import build_sync_report, CoverageStatus  # noqa: F401

        # Try a cached account snapshot WITHOUT forcing a live Robinhood login.
        snapshot = None
        snapshot_note = "no account snapshot (holdings excluded)"
        try:
            from data.robinhood_portfolio import fetch_account_snapshot
            snapshot = fetch_account_snapshot()
            snapshot_note = "account snapshot loaded"
        except Exception as se:
            snapshot = None
            snapshot_note = f"account snapshot unavailable ({type(se).__name__})"

        report = build_sync_report(snapshot, probe_market=True)

        def _num(v):
            try:
                if v is None:
                    return None
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else f
            except (TypeError, ValueError):
                return None

        symbols = getattr(report, "symbols", {}) or {}
        lines = ["# Portfolio & Watchlist Coverage\n"]
        lines.append(f"_{snapshot_note}._\n")
        lines.append(f"- **Provider Source**: {getattr(report, 'provider_source', 'N/A')}")
        lines.append(f"- **Fundamentals Source**: {getattr(report, 'fundamentals_source', 'N/A')}")
        lines.append(f"- **Total Symbols**: {getattr(report, 'n_total', len(symbols))}")
        lines.append(f"- **Full**: {getattr(report, 'n_full', 0)}  |  "
                     f"**Equity-Only**: {getattr(report, 'n_equity_only', 0)}  |  "
                     f"**Uncovered**: {getattr(report, 'n_uncovered', 0)}\n")

        rows = []
        json_symbols = []
        for sym in sorted(symbols.keys()):
            st = symbols[sym]
            coverage = getattr(getattr(st, "coverage", None), "value", None) or str(getattr(st, "coverage", ""))
            delta = _num(getattr(st, "cost_basis_delta_per_share", None))
            price = _num(getattr(st, "current_price", None))
            held = bool(getattr(st, "held", False))
            fc = bool(getattr(st, "forecast_available", False))
            rows.append(
                "| {sym} | {cov} | {held} | {price} | {delta} | {fc} |".format(
                    sym=sym,
                    cov=coverage,
                    held="✅" if held else "",
                    price=f"${price:,.2f}" if price is not None else "N/A",
                    delta=f"{delta:+,.2f}" if delta is not None else "N/A",
                    fc="✅" if fc else "",
                )
            )
            json_symbols.append({
                "symbol": sym,
                "coverage": coverage,
                "held": held,
                "current_price": price,
                "cost_basis_delta_per_share": delta,
                "forecast_available": fc,
                "diagnostic": getattr(st, "diagnostic", "") or "",
            })

        if rows:
            lines.append("| Symbol | Coverage | Held | Price | Δ/Share | Forecast |")
            lines.append("|--------|----------|------|-------|---------|----------|")
            lines.extend(rows)
        else:
            lines.append("_No symbols in the tracked universe (no holdings or watchlists found)._")

        # Coverage-gap callout
        gaps = [s for s in json_symbols if s["coverage"] in ("uncovered", "equity_only")]
        if gaps:
            lines.append("\n## Coverage Gaps")
            for g in gaps:
                note = f" — {g['diagnostic']}" if g["diagnostic"] else ""
                lines.append(f"- **{g['symbol']}** ({g['coverage']}){note}")

        payload = {
            "snapshot_loaded": snapshot is not None,
            "provider_source": getattr(report, "provider_source", None),
            "fundamentals_source": getattr(report, "fundamentals_source", None),
            "n_total": getattr(report, "n_total", len(symbols)),
            "n_full": getattr(report, "n_full", 0),
            "n_equity_only": getattr(report, "n_equity_only", 0),
            "n_uncovered": getattr(report, "n_uncovered", 0),
            "symbols": json_symbols,
        }
        lines.append("\n```json")
        lines.append(json.dumps(payload, indent=2))
        lines.append("```")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to build portfolio coverage report: {str(e)}"


# ==========================================
# [7] SERVER EXECUTION
# ==========================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="InvestYo MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' for local IDE, 'sse' for cloud deployment (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for SSE transport (default: 8080)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        print(f"Starting InvestYo MCP Server in SSE mode on port {args.port}...")
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")

