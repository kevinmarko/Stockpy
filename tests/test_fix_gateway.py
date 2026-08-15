"""
Comprehensive Unit Tests for FIX 4.4 Protocol Gateway and Session State Machine.
Verifies raw serialization, checksum calculation/verification, gap fills,
order cancel/replace lifecycles, session state transitions, and multi-venue routing.
"""
import pytest
import asyncio
from execution.fix_gateway import (
    FixMessage,
    FixMsgType,
    Logon,
    Logout,
    Heartbeat,
    TestRequest,
    ResendRequest,
    SequenceReset,
    Reject,
    NewOrderSingle,
    OrderCancelRequest,
    OrderCancelReplace,
    OrderCancelReject,
    ExecutionReport,
    Side,
    OrdStatus,
    ExecType,
    OrdType,
    TimeInForce,
    CxlRejResponseTo,
    CxlRejReason,
    SessionRejectReason,
    FixSession,
    FixSessionState,
    FixChecksumError,
    FixParseError,
    compute_checksum,
    format_fix_timestamp,
    MultiVenueAggregator,
    RoutingPolicy,
    VenueConfig,
    SOH,
)

# --- 1. Checksum and Raw Serialization Tests ---

def test_compute_checksum():
    # Test deterministic checksum
    raw_sample = "8=FIX.4.4\x019=65\x0135=0\x0149=CLIENT\x0156=EXCHANGE\x0134=1\x0152=20260815-12:00:00.000\x01"
    chk = compute_checksum(raw_sample)
    assert len(chk) == 3
    assert chk.isdigit()

    # Manual verification of byte sum modulo 256
    expected = f"{sum(raw_sample.encode('latin1')) % 256:03d}"
    assert chk == expected


def test_fix_message_serialization_to_dict():
    msg = FixMessage(FixMsgType.HEARTBEAT, "CLIENT1", "EXCHANGE", 1)
    msg.tags["108"] = 30
    msg_dict = msg.to_dict()
    assert msg_dict["35"] == "0"
    assert msg_dict["49"] == "CLIENT1"
    assert msg_dict["56"] == "EXCHANGE"
    assert msg_dict["34"] == 1
    assert "52" in msg_dict
    assert msg_dict["108"] == 30


def test_to_fix_str_and_roundtrip_parsing():
    nos = NewOrderSingle(
        sender_comp_id="TRADER_A",
        target_comp_id="EXCHANGE_B",
        seq_num=42,
        cl_ord_id="ORD-9988",
        symbol="SPY",
        side=Side.BUY,
        order_qty=500.0,
        price=450.25,
        ord_type="2",
        time_in_force=TimeInForce.DAY,
    )
    raw_fix = nos.to_fix_str()
    assert raw_fix.startswith("8=FIX.4.4\x019=")
    assert "35=D\x01" in raw_fix
    assert "49=TRADER_A\x01" in raw_fix
    assert "56=EXCHANGE_B\x01" in raw_fix
    assert "34=42\x01" in raw_fix
    assert "11=ORD-9988\x01" in raw_fix
    assert "55=SPY\x01" in raw_fix
    assert "54=1\x01" in raw_fix
    assert "38=500.0\x01" in raw_fix
    assert "44=450.25\x01" in raw_fix
    assert "59=0\x01" in raw_fix
    assert raw_fix.endswith("\x01")
    assert "10=" in raw_fix

    # Parse back into FixMessage subclass
    parsed = FixMessage.from_fix_str(raw_fix)
    assert isinstance(parsed, NewOrderSingle)
    assert parsed.sender_comp_id == "TRADER_A"
    assert parsed.target_comp_id == "EXCHANGE_B"
    assert parsed.seq_num == 42
    assert parsed.cl_ord_id == "ORD-9988"
    assert parsed.symbol == "SPY"
    assert parsed.side == Side.BUY
    assert parsed.order_qty == 500.0
    assert parsed.price == 450.25
    assert parsed.ord_type == "2"


