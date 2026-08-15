import pytest
from execution.fix_recovery import (
    FixSessionRecovery,
    ResendRequest,
    SequenceReset,
    FixMessage,
    MsgType,
    FixTag
)

@pytest.fixture
def session():
    return FixSessionRecovery(sender_comp_id="STOCKPY_ALGO", target_comp_id="EXCHANGE_SIM")

def test_outbound_message_logging(session):
    msg = FixMessage(msg_type=MsgType.NewOrderSingle, sender_comp_id="STOCKPY_ALGO", target_comp_id="EXCHANGE_SIM")
    session.log_outbound_message(msg)
    
    assert session.outbound_seq_num == 2
    assert 1 in session.message_store
    assert session.message_store[1].msg_type == MsgType.NewOrderSingle

def test_handle_resend_request_gap_fill(session):
    # Simulate sending 5 messages
    for _ in range(5):
        session.log_outbound_message(FixMessage(msg_type=MsgType.NewOrderSingle, sender_comp_id="STOCKPY_ALGO", target_comp_id="EXCHANGE_SIM"))
        
    assert session.outbound_seq_num == 6
    
    # Exchange requests resend from sequence 2 to 4
    resend_req = ResendRequest(
        sender_comp_id="EXCHANGE_SIM",
        target_comp_id="STOCKPY_ALGO",
        begin_seq_no=2,
        end_seq_no=4
    )
    
    reset_response = session.handle_resend_request(resend_req)
    
    assert reset_response.msg_type == MsgType.SequenceReset
    assert reset_response.tags[FixTag.GapFillFlag.value] == "Y"
    assert reset_response.seq_num == 2
    assert reset_response.tags[FixTag.NewSeqNo.value] == "5" # End (4) + 1

def test_process_sequence_reset_increase_success(session):
    session.inbound_seq_num = 5
    
    reset_msg = SequenceReset(
        sender_comp_id="EXCHANGE_SIM",
        target_comp_id="STOCKPY_ALGO",
        new_seq_no=10,
        gap_fill_flag="Y"
    )
    
    success = session.process_sequence_reset(reset_msg)
    
    assert success is True
    assert session.inbound_seq_num == 10

def test_process_sequence_reset_decrease_failure(session):
    session.inbound_seq_num = 15
    
    # Attempting to decrease the sequence number is a serious error
    reset_msg = SequenceReset(
        sender_comp_id="EXCHANGE_SIM",
        target_comp_id="STOCKPY_ALGO",
        new_seq_no=10, 
        gap_fill_flag="N"
    )
    
    success = session.process_sequence_reset(reset_msg)
    
    assert success is False
    assert session.inbound_seq_num == 15 # Sequence should not change
