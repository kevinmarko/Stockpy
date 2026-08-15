import asyncio
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional

class MsgType(Enum):
    ResendRequest = '2'
    Reject = '3'
    SequenceReset = '4'
    NewOrderSingle = 'D'

class FixTag(Enum):
    MsgType = '35'
    MsgSeqNum = '34'
    BeginSeqNo = '7'
    EndSeqNo = '16'
    NewSeqNo = '36'
    GapFillFlag = '123'
    SenderCompID = '49'
    TargetCompID = '56'

@dataclass
class FixMessage:
    sender_comp_id: str = ""
    target_comp_id: str = ""
    msg_type: MsgType = MsgType.NewOrderSingle
    seq_num: int = 0
    tags: Dict[str, str] = field(default_factory=dict)
    
@dataclass
class ResendRequest(FixMessage):
    msg_type: MsgType = MsgType.ResendRequest
    begin_seq_no: int = 0
    end_seq_no: int = 0
    
    def __post_init__(self):
        self.tags = {
            FixTag.BeginSeqNo.value: str(self.begin_seq_no),
            FixTag.EndSeqNo.value: str(self.end_seq_no)
        }

@dataclass
class SequenceReset(FixMessage):
    msg_type: MsgType = MsgType.SequenceReset
    new_seq_no: int = 0
    gap_fill_flag: str = "N"
    
    def __post_init__(self):
        self.tags = {
            FixTag.NewSeqNo.value: str(self.new_seq_no),
            FixTag.GapFillFlag.value: self.gap_fill_flag
        }

class FixSessionRecovery:
    """
    Manages FIX sequence numbers and gap fill recovery.
    """
    def __init__(self, sender_comp_id: str, target_comp_id: str):
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.outbound_seq_num = 1
        self.inbound_seq_num = 1
        self.message_store: Dict[int, FixMessage] = {}
        
    def log_outbound_message(self, msg: FixMessage) -> None:
        msg.seq_num = self.outbound_seq_num
        self.message_store[self.outbound_seq_num] = msg
        self.outbound_seq_num += 1
        
    def handle_resend_request(self, request: ResendRequest) -> SequenceReset:
        """
        Generates a Sequence Reset in Gap Fill mode to respond to a Resend Request.
        """
        begin = request.begin_seq_no
        end = request.end_seq_no if request.end_seq_no > 0 else self.outbound_seq_num - 1
        
        # In this simulation, we simulate skipping administrative or aged messages
        # and issue a Gap Fill up to the end of the requested range.
        next_seq_no = end + 1
        
        reset_msg = SequenceReset(
            sender_comp_id=self.sender_comp_id,
            target_comp_id=self.target_comp_id,
            seq_num=begin, 
            new_seq_no=next_seq_no,
            gap_fill_flag="Y"
        )
        return reset_msg

    def process_sequence_reset(self, reset_msg: SequenceReset) -> bool:
        """
        Processes an incoming Sequence Reset. Returns True if successful,
        False if it attempts to illegally decrease the sequence number.
        """
        new_seq_str = reset_msg.tags.get(FixTag.NewSeqNo.value, "0")
        new_seq = int(new_seq_str)
        
        if new_seq < self.inbound_seq_num:
            # Sequence reset can only increase the sequence number.
            return False
            
        self.inbound_seq_num = new_seq
        return True