def test_checksum_validation_error_on_tampered_message():
    nos = NewOrderSingle("CLIENT", "SERVER", 1, "ORD1", "AAPL", Side.BUY, 100, 150)
    raw_fix = nos.to_fix_str()
    # Corrupt body content without updating checksum
    corrupted = raw_fix.replace("150", "999")
    with pytest.raises(FixChecksumError):
        FixMessage.from_fix_str(corrupted, validate_checksum=True)


def test_pipe_delimited_parsing_support():
    pipe_raw = "8=FIX.4.4|9=45|35=0|49=SENDER|56=TARGET|34=1|52=20260815-10:00:00.000|10=000|"
    # Test parsing with validate_checksum=False for arbitrary pipe strings
    parsed = FixMessage.from_fix_str(pipe_raw, validate_checksum=False)
    assert isinstance(parsed, Heartbeat)
    assert parsed.sender_comp_id == "SENDER"
    assert parsed.target_comp_id == "TARGET"
    assert parsed.seq_num == 1


# --- 2. Message Types Construction & Properties Tests ---

def test_all_message_types():
    # Logon
    logon = Logon("SENDER", "TARGET", 1, heartbeat_int=60, reset_seq_num=True, username="user1", password="pw1")
    assert logon.msg_type == FixMsgType.LOGON
    assert logon.heartbeat_int == 60
    assert logon.reset_seq_num_flag is True
    assert logon.tags["553"] == "user1"
    assert logon.tags["554"] == "pw1"

    # Logout
    logout = Logout("SENDER", "TARGET", 2, text="Session Terminating")
    assert logout.msg_type == FixMsgType.LOGOUT
    assert logout.text == "Session Terminating"

    # Heartbeat
    hb = Heartbeat("SENDER", "TARGET", 3, test_req_id="REQ-123")
    assert hb.msg_type == FixMsgType.HEARTBEAT
    assert hb.test_req_id == "REQ-123"

    # TestRequest
    tr = TestRequest("SENDER", "TARGET", 4, test_req_id="SYNC-999")
    assert tr.msg_type == FixMsgType.TEST_REQUEST
    assert tr.test_req_id == "SYNC-999"

    # ResendRequest
    rr = ResendRequest("SENDER", "TARGET", 5, begin_seq_no=2, end_seq_no=4)
    assert rr.msg_type == FixMsgType.RESEND_REQUEST
    assert rr.begin_seq_no == 2
    assert rr.end_seq_no == 4

    # SequenceReset
    sr = SequenceReset("SENDER", "TARGET", 6, new_seq_no=10, gap_fill=True)
    assert sr.msg_type == FixMsgType.SEQUENCE_RESET
    assert sr.new_seq_no == 10
    assert sr.gap_fill is True

    # Reject
    rej = Reject("SENDER", "TARGET", 7, ref_seq_num=5, ref_tag_id=44, session_reject_reason=SessionRejectReason.VALUE_IS_INCORRECT, text="Invalid price")
    assert rej.msg_type == FixMsgType.REJECT
    assert rej.ref_seq_num == 5
    assert rej.ref_tag_id == "44"
    assert rej.text == "Invalid price"

    # OrderCancelRequest
    cxl = OrderCancelRequest("SENDER", "TARGET", 8, orig_cl_ord_id="ORD1", cl_ord_id="CXL1", symbol="TSLA", side=Side.SELL, order_qty=50)
    assert cxl.msg_type == FixMsgType.ORDER_CANCEL_REQUEST
    assert cxl.orig_cl_ord_id == "ORD1"
    assert cxl.cl_ord_id == "CXL1"
    assert cxl.symbol == "TSLA"
    assert cxl.side == Side.SELL

    # OrderCancelReplace
    rpl = OrderCancelReplace("SENDER", "TARGET", 9, orig_cl_ord_id="ORD1", cl_ord_id="RPL1", symbol="TSLA", side=Side.SELL, order_qty=100, price=220.5)
    assert rpl.msg_type == FixMsgType.ORDER_CANCEL_REPLACE
    assert rpl.orig_cl_ord_id == "ORD1"
    assert rpl.cl_ord_id == "RPL1"
    assert rpl.order_qty == 100.0
    assert rpl.price == 220.5

    # OrderCancelReject
    cxl_rej = OrderCancelReject("TARGET", "SENDER", 10, order_id="EXCH_100", cl_ord_id="CXL1", orig_cl_ord_id="ORD1", ord_status=OrdStatus.FILLED, cxl_rej_response_to=CxlRejResponseTo.ORDER_CANCEL_REQUEST, cxl_rej_reason=CxlRejReason.TOO_LATE_TO_CANCEL, text="Order already filled")
    assert cxl_rej.msg_type == FixMsgType.ORDER_CANCEL_REJECT
    assert cxl_rej.order_id == "EXCH_100"
    assert cxl_rej.cxl_rej_response_to == "1"
    assert cxl_rej.cxl_rej_reason == "0"

    # ExecutionReport
    exec_rep = ExecutionReport(
        sender_comp_id="TARGET",
        target_comp_id="SENDER",
        seq_num=11,
        order_id="EXCH_100",
        cl_ord_id="ORD1",
        exec_id="EXEC_001",
        exec_type=ExecType.PARTIAL_FILL,
        ord_status=OrdStatus.PARTIALLY_FILLED,
        symbol="TSLA",
        side=Side.SELL,
        leaves_qty=50.0,
        cum_qty=50.0,
        avg_px=220.5,
        last_px=220.5,
        last_qty=50.0,
    )
    assert exec_rep.msg_type == FixMsgType.EXECUTION_REPORT
    assert exec_rep.exec_type == ExecType.PARTIAL_FILL
    assert exec_rep.ord_status == OrdStatus.PARTIALLY_FILLED
    assert exec_rep.cum_qty == 50.0
    assert exec_rep.leaves_qty == 50.0
    assert exec_rep.avg_px == 220.5
    assert exec_rep.last_px == 220.5
    assert exec_rep.last_qty == 50.0


