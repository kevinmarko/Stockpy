"""
InvestYo Quant Platform - Paper Account Store
=============================================
SQLite store tracking virtual cash balance, open positions, and order history
across process restarts for the FMP-based paper trading engine.
"""

import logging
import re
from datetime import datetime, timezone, date
from typing import Optional, List, Dict, Any


from sqlalchemy import Column, Integer, String, Float, DateTime, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from db_config import resolve_database_url, create_db_engine, session_scope
from settings import settings
from data import fmp_client
from execution.broker_base import AccountSnapshot, PositionSnapshot, OrderResult, OrderStatus

logger = logging.getLogger(__name__)

# Position quantities near zero after a full sell can carry float noise
# (e.g. 1e-13 rather than exactly 0.0) -- see CLAUDE.md's "Degenerate-std
# guard convention": never compare a computed float to 0 with ==.
_QTY_EPSILON = 1e-9

Base = declarative_base()

class PaperAccount(Base):
    __tablename__ = 'paper_account'
    
    id = Column(Integer, primary_key=True)  # always 1
    cash_balance = Column(Float, nullable=False)


class PaperPosition(Base):
    __tablename__ = 'paper_positions'
    
    symbol = Column(String(64), primary_key=True)
    strategy_id = Column(String(100), primary_key=True, default="untagged")
    pilot_id = Column(String(100), nullable=True)
    experiment_arm = Column(String(100), nullable=True)
    qty = Column(Float, nullable=False)
    avg_entry_price = Column(Float, nullable=False)
    # Real open time of this position, set when it is first opened from flat
    # (or re-opened after a full flip through zero) and left untouched while
    # averaging in. NULL only for a legacy/migrated position whose true entry
    # time is genuinely unknown -- never fabricated (CONSTRAINT #4).
    entry_ts = Column(DateTime, nullable=True)



class PaperOrder(Base):
    __tablename__ = 'paper_orders'
    
    client_order_id = Column(String(100), primary_key=True)
    broker_order_id = Column(String(100), nullable=True)
    symbol = Column(String(64), nullable=False)
    side = Column(String(10), nullable=False)
    qty = Column(Float, nullable=False)
    target_qty = Column(Float, nullable=True)
    filled_qty = Column(Float, nullable=False, default=0.0)
    filled_avg_price = Column(Float, nullable=True)
    status = Column(String(20), nullable=False)
    strategy_id = Column(String(100), nullable=True)
    pilot_id = Column(String(100), nullable=True)
    experiment_arm = Column(String(100), nullable=True)
    leg_group_id = Column(String(100), nullable=True)
    order_kind = Column(String(20), nullable=True)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class PaperClosedTrade(Base):
    __tablename__ = 'paper_closed_trades'
    
    trade_id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(100), nullable=True)
    pilot_id = Column(String(100), nullable=True)
    experiment_arm = Column(String(100), nullable=True)
    symbol = Column(String(64), nullable=False)
    side = Column(String(10), nullable=False)
    qty = Column(Float, nullable=False)
    entry_ts = Column(DateTime, nullable=True)
    entry_price = Column(Float, nullable=False)
    exit_ts = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    exit_price = Column(Float, nullable=False)
    commission = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False)
    # Nullable: undefined (None/NaN), never fabricated, when avg_entry_price
    # is degenerate (<=0 -- see scripts/purge_corrupt_paper_options.py for
    # why real production rows can have this) -- CONSTRAINT #4.
    realized_pnl_pct = Column(Float, nullable=True)
    holding_period_days = Column(Float, nullable=True)
    close_reason = Column(String(20), nullable=False)
    leg_group_id = Column(String(100), nullable=True)


_OPTION_SYMBOL_REGEX = re.compile(
    r"^([A-Z0-9]+)\s+(\d{4}-\d{2}-\d{2})\s+\$?(\d+(?:\.\d+)?)\s+(CALL|PUT)$",
    re.IGNORECASE,
)


