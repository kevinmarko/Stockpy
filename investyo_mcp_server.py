import os
import sys
import subprocess
import sqlite3
import json
from typing import List, Dict, Any, Optional
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
    env_path = ".env"
    symbol_upper = symbol.upper().strip()
    action_lower = action.lower().strip()
    
    env_vars = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip()
        except Exception as e:
            return f"Failed to read .env file: {str(e)}"
            
    current_tickers = ["AAPL", "MSFT", "JNJ", "AGNC"]
    if "DEFAULT_TICKERS" in env_vars:
        try:
            val = env_vars["DEFAULT_TICKERS"]
            current_tickers = json.loads(val)
            if not isinstance(current_tickers, list):
                current_tickers = [current_tickers]
        except Exception:
            current_tickers = [t.strip() for t in env_vars["DEFAULT_TICKERS"].split(",") if t.strip()]
            
    current_tickers = [t.upper() for t in current_tickers]
    
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
        
    env_vars["DEFAULT_TICKERS"] = json.dumps(current_tickers)
    
    try:
        with open(env_path, "w") as f:
            for k, v in env_vars.items():
                f.write(f"{k}={v}\n")
        return f"Successfully {action_lower}ed {symbol_upper} from the active universe. Current tickers: {current_tickers}"
    except Exception as e:
        return f"Failed to write to .env file: {str(e)}"

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
        
        artifact_dir = "/Users/kevinlee/.gemini/antigravity/brain/d401d6a1-6d28-4b48-a196-95e42415c9ed"
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
    import json
    import yfinance as yf
    import backtrader as bt
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from simulation_engine import InstitutionalStrategy
    
    env_path = ".env"
    current_tickers = ["AAPL", "MSFT", "JNJ", "AGNC"]
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "DEFAULT_TICKERS":
                            current_tickers = json.loads(v.strip())
        except Exception:
            pass
            
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
        
        artifact_dir = "/Users/kevinlee/.gemini/antigravity/brain/d401d6a1-6d28-4b48-a196-95e42415c9ed"
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
    import json
    import sqlite3
    import yaml
    
    status = ["# InvestYo Universe Status Dashboard\n"]
    
    env_path = ".env"
    current_tickers = ["AAPL", "MSFT", "JNJ", "AGNC"]
    if os.path.exists(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "DEFAULT_TICKERS":
                            current_tickers = json.loads(v.strip())
        except Exception:
            pass
            
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
            
    db_path = "quant_platform.db"
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM DailySignals")
            signals_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM Transactions")
            transactions_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM ExecutionLogs")
            logs_count = cursor.fetchone()[0]
            
            conn.close()
            
            status.append("## Database Metrics")
            status.append(f"- **Daily Signals Table Rows**: {signals_count}")
            status.append(f"- **Transactions Table Rows**: {transactions_count}")
            status.append(f"- **Execution Logs Table Rows**: {logs_count}")
        except Exception as e:
            status.append(f"## Database Metrics\nError querying DB stats: {str(e)}")
            
    return "\n".join(status)

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