# --- 3. FIX Session State Machine, Gap Fill & Sync Tests ---

@pytest.mark.anyio
async def test_fix_session_state_machine():
    session = FixSession("CLIENT1", "EXCHANGE", heartbeat_int=1)
    assert session.state == FixSessionState.DISCONNECTED

    # Connect
    await session.connect()
    assert session.state == FixSessionState.CONNECTED
    assert session.outbound_seq_num == 2  # Logon sent

    # Send Order
    cl_ord_id = session.send_order("AAPL", Side.BUY, 100.0, 150.0)
    assert session.outbound_seq_num == 3
    assert session.order_book[cl_ord_id]["status"] == OrdStatus.NEW

    # Simulate Execution Report
    exec_rep = ExecutionReport(
        "EXCHANGE", "CLIENT1", 1, "EXCH_ORD_1", cl_ord_id, "EXEC_1",
        OrdStatus.FILLED, OrdStatus.FILLED, "AAPL", Side.BUY, 0.0, 100.0, 150.0
    )
    exec_dict = exec_rep.to_dict()

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


def test_sequence_gap_detection_and_automatic_resend():
    session = FixSession("CLIENT1", "EXCHANGE")
    assert session.inbound_seq_num == 1

    # Receive sequence 1 normally
    session.simulate_receive({"34": 1, "35": "0"})
    assert session.inbound_seq_num == 2
    assert session.state == FixSessionState.DISCONNECTED

    # Peer sends sequence 5 (gap detected! missing 2, 3, 4)
    session.simulate_receive({"34": 5, "35": "0"})

    # Inbound seq must stay at 2 waiting for gap recovery
    assert session.inbound_seq_num == 2
    assert session.state == FixSessionState.RESEND_PROCESSING

    # A ResendRequest must have been automatically sent requesting from 2 onwards
    last_sent = session.sent_messages[-1]
    assert isinstance(last_sent, ResendRequest)
    assert last_sent.begin_seq_no == 2
    assert last_sent.end_seq_no == 0

    # Now peer responds with SequenceReset GapFill to skip 2..4 up to 5
    gap_fill_msg = SequenceReset("EXCHANGE", "CLIENT1", 2, new_seq_no=5, gap_fill=True)
    session.simulate_receive(gap_fill_msg)

    # Inbound sequence should now advance past the gap and drain buffered message 5!
    assert session.inbound_seq_num == 6
    assert session.state == FixSessionState.CONNECTED


