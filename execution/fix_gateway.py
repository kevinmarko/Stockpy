"""
FIX 4.4 Simulated Gateway & Cross-Exchange Routing Engine
Simulates an asynchronous event-driven FIX session and multi-venue liquidity routing.
AST Safety: Strict (stdlib, numpy, pandas only, no heavy engine imports).
"""
import asyncio
import time
import random
import uuid
import json
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
import numpy as np

# --- FIX Data Types ---

class FixMsgType(str, Enum):
    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    LOGON = "A"
    NEW_ORDER_SINGLE = "D"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REPLACE = "G"
    ORDER_CANCEL_REQUEST = "F"
    REJECT = "3"

class OrdStatus(str, Enum):
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    REPLACED = "5"
    REJECTED = "8"

class Side(str, Enum):
    BUY = "1"
    SELL = "2"

class FixMessage:
    def __init__(self, msg_type: FixMsgType, sender_comp_id: str, target_comp_id: str, seq_num: int):
        self.msg_type = msg_type
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.seq_num = seq_num
        self.sending_time = time.time()
        self.tags: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "35": self.msg_type.value,
            "49": self.sender_comp_id,
            "56": self.target_comp_id,
            "34": self.seq_num,
            "52": self.sending_time,
            **self.tags
        }

class NewOrderSingle(FixMessage):
    def __init__(self, sender_comp_id: str, target_comp_id: str, seq_num: int, 
                 cl_ord_id: str, symbol: str, side: Side, order_qty: float, 
                 price: float, ord_type: str = "2"): # 2 = Limit
        super().__init__(FixMsgType.NEW_ORDER_SINGLE, sender_comp_id, target_comp_id, seq_num)
        self.tags.update({
            "11": cl_ord_id,
            "55": symbol,
            "54": side.value,
            "38": order_qty,
            "44": price,
            "40": ord_type
        })

class ExecutionReport(FixMessage):
    def __init__(self, sender_comp_id: str, target_comp_id: str, seq_num: int, 
                 order_id: str, cl_ord_id: str, exec_id: str, exec_type: OrdStatus, ord_status: OrdStatus,
                 symbol: str, side: Side, leaves_qty: float, cum_qty: float, avg_px: float):
        super().__init__(FixMsgType.EXECUTION_REPORT, sender_comp_id, target_comp_id, seq_num)
        self.tags.update({
            "37": order_id,
            "11": cl_ord_id,
            "17": exec_id,
            "150": exec_type.value,
            "39": ord_status.value,
            "55": symbol,
            "54": side.value,
            "151": leaves_qty,
            "14": cum_qty,
            "6": avg_px
        })

class OrderCancelReplace(FixMessage):
    def __init__(self, sender_comp_id: str, target_comp_id: str, seq_num: int, 
                 orig_cl_ord_id: str, cl_ord_id: str, symbol: str, side: Side, 
                 order_qty: float, price: float):
        super().__init__(FixMsgType.ORDER_CANCEL_REPLACE, sender_comp_id, target_comp_id, seq_num)
        self.tags.update({
            "41": orig_cl_ord_id,
            "11": cl_ord_id,
            "55": symbol,
            "54": side.value,
            "38": order_qty,
            "44": price
        })

# --- FIX Session State Machine ---

class FixSessionState(Enum):
    DISCONNECTED = 0
    LOGGING_ON = 1
    CONNECTED = 2
    LOGGING_OFF = 3

