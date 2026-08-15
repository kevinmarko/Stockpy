import pytest
import asyncio
from execution.fix_gateway import (
    FixMessage, FixMsgType, NewOrderSingle, ExecutionReport, 
    Side, OrdStatus, FixSession, FixSessionState, MultiVenueAggregator
)

def test_fix_message_serialization():
    msg = FixMessage(FixMsgType.HEARTBEAT, "CLIENT1", "EXCHANGE", 1)
    msg.tags["108"] = 30
    msg_dict = msg.to_dict()
    assert msg_dict["35"] == "0"
    assert msg_dict["49"] == "CLIENT1"
    assert msg_dict["56"] == "EXCHANGE"
    assert msg_dict["34"] == 1
    assert "52" in msg_dict
    assert msg_dict["108"] == 30

def test_new_order_single():
    nos = NewOrderSingle("CLIENT1", "EXCHANGE", 2, "ORD123", "AAPL", Side.BUY, 100.0, 150.0)
    nos_dict = nos.to_dict()
    assert nos_dict["35"] == "D"
    assert nos_dict["11"] == "ORD123"
    assert nos_dict["55"] == "AAPL"
    assert nos_dict["54"] == "1"
    assert nos_dict["38"] == 100.0
    assert nos_dict["44"] == 150.0
    assert nos_dict["40"] == "2"

@pytest.mark.anyio
async def test_fix_session_state_machine():
    session = FixSession("CLIENT1", "EXCHANGE", heartbeat_int=1)
    assert session.state == FixSessionState.DISCONNECTED
    
    # Connect
    await session.connect()
    assert session.state == FixSessionState.CONNECTED
    assert session.outbound_seq_num == 2 # Logon sent
    
    # Send Order
    cl_ord_id = session.send_order("AAPL", Side.BUY, 100.0, 150.0)
    assert session.outbound_seq_num == 3
    assert session.order_book[cl_ord_id]["status"] == OrdStatus.NEW
    
    # Simulate Execution Report
    exec_rep = ExecutionReport(
        "EXCHANGE", "CLIENT1", 1, "EXCH_ORD_1", "EXEC_1", 
        OrdStatus.FILLED, OrdStatus.FILLED, "AAPL", Side.BUY, 0.0, 100.0, 150.0
    )
    exec_dict = exec_rep.to_dict()
    exec_dict["37"] = cl_ord_id # route back to cl_ord_id for simulation
    
    session.simulate_receive(exec_dict)
    assert session.inbound_seq_num == 2
    assert session.order_book[cl_ord_id]["status"] == OrdStatus.FILLED
    assert session.order_book[cl_ord_id]["filled"] == 100.0
    
    # Wait for heartbeat
    await asyncio.sleep(1.1)
    assert session.outbound_seq_num >= 4
    
    # Disconnect
    await session.disconnect()
    assert session.state == FixSessionState.DISCONNECTED

@pytest.mark.anyio
async def test_multi_venue_aggregator_routing():
    aggregator = MultiVenueAggregator()
    
    # Seed numpy random to get deterministic liquidity depths for the test
    import numpy as np
    np.random.seed(42)
    import random
    random.seed(42)
    
    qty_to_route = 1500.0
    fills = await aggregator.route_order("AAPL", Side.BUY, qty_to_route, 150.0)
    
    total_filled = sum(f["fill_qty"] for f in fills)
    
    assert len(fills) > 0
    # The aggregator prioritizes by fee, then sweeps until filled.
    # BOX has lowest fee (0.10), then MIAX (0.25), then PHLX (0.40), then CBOE (0.45)
    sorted_venues = [f["venue"] for f in fills]
    assert sorted_venues[0] == "BOX"
    
    # We should have swept until our qty is satisfied (or liquidity exhausted)
    assert total_filled <= qty_to_route