def test_test_request_heartbeat_sync():
    session = FixSession("CLIENT1", "EXCHANGE")
    # Simulate receiving a TestRequest with TestReqID="SYNC_REQ_777"
    tr = TestRequest("EXCHANGE", "CLIENT1", 1, test_req_id="SYNC_REQ_777")
    session.simulate_receive(tr)

    # Verify session immediately responded with a Heartbeat matching the TestReqID
    last_sent = session.sent_messages[-1]
    assert isinstance(last_sent, Heartbeat)
    assert last_sent.test_req_id == "SYNC_REQ_777"


def test_peer_resend_request_handling():
    session = FixSession("CLIENT1", "EXCHANGE")
    # Send order (seq 1) and heartbeat (seq 2)
    session.send_order("AAPL", Side.BUY, 10, 150.0)
    hb = Heartbeat("CLIENT1", "EXCHANGE", 2)
    session._send(hb)

    # Peer requests resend from 1 to 2
    resend_req = ResendRequest("EXCHANGE", "CLIENT1", 1, begin_seq_no=1, end_seq_no=2)
    session.simulate_receive(resend_req)

    # Sequence 1 (NewOrderSingle) should be resent with PossDupFlag="Y"
    # Sequence 2 (Heartbeat/Admin) should be replaced with SequenceReset GapFill
    resent_msgs = session.message_log[-2:]
    assert resent_msgs[0]["35"] == "D"
    assert resent_msgs[0]["43"] == "Y"  # PossDupFlag
    assert resent_msgs[1]["35"] == "4"  # SequenceReset
    assert resent_msgs[1]["123"] == "Y"  # GapFill


# --- 4. Event Callbacks & Order Lifecycle Transitions ---

def test_event_callbacks():
    session = FixSession("CLIENT1", "EXCHANGE")
    exec_reports = []
    rejects = []
    cancel_rejects = []

    session.register_callback("execution_report", lambda msg: exec_reports.append(msg))
    session.register_callback("reject", lambda msg: rejects.append(msg))
    session.register_callback("cancel_reject", lambda msg: cancel_rejects.append(msg))

    # Send order
    cl_ord_id = session.send_order("NVDA", Side.BUY, 100, 120.0)

    # Trigger execution report
    er = ExecutionReport("EXCHANGE", "CLIENT1", 1, "ORD_1", cl_ord_id, "E1", ExecType.NEW, OrdStatus.NEW, "NVDA", Side.BUY, 100, 0, 0)
    session.simulate_receive(er)
    assert len(exec_reports) == 1
    assert exec_reports[0].cl_ord_id == cl_ord_id

    # Trigger reject
    rej = Reject("EXCHANGE", "CLIENT1", 2, ref_seq_num=1, text="System error")
    session.simulate_receive(rej)
    assert len(rejects) == 1
    assert rejects[0].text == "System error"

    # Trigger cancel reject
    cxl_rej = OrderCancelReject("EXCHANGE", "CLIENT1", 3, "ORD_1", "CXL_99", cl_ord_id, OrdStatus.NEW, text="Too late")
    session.simulate_receive(cxl_rej)
    assert len(cancel_rejects) == 1
    assert cancel_rejects[0].orig_cl_ord_id == cl_ord_id