class FixSession:
    def __init__(self, sender_comp_id: str, target_comp_id: str, heartbeat_int: int = 30):
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.heartbeat_int = heartbeat_int
        self.state = FixSessionState.DISCONNECTED
        self.outbound_seq_num = 1
        self.inbound_seq_num = 1
        self.message_log: List[Dict[str, Any]] = []
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.order_book: Dict[str, Dict[str, Any]] = {}
    
    async def connect(self):
        self.state = FixSessionState.LOGGING_ON
        # Simulate network delay
        await asyncio.sleep(0.05)
        logon_msg = FixMessage(FixMsgType.LOGON, self.sender_comp_id, self.target_comp_id, self.outbound_seq_num)
        logon_msg.tags["108"] = self.heartbeat_int
        self._send(logon_msg)
        self.state = FixSessionState.CONNECTED
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
    
    async def disconnect(self):
        self.state = FixSessionState.LOGGING_OFF
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self.state = FixSessionState.DISCONNECTED

    async def _heartbeat_loop(self):
        try:
            while self.state == FixSessionState.CONNECTED:
                await asyncio.sleep(self.heartbeat_int)
                hb = FixMessage(FixMsgType.HEARTBEAT, self.sender_comp_id, self.target_comp_id, self.outbound_seq_num)
                self._send(hb)
        except asyncio.CancelledError:
            pass

    def _send(self, msg: FixMessage):
        self.message_log.append(msg.to_dict())
        self.outbound_seq_num += 1

    def send_order(self, symbol: str, side: Side, qty: float, price: float) -> str:
        cl_ord_id = str(uuid.uuid4())
        msg = NewOrderSingle(
            self.sender_comp_id, self.target_comp_id, self.outbound_seq_num,
            cl_ord_id, symbol, side, qty, price
        )
        self._send(msg)
        # Store local state expectation
        self.order_book[cl_ord_id] = {
            "symbol": symbol, "side": side, "qty": qty, "price": price, 
            "status": OrdStatus.NEW, "filled": 0.0
        }
        return cl_ord_id

    def simulate_receive(self, msg_dict: Dict[str, Any]):
        """Simulate receiving a message from the exchange."""
        seq = msg_dict.get("34", 0)
        if seq >= self.inbound_seq_num:
            self.inbound_seq_num = seq + 1
        
        msg_type = msg_dict.get("35")
        if msg_type == FixMsgType.EXECUTION_REPORT.value:
            cl_ord_id = msg_dict.get("11")
            if cl_ord_id in self.order_book:
                self.order_book[cl_ord_id]["status"] = OrdStatus(msg_dict.get("39"))
                self.order_book[cl_ord_id]["filled"] = msg_dict.get("14", 0.0)


# --- Multi-Venue Aggregator ---

class VenueConfig:
    def __init__(self, name: str, base_latency_ms: float, liquidity_depth: float, fee_per_contract: float):
        self.name = name
        self.base_latency_ms = base_latency_ms
        self.liquidity_depth = liquidity_depth
        self.fee_per_contract = fee_per_contract

class MultiVenueAggregator:
    """
    Simulates cross-exchange routing and liquidity aggregation.
    Venues: CBOE, MIAX, BOX, PHLX.
    """
    def __init__(self):
        self.venues = {
            "CBOE": VenueConfig("CBOE", 1.2, 1000.0, 0.45),
            "MIAX": VenueConfig("MIAX", 0.8, 500.0, 0.25),
            "BOX":  VenueConfig("BOX",  2.5, 300.0, 0.10),
            "PHLX": VenueConfig("PHLX", 1.5, 800.0, 0.40)
        }
    
    async def route_order(self, symbol: str, side: Side, qty: float, limit_price: float) -> List[Dict[str, Any]]:
        """
        Smart Order Router logic:
        1. Ping venues for simulated quotes.
        2. Sort venues by fee (assuming all hit the limit price in this simulation) + latency.
        3. Sweep venues until qty is filled.
        """
        # 1. Simulate venue quotes (adding slight latency jitter)
        quotes = []
        for name, config in self.venues.items():
            latency = config.base_latency_ms + random.uniform(0.1, 0.5)
            # Simulated available depth at this exact ms
            avail_qty = max(0, np.random.normal(config.liquidity_depth, config.liquidity_depth * 0.2))
            quotes.append({
                "venue": name,
                "latency_ms": latency,
                "avail_qty": avail_qty,
                "fee": config.fee_per_contract
            })
        
        # 2. Sort by lowest fee first (cost-based routing)
        quotes.sort(key=lambda x: (x["fee"], x["latency_ms"]))
        
        # 3. Sweep
        remaining_qty = qty
        fills = []
        
        for q in quotes:
            if remaining_qty <= 0:
                break
            
            # Await the venue latency to simulate travel time
            await asyncio.sleep(q["latency_ms"] / 1000.0)
            
            fill_qty = min(remaining_qty, q["avail_qty"])
            if fill_qty > 0:
                fills.append({
                    "venue": q["venue"],
                    "fill_qty": fill_qty,
                    "fill_price": limit_price, # Perfect limit fill assumption for paper simulation
                    "latency_ms": q["latency_ms"]
                })
                remaining_qty -= fill_qty
                
        return fills