class PaperAccountStore:

    def __init__(self, db_url: Optional[str] = None, *, readonly: bool = False):
        db_url = db_url or resolve_database_url()
        self._readonly = readonly
        if readonly:
            from db_config import create_readonly_db_engine
            self.engine = create_readonly_db_engine(db_url)
        else:
            self.engine = create_db_engine(db_url)
            Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        if not readonly:
            self._ensure_account_exists()

    def _ensure_account_exists(self):
        with self.engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE paper_orders ADD COLUMN target_qty REAL"))
            except Exception:
                pass
            
            try:
                conn.execute(text("ALTER TABLE paper_orders ADD COLUMN strategy_id VARCHAR(100)"))
                conn.execute(text("ALTER TABLE paper_orders ADD COLUMN pilot_id VARCHAR(100)"))
                conn.execute(text("ALTER TABLE paper_orders ADD COLUMN experiment_arm VARCHAR(100)"))
                conn.execute(text("ALTER TABLE paper_orders ADD COLUMN leg_group_id VARCHAR(100)"))
                conn.execute(text("ALTER TABLE paper_orders ADD COLUMN order_kind VARCHAR(20)"))
            except Exception:
                pass

            try:
                res = conn.execute(text("PRAGMA table_info(paper_positions)")).fetchall()
                cols = [r[1] for r in res]
                if "strategy_id" not in cols:
                    conn.execute(text("ALTER TABLE paper_positions RENAME TO old_paper_positions"))
                    conn.execute(text(
                        "CREATE TABLE paper_positions ("
                        "symbol VARCHAR(64) NOT NULL, "
                        "strategy_id VARCHAR(100) DEFAULT 'untagged' NOT NULL, "
                        "pilot_id VARCHAR(100), "
                        "experiment_arm VARCHAR(100), "
                        "qty REAL NOT NULL, "
                        "avg_entry_price REAL NOT NULL, "
                        "entry_ts DATETIME, "
                        "PRIMARY KEY (symbol, strategy_id)"
                        ")"
                    ))
                    conn.execute(text(
                        "INSERT INTO paper_positions (symbol, strategy_id, qty, avg_entry_price) "
                        "SELECT symbol, 'untagged', qty, avg_entry_price FROM old_paper_positions"
                    ))
                    conn.execute(text("DROP TABLE old_paper_positions"))
                    cols.append("entry_ts")
                if "entry_ts" not in cols:
                    # Additive migration for a DB created after the strategy_id
                    # rebuild above but before entry_ts existed. Legacy rows
                    # get NULL (genuinely unknown entry time), never a
                    # fabricated timestamp -- CONSTRAINT #4.
                    conn.execute(text("ALTER TABLE paper_positions ADD COLUMN entry_ts DATETIME"))
            except Exception as exc:
                logger.error(f"Failed to migrate paper_positions: {exc}")

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).first()
            if not acc:
                acc = PaperAccount(id=1, cash_balance=settings.FMP_PAPER_STARTING_CASH)
                session.add(acc)

    def _resolve_position_prices(self, positions: List[PaperPosition]) -> Dict[str, float]:
        """
        Resolves current mark prices for both stock and option positions.
        Stocks are quoted via fmp_client.batch_quote.
        Options are marked dynamically using Black-Scholes if spot price is known,
        falling back to avg_entry_price if unresolvable.
        """
        if not positions:
            return {}

        stock_symbols = set()
        option_positions = []

        for p in positions:
            sym = p.symbol.upper().strip()
            if " " in sym and "$" in sym:
                # Option format: e.g. "AAPL 2026-09-18 $150.00 CALL"
                option_positions.append(p)
                underlying = sym.split()[0]
                stock_symbols.add(underlying)
            else:
                stock_symbols.add(sym)

        prices: Dict[str, float] = {}
        if stock_symbols:
            try:
                quotes_resp = fmp_client.batch_quote(list(stock_symbols))
                prices = {q.get("symbol", "").upper(): float(q.get("price", 0.0)) for q in quotes_resp if isinstance(q, dict)}
            except Exception as e:
                logger.error(f"Failed to fetch quotes for paper positions: {e}")
                prices = {}

        # Price option positions
        for p in option_positions:
            sym = p.symbol.upper().strip()
            parts = sym.split()
            # Expecting: [UNDERLYING, EXPIRATION, $STRIKE, TYPE]
            try:
                underlying = parts[0]
                exp_str = parts[1]
                strike_str = parts[2].replace("$", "")
                opt_type = parts[3].lower()

                strike = float(strike_str)
                spot = prices.get(underlying, 0.0)

                if spot > 0:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    today = datetime.now(timezone.utc).date()
                    dte = max(1, (exp_date - today).days)
                    t_years = dte / 365.0

                    # Standard Black-Scholes pricing
                    import math
                    from scipy.stats import norm

                    r = 0.04
                    sigma = 0.30  # baseline implied volatility estimate
                    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * math.sqrt(t_years))
                    d2 = d1 - sigma * math.sqrt(t_years)

                    if opt_type == "call":
                        bs_price = spot * norm.cdf(d1) - strike * math.exp(-r * t_years) * norm.cdf(d2)
                    else:
                        bs_price = strike * math.exp(-r * t_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)

                    # Option contract unit price is $/share * 100
                    unit_mark = max(0.01, round(bs_price, 4)) * 100.0
                    prices[sym] = unit_mark
                else:
                    prices[sym] = p.avg_entry_price
            except Exception:
                prices[sym] = p.avg_entry_price

        return prices

    def get_account(self) -> AccountSnapshot:
        """Returns account equity, cash, buying_power (same as cash here)."""
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_account"):
                    return AccountSnapshot(equity=0.0, cash=0.0, buying_power=0.0)
            except Exception:
                return AccountSnapshot(equity=0.0, cash=0.0, buying_power=0.0)

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).first()
            cash = float(acc.cash_balance) if acc else 0.0

            positions = session.query(PaperPosition).filter(PaperPosition.qty != 0).all()
            
            equity = cash
            if positions:
                prices = self._resolve_position_prices(positions)
                for p in positions:
                    price = prices.get(p.symbol.upper(), p.avg_entry_price)
                    equity += (float(p.qty) * float(price))

        return AccountSnapshot(equity=equity, cash=cash, buying_power=cash)

    def get_open_positions(self) -> List[PositionSnapshot]:
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_positions"):
                    return []
            except Exception:
                return []

        results = []
        with session_scope(self.Session) as session:
            positions = session.query(PaperPosition).filter(PaperPosition.qty != 0).all()
            if not positions:
                return []

            prices = self._resolve_position_prices(positions)

            for p in positions:
                current_price = prices.get(p.symbol.upper(), p.avg_entry_price)
                market_value = float(p.qty) * float(current_price)
                if p.qty >= 0:
                    unrealized_pl = market_value - (float(p.qty) * float(p.avg_entry_price))
                else:
                    # Short position: gain when current price is lower than entry price
                    unrealized_pl = (float(p.avg_entry_price) - float(current_price)) * abs(float(p.qty))
                
                results.append(PositionSnapshot(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    market_value=market_value,
                    unrealized_pl=unrealized_pl,
                    strategy_id=p.strategy_id,
                    pilot_id=p.pilot_id,
                    experiment_arm=p.experiment_arm
                ))
        return results

    def reset_account(self, starting_cash: Optional[float] = None) -> None:
        """
        Deletes all PaperPosition and PaperOrder rows. Resets cash balance to
        `starting_cash` if provided, otherwise to FMP_PAPER_STARTING_CASH.
        """
        if self._readonly:
            raise RuntimeError("Cannot reset account in readonly mode.")

        cash_value = starting_cash if starting_cash is not None else settings.FMP_PAPER_STARTING_CASH

        with session_scope(self.Session) as session:
            session.query(PaperPosition).delete()
            session.query(PaperOrder).delete()
            session.query(PaperClosedTrade).delete()

            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if acc:
                acc.cash_balance = cash_value
            else:
                acc = PaperAccount(id=1, cash_balance=cash_value)
                session.add(acc)

    def apply_fill(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        qty: float,
        fill_price: float,
        commission_and_fees: float = 0.0,
        target_qty: Optional[float] = None,
        status: str = OrderStatus.FILLED,
        allow_short: bool = False,
        collateral_required: Optional[float] = None,
        strategy_id: str = "untagged",
        pilot_id: Optional[str] = None,
        experiment_arm: Optional[str] = None,
        leg_group_id: Optional[str] = None,
        order_kind: Optional[str] = None,
    ) -> bool:

        """
        Updates cash and position. Returns True if successful, False if insufficient funds/inventory.
        Records the order.

        ``collateral_required``, when provided, is checked against available cash
        before a short position is opened or increased (mirroring the check
        already performed by ``apply_multi_leg_fill``/``apply_roll_fill``) -- a
        single-leg naked short otherwise has no margin requirement at all.
        """
        if self._readonly:
            raise RuntimeError("Cannot apply fill in readonly mode.")
            
        side = side.lower().strip()
        cost_basis_impact = qty * fill_price
        is_option_contract = (" " in symbol and "$" in symbol) or allow_short
        
        try:
            fill_price_val = float(fill_price)
            if fill_price_val <= 0.0:
                raise ValueError("Price must be positive")
        except (ValueError, TypeError):
            logger.warning(f"Rejecting single-leg order {client_order_id}: invalid fill_price {fill_price}")
            with session_scope(self.Session) as session:
                self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
            return False

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if not acc:
                return False

            now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

            pos = session.query(PaperPosition).filter_by(symbol=symbol.upper(), strategy_id=strategy_id).with_for_update().first()
            if not pos:
                untagged_pos = session.query(PaperPosition).filter_by(symbol=symbol.upper(), strategy_id="untagged").with_for_update().first()
                if untagged_pos:
                    if side == "buy" and untagged_pos.qty < -_QTY_EPSILON:
                        pos = untagged_pos
                    elif side == "sell" and untagged_pos.qty > _QTY_EPSILON:
                        pos = untagged_pos
            current_qty = pos.qty if pos else 0.0

            if side == "buy":
                if pos and pos.qty < -_QTY_EPSILON:
                    # Buying to close an existing short position
                    cost_to_close = cost_basis_impact + commission_and_fees
                    if acc.cash_balance < cost_to_close:
                        logger.warning(f"Insufficient funds to close short paper position of {qty} {symbol}")
                        self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
                        return False
                    acc.cash_balance -= cost_to_close

                    closed_qty = min(abs(pos.qty), qty)
                    prorated_comm = commission_and_fees * (closed_qty / qty) if qty > 0 else 0.0
                    self._record_closed_trade(session, pos, closed_qty, fill_price, "flatten", prorated_comm)

                    new_qty = pos.qty + qty
                    if abs(new_qty) < _QTY_EPSILON:
                        session.delete(pos)
                    elif new_qty > 0:
                        # Short fully closed and flipped through zero into a
                        # brand-new long -- reset entry_ts along with
                        # avg_entry_price rather than leaving it pinned to the
                        # now-fully-closed short's open time.
                        pos.qty = new_qty
                        pos.avg_entry_price = fill_price
                        pos.entry_ts = now_ts
                    else:
                        pos.qty = new_qty
                else:
                    # Buying to open long
                    total_cost = cost_basis_impact + commission_and_fees
                    if acc.cash_balance < total_cost:
                        logger.warning(f"Insufficient funds for paper buy of {qty} {symbol}: cash={acc.cash_balance}, cost={total_cost}")
                        self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
                        return False

                    acc.cash_balance -= total_cost

                    if pos:
                        # Averaging into an existing position -- avg_entry_price
                        # is a weighted average, and entry_ts is likewise left
                        # untouched rather than reset to now.
                        new_qty = pos.qty + qty
                        pos.avg_entry_price = ((pos.qty * pos.avg_entry_price) + cost_basis_impact) / new_qty
                        pos.qty = new_qty
                    else:
                        pos = PaperPosition(symbol=symbol.upper(), strategy_id=strategy_id, pilot_id=pilot_id, experiment_arm=experiment_arm, qty=qty, avg_entry_price=fill_price, entry_ts=now_ts)
                        session.add(pos)

            elif side == "sell":
                if pos and pos.qty > _QTY_EPSILON:
                    # Selling against long inventory
                    if pos.qty < qty - _QTY_EPSILON and not is_option_contract:
                        logger.warning(f"Insufficient inventory for paper sell of {qty} {symbol}: pos={current_qty}")
                        self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
                        return False

                    if pos.qty < qty - _QTY_EPSILON and is_option_contract:
                        # Overselling past long inventory opens/increases a naked
                        # short -- require the same collateral check as opening
                        # one from flat (below).
                        if collateral_required and collateral_required > 0 and acc.cash_balance < collateral_required:
                            logger.warning(f"Insufficient collateral for paper short sell of {qty} {symbol}: required={collateral_required}, cash={acc.cash_balance}")
                            self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
                            return False

                    total_proceeds = cost_basis_impact - commission_and_fees
                    acc.cash_balance += total_proceeds

                    closed_qty = min(pos.qty, qty)
                    prorated_comm = commission_and_fees * (closed_qty / qty) if qty > 0 else 0.0
                    self._record_closed_trade(session, pos, closed_qty, fill_price, "flatten", prorated_comm)

                    pos.qty -= qty
                    if abs(pos.qty) < _QTY_EPSILON:
                        session.delete(pos)
                    elif pos.qty < -_QTY_EPSILON:
                        # Long fully closed and flipped through zero into a
                        # brand-new short -- same entry_ts reset reasoning as
                        # the buy-side flip above.
                        pos.avg_entry_price = fill_price
                        pos.entry_ts = now_ts
                else:
                    # Selling to open short (options or short stock)
                    if not is_option_contract:
                        logger.warning(f"Insufficient inventory for paper sell of {qty} {symbol}: pos={current_qty}")
                        self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
                        return False

                    if collateral_required and collateral_required > 0 and acc.cash_balance < collateral_required:
                        logger.warning(f"Insufficient collateral for paper short sell of {qty} {symbol}: required={collateral_required}, cash={acc.cash_balance}")
                        self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
                        return False

                    total_proceeds = cost_basis_impact - commission_and_fees
                    acc.cash_balance += total_proceeds

                    if pos:
                        # Averaging into an existing short -- entry_ts left
                        # untouched, same reasoning as the long-side average-in.
                        new_qty = pos.qty - qty
                        pos.avg_entry_price = ((abs(pos.qty) * pos.avg_entry_price) + cost_basis_impact) / abs(new_qty)
                        pos.qty = new_qty
                    else:
                        pos = PaperPosition(symbol=symbol.upper(), strategy_id=strategy_id, pilot_id=pilot_id, experiment_arm=experiment_arm, qty=-qty, avg_entry_price=fill_price, entry_ts=now_ts)
                        session.add(pos)

            else:
                return False

            self._insert_order(session, client_order_id, symbol, side, qty, qty, fill_price, status, target_qty, strategy_id, pilot_id, experiment_arm, leg_group_id, order_kind)
            return True

    def apply_multi_leg_fill(
        self,
        client_order_id: str,
        symbol: str,
        strategy_name: str,
        contracts: int,
        legs: List[Dict[str, Any]],
        net_cash_impact: float,
        commission_and_fees: float,
        collateral_required: Optional[float] = None,
        status: str = OrderStatus.FILLED,
        strategy_id: str = "untagged",
        pilot_id: Optional[str] = None,
        experiment_arm: Optional[str] = None,
    ) -> bool:
        """
        Executes an atomic multi-leg options order fill across all legs and updates cash balance.
        net_cash_impact: signed cash change (negative for net debit + commission, positive for net credit - commission).
        """
        if self._readonly:
            raise RuntimeError("Cannot apply fill in readonly mode.")

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if not acc:
                return False

            # Validate all legs have a positive fill_price before processing
            for leg in legs:
                try:
                    price = float(leg.get("fill_price", 0.0))
                    if price <= 0.0:
                        raise ValueError("Price must be positive")
                except (ValueError, TypeError):
                    logger.warning(f"Rejecting multi-leg order {client_order_id}: missing or invalid fill_price in leg")
                    self._insert_order(
                        session, client_order_id, f"{strategy_name} {symbol}", "BUY" if net_cash_impact < 0 else "SELL",
                        float(contracts), 0.0, None, OrderStatus.REJECTED, float(contracts),
                        strategy_id, pilot_id, experiment_arm, None, "parent"
                    )
                    return False

            # Check cash sufficiency
            if net_cash_impact < 0 and acc.cash_balance < abs(net_cash_impact):
                logger.warning(
                    f"Insufficient funds for multi-leg order {client_order_id}: "
                    f"cash={acc.cash_balance:.2f}, required={abs(net_cash_impact):.2f}"
                )
                self._insert_order(
                    session, client_order_id, symbol, "BUY",
                    float(contracts), 0.0, None, OrderStatus.REJECTED, float(contracts),
                    strategy_id, pilot_id, experiment_arm, None, "parent"
                )
                return False

            if collateral_required and collateral_required > 0 and acc.cash_balance < collateral_required:
                logger.warning(
                    f"Insufficient collateral for multi-leg credit order {client_order_id}: "
                    f"cash={acc.cash_balance:.2f}, collateral={collateral_required:.2f}"
                )
                self._insert_order(
                    session, client_order_id, symbol, "SELL",
                    float(contracts), 0.0, None, OrderStatus.REJECTED, float(contracts),
                    strategy_id, pilot_id, experiment_arm, None, "parent"
                )
                return False

            # Update account cash
            acc.cash_balance += net_cash_impact

            now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

            # Update each constituent leg position
            for idx, leg in enumerate(legs):
                leg_symbol = str(leg["symbol"]).upper().strip()
                leg_side = str(leg.get("side", "buy")).lower().strip()
                leg_qty = float(leg.get("qty", contracts))
                leg_fill_price = float(leg["fill_price"])  # Already validated above
                leg_cost = leg_qty * leg_fill_price

                pos = session.query(PaperPosition).filter_by(symbol=leg_symbol, strategy_id=strategy_id).with_for_update().first()
                if not pos:
                    untagged_pos = session.query(PaperPosition).filter_by(symbol=leg_symbol, strategy_id="untagged").with_for_update().first()
                    if untagged_pos:
                        if leg_side == "buy" and untagged_pos.qty < -_QTY_EPSILON:
                            pos = untagged_pos
                        elif leg_side == "sell" and untagged_pos.qty > _QTY_EPSILON:
                            pos = untagged_pos

                if leg_side == "buy":
                    if pos and pos.qty < -_QTY_EPSILON:
                        # Buying to close short
                        closed_qty = min(abs(pos.qty), leg_qty)
                        prorated_comm = commission_and_fees * (closed_qty / leg_qty) / len(legs) if leg_qty > 0 else 0.0
                        self._record_closed_trade(session, pos, closed_qty, leg_fill_price, "flatten", prorated_comm)

                        new_qty = pos.qty + leg_qty
                        if abs(new_qty) < _QTY_EPSILON:
                            session.delete(pos)
                        elif new_qty > 0:
                            # Flipped through zero -- brand-new position basis.
                            pos.qty = new_qty
                            pos.avg_entry_price = leg_fill_price
                            pos.entry_ts = now_ts
                        else:
                            pos.qty = new_qty
                    elif pos:
                        # Averaging in -- entry_ts left untouched.
                        new_qty = pos.qty + leg_qty
                        pos.avg_entry_price = ((pos.qty * pos.avg_entry_price) + leg_cost) / new_qty
                        pos.qty = new_qty
                    else:
                        pos = PaperPosition(symbol=leg_symbol, strategy_id=strategy_id, pilot_id=pilot_id, experiment_arm=experiment_arm, qty=leg_qty, avg_entry_price=leg_fill_price, entry_ts=now_ts)
                        session.add(pos)
                elif leg_side == "sell":
                    if pos and pos.qty > _QTY_EPSILON:
                        closed_qty = min(pos.qty, leg_qty)
                        prorated_comm = commission_and_fees * (closed_qty / leg_qty) / len(legs) if leg_qty > 0 else 0.0
                        self._record_closed_trade(session, pos, closed_qty, leg_fill_price, "flatten", prorated_comm)

                        pos.qty -= leg_qty
                        if abs(pos.qty) < _QTY_EPSILON:
                            session.delete(pos)
                        elif pos.qty < -_QTY_EPSILON:
                            # Flipped through zero -- brand-new position basis.
                            pos.avg_entry_price = leg_fill_price
                            pos.entry_ts = now_ts
                    elif pos:
                        # Averaging in -- entry_ts left untouched.
                        new_qty = pos.qty - leg_qty
                        pos.avg_entry_price = ((abs(pos.qty) * pos.avg_entry_price) + leg_cost) / abs(new_qty)
                        pos.qty = new_qty
                    else:
                        pos = PaperPosition(symbol=leg_symbol, strategy_id=strategy_id, pilot_id=pilot_id, experiment_arm=experiment_arm, qty=-leg_qty, avg_entry_price=leg_fill_price, entry_ts=now_ts)
                        session.add(pos)

                # Record individual leg order
                leg_coid = f"{client_order_id}_L{idx+1}"
                self._insert_order(
                    session, leg_coid, leg_symbol, leg_side, leg_qty, leg_qty, leg_fill_price, status, leg_qty,
                    strategy_id, pilot_id, experiment_arm, client_order_id, "leg"
                )

            # Record parent multi-leg order
            parent_side = "BUY" if net_cash_impact < 0 else "SELL"
            avg_contract_price = abs(net_cash_impact) / (contracts * 100.0) if contracts > 0 else 0.0
            self._insert_order(
                session, client_order_id, symbol, parent_side,
                float(contracts), float(contracts), avg_contract_price, status, float(contracts),
                strategy_id, pilot_id, experiment_arm, None, "parent"
            )

            return True

    def apply_roll_fill(
        self,
        client_order_id: str,
        symbol: str,
        close_legs: List[Dict[str, Any]],
        open_legs: List[Dict[str, Any]],
        net_cash_impact: Optional[float] = None,
        commission_and_fees: Optional[float] = None,
        contracts: int = 1,
        limit_price: Optional[float] = None,
        collateral_required: Optional[float] = None,
        status: str = OrderStatus.FILLED,
        strategy_id: str = "untagged",
        pilot_id: Optional[str] = None,
        experiment_arm: Optional[str] = None,
    ) -> bool:
        """
        Executes an atomic roll order: closes existing position legs and opens new expiration legs in a single transaction.
        """
        if self._readonly:
            raise RuntimeError("Cannot apply fill in readonly mode.")

        # Combine close and open legs
        all_legs = []
        for l in close_legs:
            all_legs.append({**l, "action": "close"})
        for l in open_legs:
            all_legs.append({**l, "action": "open"})

        if commission_and_fees is None:
            commission_and_fees = 0.65 * contracts * len(all_legs)

        if net_cash_impact is None:
            # Calculate from leg prices. Whether the ×100 contract multiplier
            # applies is determined by WHICH field actually carries the price
            # (this codebase's leg-dict convention: "fill_price" is always
            # already a per-contract dollar amount, "raw_price" is per-share),
            # never by guessing from the price's magnitude -- a premium ≥$50
            # /share (deep ITM, LEAPS) is a legitimate raw_price and must
            # still be scaled by 100, which a `price < 50.0` heuristic gets
            # silently wrong.
            leg_cash_sum = 0.0
            for l in all_legs:
                side = str(l.get("side", "buy")).lower().strip()
                qty = float(l.get("qty", contracts))
                option_multiplier = 100.0 if " " in str(l.get("symbol", "")) else 1.0
                fill_price_raw = l.get("fill_price")
                if fill_price_raw:
                    # Already a full per-contract dollar amount -- use as-is.
                    price = float(fill_price_raw)
                    scale = 1.0
                else:
                    price = float(l.get("raw_price", 0.0) or 0.0)
                    scale = option_multiplier
                if side == "buy":
                    leg_cash_sum -= qty * price * scale
                else:
                    leg_cash_sum += qty * price * scale
            net_cash_impact = leg_cash_sum - commission_and_fees

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if not acc:
                return False

            # Validate all legs have a positive price before processing
            for leg in all_legs:
                try:
                    price_val = leg.get("fill_price")
                    if price_val is None:
                        price_val = leg.get("raw_price")
                    price = float(price_val if price_val is not None else 0.0)
                    if price <= 0.0:
                        raise ValueError("Price must be positive")
                except (ValueError, TypeError):
                    logger.warning(f"Rejecting roll order {client_order_id}: missing or invalid fill_price/raw_price in leg")
                    self._insert_order(
                        session, client_order_id, f"ROLL {symbol}", "BUY" if net_cash_impact < 0 else "SELL",
                        float(contracts), 0.0, None, OrderStatus.REJECTED, float(contracts),
                        strategy_id, pilot_id, experiment_arm, None, "parent"
                    )
                    return False

            # Check cash sufficiency
            if net_cash_impact < 0 and acc.cash_balance < abs(net_cash_impact):
                logger.warning(
                    f"Insufficient funds for roll order {client_order_id}: "
                    f"cash={acc.cash_balance:.2f}, required={abs(net_cash_impact):.2f}"
                )
                self._insert_order(
                    session, client_order_id, symbol, "BUY",
                    float(contracts), 0.0, None, OrderStatus.REJECTED, float(contracts),
                    strategy_id, pilot_id, experiment_arm, None, "parent"
                )
                return False

            if collateral_required and collateral_required > 0 and acc.cash_balance < collateral_required:
                logger.warning(
                    f"Insufficient collateral for roll order {client_order_id}: "
                    f"cash={acc.cash_balance:.2f}, collateral={collateral_required:.2f}"
                )
                self._insert_order(
                    session, client_order_id, symbol, "SELL",
                    float(contracts), 0.0, None, OrderStatus.REJECTED, float(contracts),
                    strategy_id, pilot_id, experiment_arm, None, "parent"
                )
                return False

            # Update account cash
            acc.cash_balance += net_cash_impact

            now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

            # Update each constituent leg position
            for idx, leg in enumerate(all_legs):
                leg_symbol = str(leg["symbol"]).upper().strip()
                leg_side = str(leg.get("side", "buy")).lower().strip()
                leg_qty = float(leg.get("qty", contracts))
                price_val = leg.get("fill_price")
                if price_val is None:
                    price_val = leg.get("raw_price")
                leg_fill_price = float(price_val)  # Already validated above
                leg_cost = leg_qty * leg_fill_price

                pos = session.query(PaperPosition).filter_by(symbol=leg_symbol, strategy_id=strategy_id).with_for_update().first()
                if not pos:
                    untagged_pos = session.query(PaperPosition).filter_by(symbol=leg_symbol, strategy_id="untagged").with_for_update().first()
                    if untagged_pos:
                        if leg_side == "buy" and untagged_pos.qty < -_QTY_EPSILON:
                            pos = untagged_pos
                        elif leg_side == "sell" and untagged_pos.qty > _QTY_EPSILON:
                            pos = untagged_pos

                if leg_side == "buy":
                    if pos and pos.qty < -_QTY_EPSILON:
                        # Buying to close short
                        closed_qty = min(abs(pos.qty), leg_qty)
                        prorated_comm = commission_and_fees * (closed_qty / leg_qty) / len(all_legs) if leg_qty > 0 else 0.0
                        self._record_closed_trade(session, pos, closed_qty, leg_fill_price, "roll", prorated_comm)

                        new_qty = pos.qty + leg_qty
                        if abs(new_qty) < _QTY_EPSILON:
                            session.delete(pos)
                        elif new_qty > 0:
                            # Flipped through zero -- brand-new position basis.
                            pos.qty = new_qty
                            pos.avg_entry_price = leg_fill_price
                            pos.entry_ts = now_ts
                        else:
                            pos.qty = new_qty
                    elif pos:
                        # Averaging in -- entry_ts left untouched.
                        new_qty = pos.qty + leg_qty
                        pos.avg_entry_price = ((pos.qty * pos.avg_entry_price) + leg_cost) / new_qty
                        pos.qty = new_qty
                    else:
                        pos = PaperPosition(symbol=leg_symbol, strategy_id=strategy_id, pilot_id=pilot_id, experiment_arm=experiment_arm, qty=leg_qty, avg_entry_price=leg_fill_price, entry_ts=now_ts)
                        session.add(pos)
                elif leg_side == "sell":
                    if pos and pos.qty > _QTY_EPSILON:
                        closed_qty = min(pos.qty, leg_qty)
                        prorated_comm = commission_and_fees * (closed_qty / leg_qty) / len(all_legs) if leg_qty > 0 else 0.0
                        self._record_closed_trade(session, pos, closed_qty, leg_fill_price, "roll", prorated_comm)

                        pos.qty -= leg_qty
                        if abs(pos.qty) < _QTY_EPSILON:
                            session.delete(pos)
                        elif pos.qty < -_QTY_EPSILON:
                            # Flipped through zero -- brand-new position basis.
                            pos.avg_entry_price = leg_fill_price
                            pos.entry_ts = now_ts
                    elif pos:
                        # Averaging in -- entry_ts left untouched.
                        new_qty = pos.qty - leg_qty
                        pos.avg_entry_price = ((abs(pos.qty) * pos.avg_entry_price) + leg_cost) / abs(new_qty)
                        pos.qty = new_qty
                    else:
                        pos = PaperPosition(symbol=leg_symbol, strategy_id=strategy_id, pilot_id=pilot_id, experiment_arm=experiment_arm, qty=-leg_qty, avg_entry_price=leg_fill_price, entry_ts=now_ts)
                        session.add(pos)

                # Record individual leg order
                leg_coid = f"{client_order_id}_L{idx+1}"
                self._insert_order(
                    session, leg_coid, leg_symbol, leg_side, leg_qty, leg_qty, leg_fill_price, status, leg_qty,
                    strategy_id, pilot_id, experiment_arm, client_order_id, "leg"
                )

            # Record parent roll order
            parent_side = "BUY" if net_cash_impact < 0 else "SELL"
            avg_contract_price = abs(net_cash_impact) / (contracts * 100.0) if contracts > 0 else 0.0
            self._insert_order(
                session, client_order_id, symbol, parent_side,
                float(contracts), float(contracts), avg_contract_price, status, float(contracts),
                strategy_id, pilot_id, experiment_arm, None, "parent"
            )

            return True

    def _insert_order(self, session, client_order_id, symbol, side, qty, filled_qty, fill_price, status, target_qty=None, strategy_id=None, pilot_id=None, experiment_arm=None, leg_group_id=None, order_kind=None):
        # We use a derived broker_order_id
        broker_order_id = f"FMP-{client_order_id}"
        
        po = session.query(PaperOrder).filter_by(client_order_id=client_order_id).first()
        if not po:
            po = PaperOrder(
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                symbol=symbol.upper(),
                side=side,
                qty=qty,
                target_qty=target_qty,
                filled_qty=filled_qty,
                filled_avg_price=fill_price,
                status=status,
                strategy_id=strategy_id,
                pilot_id=pilot_id,
                experiment_arm=experiment_arm,
                leg_group_id=leg_group_id,
                order_kind=order_kind,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None)
            )
            session.add(po)
        else:
            po.status = status
            po.filled_qty = filled_qty
            po.filled_avg_price = fill_price
            if strategy_id: po.strategy_id = strategy_id
            if pilot_id: po.pilot_id = pilot_id
            if experiment_arm: po.experiment_arm = experiment_arm
            if leg_group_id: po.leg_group_id = leg_group_id
            if order_kind: po.order_kind = order_kind

    def _record_closed_trade(self, session, pos: PaperPosition, closed_qty: float, exit_price: float, close_reason: str, commission: float = 0.0):
        closed_qty_abs = abs(closed_qty)
        is_long = pos.qty > 0
        if is_long:
            realized_pnl = (exit_price - pos.avg_entry_price) * closed_qty_abs
        else:
            realized_pnl = (pos.avg_entry_price - exit_price) * closed_qty_abs

        # NOTE (bug fix, PR 872 remediation): no ×100 option multiplier here.
        # Every writer in this codebase stores option entry/exit prices already
        # as a per-CONTRACT dollar amount -- apply_roll_fill's own docstring
        # states it explicitly: "fill_price is always already a per-contract
        # dollar amount". Both entry_price (pos.avg_entry_price) and
        # exit_price are per-contract for options and per-share for equities;
        # qty (contracts vs. shares) is what varies, not the price convention.
        # Re-applying ×100 here double-counts the multiplier and inflated
        # realized_pnl 100x for every closed option trade.
        realized_pnl -= commission

        # CONSTRAINT #4 / "Degenerate-std guard convention" (CLAUDE.md): guard
        # with `>= 1e-12`, never an exact `> 0`/`== 0` check, and the
        # undefined case must be None, never a fabricated 0.0. Real production
        # rows can have avg_entry_price <= 0 (see
        # scripts/purge_corrupt_paper_options.py) -- those must not close as a
        # fabricated "flat trade".
        if abs(pos.avg_entry_price) >= 1e-12:
            if is_long:
                realized_pnl_pct = (exit_price - pos.avg_entry_price) / pos.avg_entry_price
            else:
                realized_pnl_pct = (pos.avg_entry_price - exit_price) / pos.avg_entry_price
        else:
            realized_pnl_pct = None

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Real entry time when known (set at position-open time on
        # PaperPosition.entry_ts); None only for a genuinely unknown
        # (legacy/migrated) position -- never fabricated as "now".
        entry_ts = pos.entry_ts
        holding_period_days = (
            (now - entry_ts).total_seconds() / 86400.0 if entry_ts is not None else None
        )

        pct = PaperClosedTrade(
            strategy_id=pos.strategy_id,
            pilot_id=pos.pilot_id,
            experiment_arm=pos.experiment_arm,
            symbol=pos.symbol,
            side="buy" if is_long else "sell",
            qty=closed_qty_abs,
            entry_ts=entry_ts,
            entry_price=pos.avg_entry_price,
            exit_ts=now,
            exit_price=exit_price,
            commission=commission,
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
            holding_period_days=holding_period_days,
            close_reason=close_reason,
            leg_group_id=None,
        )
        session.add(pct)
        session.flush()

        if getattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True):
            try:
                import transactions_store
                store = transactions_store.TransactionsStore()
                trade_id = store.record_trade(
                    symbol=pos.symbol,
                    side="buy" if is_long else "sell",
                    # Real entry time when known; None lets
                    # transactions_store.record_trade's own documented
                    # fallback apply (it substitutes "now" internally when
                    # entry_ts is falsy) rather than us fabricating a "now"
                    # here and passing it off as real.
                    entry_ts=entry_ts,
                    entry_price=pos.avg_entry_price,
                    # No option_multiplier scaling -- shares/contracts is the
                    # raw closed quantity, unscaled (same fix as realized_pnl
                    # above; entry_price/exit_price already carry the correct
                    # per-contract convention for options).
                    shares=closed_qty_abs,
                    strategy=pos.strategy_id,
                    notes=f"Paper bridge, reason: {close_reason}"
                )
                store.close_trade(trade_id, now, exit_price)
            except Exception as exc:
                logger.error(f"Failed to bridge paper trade to transactions_store: {exc}")

    def get_orders(self, status: Optional[str] = None, limit: int = 100) -> List[OrderResult]:
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_orders"):
                    return []
            except Exception:
                return []

        results = []
        with session_scope(self.Session) as session:
            q = session.query(PaperOrder)
            if status:
                q = q.filter_by(status=status)
            q = q.order_by(PaperOrder.timestamp.desc()).limit(limit)
            
            for po in q.all():
                ts = po.timestamp.replace(tzinfo=timezone.utc)
                results.append(OrderResult(
                    client_order_id=po.client_order_id,
                    broker_order_id=po.broker_order_id,
                    status=OrderStatus(po.status),
                    filled_qty=po.filled_qty,
                    filled_avg_price=po.filled_avg_price,
                    submitted_at=ts,
                    filled_at=ts if po.filled_qty > 0 else None,
                    error_message=None
                ))
        return results

    def get_full_orders(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if self._readonly:
            try:
                insp = inspect(self.engine)
                if not insp.has_table("paper_orders"):
                    return []
            except Exception:
                return []

        results = []
        with session_scope(self.Session) as session:
            q = session.query(PaperOrder)
            if status:
                q = q.filter_by(status=status)
            q = q.order_by(PaperOrder.timestamp.desc()).limit(limit)
            
            for po in q.all():
                ts = po.timestamp.replace(tzinfo=timezone.utc)
                results.append({
                    "order_id": po.client_order_id,
                    "symbol": po.symbol,
                    "side": po.side.upper(),
                    "qty": po.qty,
                    "price": po.filled_avg_price or 0.0,
                    "status": po.status,
                    "filled_qty": po.filled_qty,
                    "filled_avg_price": po.filled_avg_price,
                    "created_at": ts.isoformat(),
                    "strategy_id": po.strategy_id,
                    "pilot_id": po.pilot_id,
                    "experiment_arm": po.experiment_arm,
                    "leg_group_id": po.leg_group_id,
                    "order_kind": po.order_kind,
                })
        return results

    def settle_expired_options(
        self,
        market_provider=None,
        current_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """
        Scans all open option positions, detects contracts that have reached or passed
        expiration (DTE <= 0), calculates intrinsic value settlement, credits/debits
        cash balance, deletes settled positions, and records settlement orders.
        """
        if self._readonly:
            return []

        now_d = current_date or datetime.now(timezone.utc).date()
        settled_records = []

        with session_scope(self.Session) as session:
            acc = session.query(PaperAccount).filter_by(id=1).with_for_update().first()
            if not acc:
                return []

            positions = session.query(PaperPosition).filter(PaperPosition.qty != 0).all()
            for pos in positions:
                m = _OPTION_SYMBOL_REGEX.match(pos.symbol)
                if not m:
                    continue

                ticker = m.group(1).upper()
                exp_str = m.group(2)
                strike = float(m.group(3))
                opt_type = m.group(4).upper()

                try:
                    exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                # Check if expired
                if exp_d <= now_d:
                    # Resolve underlying spot price
                    spot = None
                    if market_provider:
                        try:
                            q = market_provider.get_latest_quote(ticker)
                            if q and getattr(q, "price", None):
                                spot = float(q.price)
                        except Exception:
                            spot = None
                    if spot is None:
                        # No honest spot price available -- do NOT fabricate one
                        # (CONSTRAINT #4). Leave the position open so a later
                        # call, once a real quote is available again, can
                        # settle it at its actual intrinsic value instead of
                        # silently forcing it to zero.
                        logger.warning(
                            "settle_expired_options: no quote available for %s "
                            "(expired %s); skipping settlement rather than "
                            "fabricating spot=strike.",
                            ticker, exp_str,
                        )
                        continue

                    # Calculate intrinsic value
                    if opt_type == "CALL":
                        intrinsic = max(0.0, spot - strike)
                    else:
                        intrinsic = max(0.0, strike - spot)

                    contracts = abs(float(pos.qty))
                    is_long = pos.qty > 0

                    # Cash settlement
                    if is_long:
                        cash_settlement = intrinsic * contracts * 100.0
                        acc.cash_balance += cash_settlement
                    else:
                        cash_settlement = -(intrinsic * contracts * 100.0)
                        acc.cash_balance += cash_settlement  # cash_settlement is negative

                    # Record closed trade. `intrinsic` above is a per-SHARE
                    # dollar amount (proven by cash_settlement's own *100.0
                    # conversion just above), but pos.avg_entry_price is
                    # already a per-CONTRACT dollar amount (this codebase's
                    # option-price convention -- see apply_roll_fill's
                    # docstring) and _record_closed_trade expects exit_price
                    # on that same per-contract basis. Convert before passing
                    # so the ledger row's realized_pnl agrees, by
                    # construction, with the real cash_settlement applied to
                    # acc.cash_balance above -- an apples-to-oranges
                    # subtraction here previously produced a fake, wildly
                    # wrong PnL (or, after removing the ×100 multiplier in
                    # _record_closed_trade alone, a mismatched sign/magnitude
                    # against the real cash credit).
                    intrinsic_per_contract = intrinsic * 100.0
                    self._record_closed_trade(session, pos, contracts, intrinsic_per_contract, "expiry_settlement", 0.0)

                    # Delete position
                    session.delete(pos)

                    # Record settlement order
                    settle_order_id = f"SETTLE_{pos.symbol}_{now_d.strftime('%Y%m%d')}"
                    self._insert_order(
                        session=session,
                        client_order_id=settle_order_id,
                        symbol=pos.symbol,
                        side="SELL" if is_long else "BUY",
                        qty=contracts,
                        filled_qty=contracts,
                        fill_price=intrinsic,
                        status="SETTLED" if intrinsic > 0 else "EXPIRED",
                        target_qty=contracts,
                    )

                    settled_records.append({
                        "symbol": pos.symbol,
                        "ticker": ticker,
                        "expiration": exp_str,
                        "strike": strike,
                        "option_type": opt_type,
                        "contracts": contracts,
                        "is_long": is_long,
                        "spot_price": spot,
                        "intrinsic_per_share": intrinsic,
                        "cash_settlement": cash_settlement,
                        "status": "SETTLED" if intrinsic > 0 else "EXPIRED",
                    })

        return settled_records