def test_order_cancel_and_replace_lifecycle():
    session = FixSession("CLIENT1", "EXCHANGE")

    # 1. New Order
    ord_id = session.send_order("MSFT", Side.BUY, 200, 400.0)
    assert session.order_book[ord_id]["status"] == OrdStatus.NEW
    assert session.order_book[ord_id]["qty"] == 200.0
    assert session.order_book[ord_id]["price"] == 400.0

    # 2. Execution Report: Partial Fill
    er1 = ExecutionReport("EXCHANGE", "CLIENT1", 1, "EX_1", ord_id, "EXE_1", ExecType.PARTIAL_FILL, OrdStatus.PARTIALLY_FILLED, "MSFT", Side.BUY, 100, 100, 400.0)
    session.simulate_receive(er1)
    assert session.order_book[ord_id]["status"] == OrdStatus.PARTIALLY_FILLED
    assert session.order_book[ord_id]["filled"] == 100.0
    assert session.order_book[ord_id]["leaves_qty"] == 100.0

    # 3. Replace Order (modify price and qty)
    rpl_id = session.replace_order(orig_cl_ord_id=ord_id, new_qty=250.0, new_price=405.0)
    assert session.order_book[ord_id]["status"] == OrdStatus.PENDING_REPLACE
    assert session.order_book[rpl_id]["status"] == OrdStatus.PENDING_REPLACE

    # 4. Exchange Confirms Replace (ExecutionReport with REPLACED status)
    er2 = ExecutionReport(
        "EXCHANGE", "CLIENT1", 2, "EX_1", rpl_id, "EXE_2",
        ExecType.REPLACED, OrdStatus.REPLACED, "MSFT", Side.BUY,
        leaves_qty=150.0, cum_qty=100.0, avg_px=400.0,
        orig_cl_ord_id=ord_id, order_qty=250.0, price=405.0
    )
    session.simulate_receive(er2)
    assert session.order_book[rpl_id]["status"] == OrdStatus.REPLACED
    assert session.order_book[rpl_id]["qty"] == 250.0
    assert session.order_book[rpl_id]["price"] == 405.0
    assert session.order_book[ord_id]["status"] == OrdStatus.REPLACED

    # 5. Cancel Order
    cxl_id = session.cancel_order(orig_cl_ord_id=rpl_id)
    assert session.order_book[rpl_id]["status"] == OrdStatus.PENDING_CANCEL

    # 6. Exchange Confirms Cancel
    er3 = ExecutionReport(
        "EXCHANGE", "CLIENT1", 3, "EX_1", cxl_id, "EXE_3",
        ExecType.CANCELED, OrdStatus.CANCELED, "MSFT", Side.BUY,
        leaves_qty=0.0, cum_qty=100.0, avg_px=400.0,
        orig_cl_ord_id=rpl_id
    )
    session.simulate_receive(er3)
    assert session.order_book[rpl_id]["status"] == OrdStatus.CANCELED
    assert session.order_book[rpl_id]["leaves_qty"] == 0.0


def test_order_cancel_reject_reverts_state():
    session = FixSession("CLIENT1", "EXCHANGE")
    ord_id = session.send_order("GOOGL", Side.BUY, 50, 175.0)

    # Order accepted
    er1 = ExecutionReport("EXCHANGE", "CLIENT1", 1, "EX_G", ord_id, "E1", ExecType.NEW, OrdStatus.NEW, "GOOGL", Side.BUY, 50, 0, 0)
    session.simulate_receive(er1)
    assert session.order_book[ord_id]["status"] == OrdStatus.NEW

    # Try to cancel
    cxl_id = session.cancel_order(orig_cl_ord_id=ord_id)
    assert session.order_book[ord_id]["status"] == OrdStatus.PENDING_CANCEL

    # Exchange rejects cancel
    cxl_rej = OrderCancelReject("EXCHANGE", "CLIENT1", 2, "EX_G", cxl_id, ord_id, ord_status=OrdStatus.NEW, text="Cancel rejected")
    session.simulate_receive(cxl_rej)

    # State should revert from PENDING_CANCEL back to NEW
    assert session.order_book[ord_id]["status"] == OrdStatus.NEW


# --- 5. Multi-Venue Aggregator Routing Test ---

@pytest.mark.anyio
async def test_multi_venue_aggregator_routing():
    aggregator = MultiVenueAggregator()

    import numpy as np
    np.random.seed(42)
    import random
    random.seed(42)

    qty_to_route = 1500.0
    fills = await aggregator.route_order("AAPL", Side.BUY, qty_to_route, 150.0)

    total_filled = sum(f["fill_qty"] for f in fills)
    assert len(fills) > 0
    # Sweeps lowest fee venue first (BOX 0.10, MIAX 0.25, PHLX 0.40, CBOE 0.45)
    sorted_venues = [f["venue"] for f in fills]
    assert sorted_venues[0] == "BOX"
    assert total_filled <= qty_to_route


# --- 6. Additional Edge Cases & Validation Tests ---

def test_sequence_reset_reset_mode():
    session = FixSession("CLIENT1", "EXCHANGE")
    session.inbound_seq_num = 10

    # SequenceReset with gap_fill=False (Reset Mode) forces inbound_seq_num to new_seq_no
    sr = SequenceReset("EXCHANGE", "CLIENT1", 1, new_seq_no=1, gap_fill=False)
    session.simulate_receive(sr)
    assert session.inbound_seq_num == 1


def test_order_replace_reject_reverts_state():
    session = FixSession("CLIENT1", "EXCHANGE")
    ord_id = session.send_order("META", Side.BUY, 100, 500.0)

    # Order accepted
    er1 = ExecutionReport("EXCHANGE", "CLIENT1", 1, "EX_M", ord_id, "E1", ExecType.NEW, OrdStatus.NEW, "META", Side.BUY, 100, 0, 0)
    session.simulate_receive(er1)
    assert session.order_book[ord_id]["status"] == OrdStatus.NEW

    # Send replace request
    rpl_id = session.replace_order(orig_cl_ord_id=ord_id, new_qty=150, new_price=505.0)
    assert session.order_book[ord_id]["status"] == OrdStatus.PENDING_REPLACE

    # Exchange rejects replace (CxlRejResponseTo = 2)
    cxl_rej = OrderCancelReject(
        "EXCHANGE", "CLIENT1", 2, "EX_M", rpl_id, ord_id,
        ord_status=OrdStatus.NEW,
        cxl_rej_response_to=CxlRejResponseTo.ORDER_CANCEL_REPLACE_REQUEST,
        text="Replace rejected"
    )
    session.simulate_receive(cxl_rej)

    # State should revert from PENDING_REPLACE back to NEW
    assert session.order_book[ord_id]["status"] == OrdStatus.NEW


def test_parsing_error_cases():
    with pytest.raises(FixParseError):
        FixMessage.from_fix_str("")

    with pytest.raises(FixParseError):
        # Missing MsgType Tag 35
        FixMessage.from_fix_str("8=FIX.4.4\x0149=A\x0156=B\x0134=1\x0110=000\x01", validate_checksum=False)


def test_custom_tags_get_set():
    msg = FixMessage(FixMsgType.HEARTBEAT, "SENDER", "TARGET", 1)
    msg.set_tag("9999", "CUSTOM_VAL")
    assert msg.get_tag("9999") == "CUSTOM_VAL"
    assert msg.get_tag("8888", "DEFAULT") == "DEFAULT"

    raw = msg.to_fix_str()
    assert "9999=CUSTOM_VAL\x01" in raw
    parsed = FixMessage.from_fix_str(raw)
    assert parsed.get_tag("9999") == "CUSTOM_VAL"


@pytest.mark.anyio
async def test_async_event_callbacks():
    session = FixSession("CLIENT1", "EXCHANGE")
    received = []

    async def async_on_exec(report):
        await asyncio.sleep(0.01)
        received.append(report)

    session.register_callback("execution_report", async_on_exec)

    cl_ord_id = session.send_order("AMZN", Side.BUY, 20, 180.0)
    er = ExecutionReport("EXCHANGE", "CLIENT1", 1, "EX_A", cl_ord_id, "E1", ExecType.FILL, OrdStatus.FILLED, "AMZN", Side.BUY, 0, 20, 180.0)
    session.simulate_receive(er)

    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].cl_ord_id == cl_ord_id


def test_multi_venue_aggregator_venues_and_nbbo_synthesis():
    aggregator = MultiVenueAggregator()
    assert len(aggregator.venues) == 6
    expected_venues = {"CBOE", "MIAX", "BOX", "PHLX", "ARCA", "EDGX"}
    assert set(aggregator.venues.keys()) == expected_venues

    # Test fee schedules
    assert aggregator.venues["EDGX"].maker_fee == -0.40
    assert aggregator.venues["EDGX"].maker_rebate == 0.40
    assert aggregator.venues["BOX"].maker_fee == -0.35
    assert aggregator.venues["BOX"].taker_fee == 0.10
    assert aggregator.venues["CBOE"].taker_fee == 0.45

    # Test NBBO synthesis
    nbbo = aggregator.synthesize_nbbo("SPY", reference_price=500.0)
    assert nbbo["symbol"] == "SPY"
    assert nbbo["best_bid"] > 0
    assert nbbo["best_ask"] >= nbbo["best_bid"]
    assert nbbo["best_bid_venue"] in expected_venues
    assert nbbo["best_ask_venue"] in expected_venues
    assert " x " in nbbo["nbbo_string"]
    assert len(nbbo["venue_quotes"]) == 6


@pytest.mark.anyio
async def test_multi_venue_aggregator_routing_policies():
    aggregator = MultiVenueAggregator()

    # 1. SMART_SWEEP (Detailed)
    res_sweep = await aggregator.route_order(
        symbol="SPY",
        side=Side.BUY,
        qty=500.0,
        limit_price=500.0,
        routing_policy=RoutingPolicy.SMART_SWEEP,
        detailed=True,
    )
    assert isinstance(res_sweep, dict)
    assert res_sweep["symbol"] == "SPY"
    assert res_sweep["side"] == "BUY"
    assert res_sweep["quantity"] == 500.0
    assert res_sweep["total_filled_qty"] == 500.0
    assert res_sweep["status"] == "FILLED"
    assert len(res_sweep["fills"]) > 0
    assert len(res_sweep["fix_audit_log"]) == len(res_sweep["fills"])
    assert res_sweep["weighted_avg_price"] > 0
    assert res_sweep["avg_latency_ms"] > 0
    # First venue in SMART_SWEEP should be BOX (lowest taker fee 0.10)
    assert res_sweep["fills"][0]["venue"] == "BOX"

    # 2. FASTEST_VENUE (Detailed)
    res_fast = await aggregator.route_order(
        symbol="SPY",
        side=Side.BUY,
        qty=300.0,
        limit_price=500.0,
        routing_policy=RoutingPolicy.FASTEST_VENUE,
        detailed=True,
    )
    assert isinstance(res_fast, dict)
    assert res_fast["routing_policy"] == "FASTEST_VENUE"
    assert len(res_fast["fills"]) > 0
    # First venue in FASTEST_VENUE should be EDGX (0.6ms base latency)
    assert res_fast["fills"][0]["venue"] == "EDGX"

    # 3. MAX_REBATE (Detailed)
    res_rebate = await aggregator.route_order(
        symbol="SPY",
        side=Side.SELL,
        qty=400.0,
        limit_price=500.0,
        routing_policy=RoutingPolicy.MAX_REBATE,
        detailed=True,
    )
    assert isinstance(res_rebate, dict)
    assert res_rebate["routing_policy"] == "MAX_REBATE"
    assert len(res_rebate["fills"]) > 0
    # First venue in MAX_REBATE should be EDGX (highest rebate: -0.40)
    assert res_rebate["fills"][0]["venue"] == "EDGX"
    assert res_rebate["total_rebates"] > 0


def test_multi_venue_aggregator_get_venues_info():
    aggregator = MultiVenueAggregator()
    info = aggregator.get_venues_info("QQQ", spot_price=450.0)
    assert "venues" in info
    assert len(info["venues"]) == 6
    assert "supported_policies" in info
    assert "SMART_SWEEP" in info["supported_policies"]
    assert "FASTEST_VENUE" in info["supported_policies"]
    assert "MAX_REBATE" in info["supported_policies"]

    # Check simulated book depth
    for v in info["venues"]:
        assert "simulated_book_depth" in v
        bids = v["simulated_book_depth"]["bids"]
        asks = v["simulated_book_depth"]["asks"]
        assert len(bids) == 3
        assert len(asks) == 3
        assert bids[0]["price"] < asks[0]["price"]


