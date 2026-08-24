"""
FIX 4.4 Protocol Gateway & Cross-Exchange Routing Engine
Provides canonical FIX tag-value serialization/parsing, session state machine with gap recovery,
in-memory order lifecycle management, and multi-venue liquidity routing.

AST Safety: Strict (stdlib, numpy, pandas only, no heavy engine imports).
"""
import asyncio
import time
import datetime
import random
import uuid
import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple, Union, Callable, Awaitable, Set
import numpy as np

from settings import settings

logger = logging.getLogger(__name__)

# --- FIX Constants & Delimiters ---

SOH = "\x01"
SOH_BYTES = b"\x01"

# --- Session buffer bounds (memory-leak guard) ---
# Long-lived FixSession instances (the global singleton in particular) previously
# grew message_log/sent_messages/received_messages/gap_queue without bound across
# the process lifetime. Trim-on-append to the most recent N entries.
_MAX_MESSAGE_LOG_SIZE = 1000
_MAX_GAP_QUEUE_SIZE = 500

# --- FIX Data Types & Enums ---

class FixMsgType(str, Enum):
    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    LOGON = "A"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE = "G"

class OrdStatus(str, Enum):
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    DONE_FOR_DAY = "3"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    STOPPED = "7"
    REJECTED = "8"
    SUSPENDED = "9"
    PENDING_NEW = "A"
    CALCULATED = "B"
    EXPIRED = "C"
    ACCEPTED_FOR_BIDDING = "D"
    PENDING_REPLACE = "E"

class ExecType(str, Enum):
    NEW = "0"
    PARTIAL_FILL = "1"
    FILL = "2"
    DONE_FOR_DAY = "3"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    STOPPED = "7"
    REJECTED = "8"
    SUSPENDED = "9"
    PENDING_NEW = "A"
    CALCULATED = "B"
    EXPIRED = "C"
    RESTATED = "D"
    PENDING_REPLACE = "E"
    TRADE = "F"
    ORDER_STATUS = "I"

class Side(str, Enum):
    BUY = "1"
    SELL = "2"
    BUY_MINUS = "3"
    SELL_PLUS = "4"
    SELL_SHORT = "5"
    SELL_SHORT_EXEMPT = "6"

class OrdType(str, Enum):
    MARKET = "1"
    LIMIT = "2"
    STOP = "3"
    STOP_LIMIT = "4"

class TimeInForce(str, Enum):
    DAY = "0"
    GTC = "1"
    OPG = "2"
    IOC = "3"
    FOK = "4"
    GTX = "5"

class CxlRejResponseTo(str, Enum):
    ORDER_CANCEL_REQUEST = "1"
    ORDER_CANCEL_REPLACE_REQUEST = "2"

class CxlRejReason(str, Enum):
    TOO_LATE_TO_CANCEL = "0"
    UNKNOWN_ORDER = "1"
    BROKER_EXCHANGE_OPTION = "2"
    ORDER_ALREADY_IN_PENDING_STATUS = "3"
    UNABLE_TO_PROCESS = "4"
    ORIG_ORD_MOD_TIME_DID_NOT_MATCH = "5"
    DUPLICATE_CL_ORD_ID = "6"
    OTHER = "99"

class SessionRejectReason(str, Enum):
    INVALID_TAG_NUMBER = "0"
    REQUIRED_TAG_MISSING = "1"
    TAG_NOT_DEFINED_FOR_THIS_MESSAGE_TYPE = "2"
    UNDEFINED_TAG = "3"
    TAG_SPECIFIED_WITHOUT_A_VALUE = "4"
    VALUE_IS_INCORRECT = "5"
    INCORRECT_DATA_FORMAT_FOR_VALUE = "6"
    DECRYPTION_PROBLEM = "7"
    SIGNATURE_PROBLEM = "8"
    COMP_ID_PROBLEM = "9"
    SENDING_TIME_ACCURACY_PROBLEM = "10"
    INVALID_MSG_TYPE = "11"
    XML_VALIDATION_ERROR = "12"
    TAG_APPEARS_MORE_THAN_ONCE = "13"
    TAG_SPECIFIED_OUT_OF_REQUIRED_ORDER = "14"
    OTHER = "99"

class FixSessionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    LOGON_SENT = "LOGON_SENT"
    LOGON_RECEIVED = "LOGON_RECEIVED"
    ACTIVE = "ACTIVE"
    RESEND_REQUESTED = "RESEND_REQUESTED"
    GAP_FILL_PROCESSING = "GAP_FILL_PROCESSING"
    LOGOUT_SENT = "LOGOUT_SENT"
    SUSPENDED = "SUSPENDED"

    # Backward compatibility aliases
    CONNECTED = "ACTIVE"
    LOGGING_ON = "LOGON_SENT"
    LOGGING_OFF = "LOGOUT_SENT"
    RESEND_PROCESSING = "RESEND_REQUESTED"


# --- FIX Exceptions ---

class FixError(Exception):
    """Base FIX protocol exception."""
    pass

class FixParseError(FixError):
    """Raised when parsing a malformed FIX message."""
    pass

class FixChecksumError(FixParseError):
    """Raised when checksum verification fails."""
    pass

class FixSequenceError(FixError):
    """Raised on sequence number mismatch / violation."""
    pass


# --- Helper Functions ---

def compute_checksum(raw: Union[str, bytes]) -> str:
    """
    Compute FIX standard 3-digit modulo 256 checksum over byte representation.
    Calculates sum of all ASCII bytes up to 10=xxx\x01 formatted as %03d.
    """
    if isinstance(raw, str):
        raw_bytes = raw.encode("latin1")
    else:
        raw_bytes = raw
    return f"{sum(raw_bytes) % 256:03d}"

def format_fix_timestamp(dt: Optional[Union[datetime.datetime, float, int, str]] = None) -> str:
    """
    Format timestamp into standard FIX 4.4 UTC format (YYYYMMDD-HH:MM:SS.sss).
    """
    if dt is None:
        ts = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(dt, (int, float)):
        ts = datetime.datetime.fromtimestamp(dt, tz=datetime.timezone.utc)
    elif isinstance(dt, datetime.datetime):
        if dt.tzinfo is None:
            ts = dt.replace(tzinfo=datetime.timezone.utc)
        else:
            ts = dt.astimezone(datetime.timezone.utc)
    elif isinstance(dt, str):
        return dt
    else:
        ts = datetime.datetime.now(datetime.timezone.utc)
    return ts.strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


# --- Base FIX Message ---

class FixMessage:
    """
    Standard FIX 4.4 Protocol Message with canonical tag-value serialization and parsing.
    """
    def __init__(
        self,
        msg_type: Union[FixMsgType, str],
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        sending_time: Optional[Union[datetime.datetime, float, int, str]] = None,
        begin_string: str = "FIX.4.4",
    ):
        if isinstance(msg_type, str):
            try:
                self.msg_type: Union[FixMsgType, str] = FixMsgType(msg_type)
            except ValueError:
                self.msg_type = msg_type
        else:
            self.msg_type = msg_type

        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.seq_num = int(seq_num)
        self.sending_time = sending_time if sending_time is not None else time.time()
        self.begin_string = begin_string
        self.tags: Dict[str, Any] = {}

    @property
    def msg_type_val(self) -> str:
        return self.msg_type.value if isinstance(self.msg_type, Enum) else str(self.msg_type)

    def set_tag(self, tag: Union[str, int], value: Any) -> "FixMessage":
        self.tags[str(tag)] = value
        return self

    def get_tag(self, tag: Union[str, int], default: Any = None) -> Any:
        return self.tags.get(str(tag), default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary of FIX tags."""
        return {
            "35": self.msg_type_val,
            "49": self.sender_comp_id,
            "56": self.target_comp_id,
            "34": self.seq_num,
            "52": self.sending_time,
            **self.tags
        }

    def to_fix_str(self, delimiter: str = SOH) -> str:
        """
        Serialize message to canonical FIX tag-value format:
        8=FIX.4.4<SOH>9=<len><SOH>35=<MsgType><SOH>...<tags>...10=<chk><SOH>
        """
        st_val = format_fix_timestamp(self.sending_time) if isinstance(self.sending_time, (int, float)) else str(self.sending_time)

        # Standard header elements
        body_parts = [
            f"35={self.msg_type_val}",
            f"49={self.sender_comp_id}",
            f"56={self.target_comp_id}",
            f"34={self.seq_num}",
            f"52={st_val}",
        ]

        # Body tags (excluding reserved header/trailer tags)
        reserved = {"8", "9", "35", "49", "56", "34", "52", "10"}
        for k, v in self.tags.items():
            if str(k) not in reserved:
                val_str = v.value if isinstance(v, Enum) else str(v)
                body_parts.append(f"{k}={val_str}")

        body_str = delimiter.join(body_parts) + delimiter
        body_len = len(body_str.encode("latin1"))

        head_and_body = f"8={self.begin_string}{delimiter}9={body_len}{delimiter}{body_str}"
        chk = compute_checksum(head_and_body)
        return f"{head_and_body}10={chk}{delimiter}"

    @classmethod
    def from_fix_str(cls, raw: str, validate_checksum: bool = True) -> "FixMessage":
        """
        Parse raw FIX string into a concrete FixMessage (or appropriate subclass).
        Strictly verifies Tag 10 checksum up to 10=xxx<SOH>.
        """
        if not raw:
            raise FixParseError("Empty FIX message string")

        delimiter = SOH if SOH in raw else ("|" if "|" in raw else SOH)
        parts = [p for p in raw.split(delimiter) if p]

        if not parts:
            raise FixParseError("No valid tag-value pairs found in FIX string")

        tag_dict: Dict[str, str] = {}
        for part in parts:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k in tag_dict:
                logger.warning(
                    "FixMessage.from_fix_str: duplicate tag %s in message (MsgType=%s) -- "
                    "keeping last occurrence, discarding earlier value %r",
                    k, tag_dict.get("35", "?"), tag_dict.get(k),
                )
            tag_dict[k] = v

        # Validate Checksum (Tag 10) and BodyLength (Tag 9).
        if validate_checksum:
            # F1: a message with Tag 10 entirely missing (e.g. a transport-truncated
            # message that lost its trailing "10=xxx" field) must be treated as fatally
            # malformed, not silently accepted with integrity checking skipped.
            if "10" not in tag_dict:
                raise FixChecksumError(
                    "Missing required Tag 10 (CheckSum) -- message truncated or malformed"
                )
            expected_chk = tag_dict["10"]
            idx = raw.rfind(f"10={expected_chk}")
            if idx == -1:
                raise FixChecksumError(
                    f"Could not locate CheckSum field '10={expected_chk}' in raw message for verification"
                )
            prefix = raw[:idx]
            actual_chk = compute_checksum(prefix)
            # If delimiter was '|', also try checking with standard SOH delimiter replacement
            if actual_chk != expected_chk and delimiter == "|":
                actual_chk_soh = compute_checksum(prefix.replace("|", SOH))
                if actual_chk_soh == expected_chk:
                    actual_chk = expected_chk
            if actual_chk != expected_chk:
                raise FixChecksumError(f"Checksum mismatch: expected {expected_chk}, calculated {actual_chk}")

            # F2: independently verify BodyLength (Tag 9) against the actual body byte
            # count. CheckSum alone cannot catch a tampered BodyLength -- a doctored Tag 9
            # combined with a checksum recomputed over the (now-tampered) prefix is
            # internally self-consistent by construction, since the checksum is just a
            # byte-sum of whatever bytes are actually present, not a statement about what
            # Tag 9 claims. Real FIX engines use BodyLength as a primary framing/
            # corruption-detection mechanism independent of the checksum. Only verified
            # when Tag 9 is present -- this does not newly require it.
            if "9" in tag_dict:
                try:
                    expected_body_len = int(tag_dict["9"])
                except ValueError:
                    raise FixParseError(f"Malformed BodyLength in tag 9: {tag_dict.get('9')!r}")
                tag9_marker = f"9={tag_dict['9']}{delimiter}"
                body_start_idx = raw.find(tag9_marker)
                if body_start_idx == -1:
                    raise FixParseError(
                        "Could not locate Tag 9 (BodyLength) field boundary for verification"
                    )
                body_start = body_start_idx + len(tag9_marker)
                body_str = raw[body_start:idx]
                actual_body_len = len(body_str.encode("latin1"))
                if actual_body_len != expected_body_len:
                    raise FixParseError(
                        f"BodyLength mismatch: tag 9 declared {expected_body_len}, "
                        f"actual body is {actual_body_len} bytes"
                    )

        begin_str = tag_dict.get("8", "FIX.4.4")
        msg_type_str = tag_dict.get("35")
        if not msg_type_str:
            raise FixParseError("Missing required Tag 35 (MsgType)")

        sender = tag_dict.get("49", "")
        target = tag_dict.get("56", "")
        try:
            seq_num = int(tag_dict.get("34", "0"))
        except ValueError:
            raise FixParseError(
                f"Malformed sequence number in tag 34: {tag_dict.get('34')!r}"
            )
        sending_time = tag_dict.get("52", "")

        subclass_map = {
            FixMsgType.LOGON.value: Logon,
            FixMsgType.LOGOUT.value: Logout,
            FixMsgType.HEARTBEAT.value: Heartbeat,
            FixMsgType.TEST_REQUEST.value: TestRequest,
            FixMsgType.RESEND_REQUEST.value: ResendRequest,
            FixMsgType.SEQUENCE_RESET.value: SequenceReset,
            FixMsgType.REJECT.value: Reject,
            FixMsgType.NEW_ORDER_SINGLE.value: NewOrderSingle,
            FixMsgType.ORDER_CANCEL_REQUEST.value: OrderCancelRequest,
            FixMsgType.ORDER_CANCEL_REPLACE.value: OrderCancelReplace,
            FixMsgType.ORDER_CANCEL_REJECT.value: OrderCancelReject,
            FixMsgType.EXECUTION_REPORT.value: ExecutionReport,
        }

        msg_class = subclass_map.get(msg_type_str, FixMessage)
        if msg_class is FixMessage:
            msg = FixMessage(msg_type_str, sender, target, seq_num, sending_time=sending_time, begin_string=begin_str)
        else:
            msg = msg_class._from_tags(tag_dict, begin_str, sender, target, seq_num, sending_time)

        # Copy any extra/custom tags
        reserved = {"8", "9", "35", "49", "56", "34", "52", "10"}
        for k, v in tag_dict.items():
            if k not in reserved and k not in msg.tags:
                msg.tags[k] = v

        return msg

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "FixMessage":
        msg = cls(tags.get("35", "0"), sender, target, seq, sending_time=st, begin_string=begin_str)
        return msg

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} type={self.msg_type_val} seq={self.seq_num} {self.sender_comp_id}->{self.target_comp_id}>"


# --- Specific FIX 4.4 Message Types ---

class Logon(FixMessage):
    """FIX 4.4 Logon Message (MsgType = A)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        heartbeat_int: int = 30,
        encrypt_method: int = 0,
        reset_seq_num: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.LOGON, sender_comp_id, target_comp_id, seq_num, sending_time)
        self.tags["108"] = int(heartbeat_int)
        self.tags["98"] = int(encrypt_method)
        if reset_seq_num:
            self.tags["141"] = "Y"
        if username is not None:
            self.tags["553"] = str(username)
        if password is not None:
            self.tags["554"] = str(password)

    @property
    def heartbeat_int(self) -> int:
        return int(self.tags.get("108", 30))

    @property
    def reset_seq_num_flag(self) -> bool:
        return self.tags.get("141") == "Y"

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "Logon":
        msg = cls(
            sender_comp_id=sender,
            target_comp_id=target,
            seq_num=seq,
            heartbeat_int=int(tags.get("108", 30)),
            encrypt_method=int(tags.get("98", 0)),
            reset_seq_num=(tags.get("141") == "Y"),
            username=tags.get("553"),
            password=tags.get("554"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


class Logout(FixMessage):
    """FIX 4.4 Logout Message (MsgType = 5)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        text: Optional[str] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.LOGOUT, sender_comp_id, target_comp_id, seq_num, sending_time)
        if text is not None:
            self.tags["58"] = str(text)

    @property
    def text(self) -> Optional[str]:
        return self.tags.get("58")

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "Logout":
        msg = cls(sender, target, seq, text=tags.get("58"), sending_time=st)
        msg.begin_string = begin_str
        return msg


class Heartbeat(FixMessage):
    """FIX 4.4 Heartbeat Message (MsgType = 0)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        test_req_id: Optional[str] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.HEARTBEAT, sender_comp_id, target_comp_id, seq_num, sending_time)
        if test_req_id is not None:
            self.tags["112"] = str(test_req_id)

    @property
    def test_req_id(self) -> Optional[str]:
        return self.tags.get("112")

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "Heartbeat":
        msg = cls(sender, target, seq, test_req_id=tags.get("112"), sending_time=st)
        msg.begin_string = begin_str
        return msg


class TestRequest(FixMessage):
    """FIX 4.4 TestRequest Message (MsgType = 1)."""
    __test__ = False

    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        test_req_id: str,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.TEST_REQUEST, sender_comp_id, target_comp_id, seq_num, sending_time)
        self.tags["112"] = str(test_req_id)

    @property
    def test_req_id(self) -> str:
        return str(self.tags.get("112", ""))

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "TestRequest":
        msg = cls(sender, target, seq, test_req_id=tags.get("112", ""), sending_time=st)
        msg.begin_string = begin_str
        return msg


class ResendRequest(FixMessage):
    """FIX 4.4 ResendRequest Message (MsgType = 2)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        begin_seq_no: int,
        end_seq_no: int = 0,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.RESEND_REQUEST, sender_comp_id, target_comp_id, seq_num, sending_time)
        self.tags["7"] = int(begin_seq_no)
        self.tags["16"] = int(end_seq_no)

    @property
    def begin_seq_no(self) -> int:
        return int(self.tags.get("7", 0))

    @property
    def end_seq_no(self) -> int:
        return int(self.tags.get("16", 0))

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "ResendRequest":
        msg = cls(sender, target, seq, begin_seq_no=int(tags.get("7", 1)), end_seq_no=int(tags.get("16", 0)), sending_time=st)
        msg.begin_string = begin_str
        return msg


class SequenceReset(FixMessage):
    """FIX 4.4 SequenceReset Message (MsgType = 4)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        new_seq_no: int,
        gap_fill: bool = True,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.SEQUENCE_RESET, sender_comp_id, target_comp_id, seq_num, sending_time)
        self.tags["36"] = int(new_seq_no)
        self.tags["123"] = "Y" if gap_fill else "N"

    @property
    def new_seq_no(self) -> int:
        return int(self.tags.get("36", 0))

    @property
    def gap_fill(self) -> bool:
        return self.tags.get("123") == "Y"

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "SequenceReset":
        msg = cls(sender, target, seq, new_seq_no=int(tags.get("36", 1)), gap_fill=(tags.get("123") == "Y"), sending_time=st)
        msg.begin_string = begin_str
        return msg


class Reject(FixMessage):
    """FIX 4.4 Session Reject Message (MsgType = 3)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        ref_seq_num: int,
        ref_tag_id: Optional[Union[int, str]] = None,
        ref_msg_type: Optional[str] = None,
        session_reject_reason: Optional[Union[int, str, SessionRejectReason]] = None,
        text: Optional[str] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.REJECT, sender_comp_id, target_comp_id, seq_num, sending_time)
        self.tags["45"] = int(ref_seq_num)
        if ref_tag_id is not None:
            self.tags["371"] = str(ref_tag_id)
        if ref_msg_type is not None:
            self.tags["372"] = str(ref_msg_type)
        if session_reject_reason is not None:
            rej_val = session_reject_reason.value if isinstance(session_reject_reason, Enum) else str(session_reject_reason)
            self.tags["373"] = rej_val
        if text is not None:
            self.tags["58"] = str(text)

    @property
    def ref_seq_num(self) -> int:
        return int(self.tags.get("45", 0))

    @property
    def ref_tag_id(self) -> Optional[str]:
        return self.tags.get("371")

    @property
    def ref_msg_type(self) -> Optional[str]:
        return self.tags.get("372")

    @property
    def session_reject_reason(self) -> Optional[str]:
        return self.tags.get("373")

    @property
    def text(self) -> Optional[str]:
        return self.tags.get("58")

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "Reject":
        msg = cls(
            sender, target, seq,
            ref_seq_num=int(tags.get("45", 0)),
            ref_tag_id=tags.get("371"),
            ref_msg_type=tags.get("372"),
            session_reject_reason=tags.get("373"),
            text=tags.get("58"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


class NewOrderSingle(FixMessage):
    """FIX 4.4 NewOrderSingle Message (MsgType = D)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        cl_ord_id: str,
        symbol: str,
        side: Union[Side, str],
        order_qty: float,
        price: Optional[float] = None,
        ord_type: str = "2",
        time_in_force: Optional[Union[TimeInForce, str]] = None,
        account: Optional[str] = None,
        transact_time: Optional[Any] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.NEW_ORDER_SINGLE, sender_comp_id, target_comp_id, seq_num, sending_time)
        side_val = side.value if isinstance(side, Side) else str(side)
        self.tags.update({
            "11": str(cl_ord_id),
            "55": str(symbol),
            "54": side_val,
            "38": float(order_qty),
            "40": str(ord_type),
        })
        if price is not None:
            self.tags["44"] = float(price)
        if time_in_force is not None:
            tif_val = time_in_force.value if isinstance(time_in_force, Enum) else str(time_in_force)
            self.tags["59"] = tif_val
        if account is not None:
            self.tags["1"] = str(account)
        if transact_time is not None:
            self.tags["60"] = format_fix_timestamp(transact_time)

    @property
    def cl_ord_id(self) -> str:
        return str(self.tags.get("11", ""))

    @property
    def symbol(self) -> str:
        return str(self.tags.get("55", ""))

    @property
    def side(self) -> Side:
        val = self.tags.get("54", "1")
        return Side(val) if val in Side._value2member_map_ else Side.BUY

    @property
    def order_qty(self) -> float:
        return float(self.tags.get("38", 0.0))

    @property
    def price(self) -> Optional[float]:
        return float(self.tags["44"]) if "44" in self.tags else None

    @property
    def ord_type(self) -> str:
        return str(self.tags.get("40", "2"))

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "NewOrderSingle":
        side_val = tags.get("54", "1")
        side = Side(side_val) if side_val in Side._value2member_map_ else side_val
        price = float(tags["44"]) if "44" in tags else None
        msg = cls(
            sender_comp_id=sender,
            target_comp_id=target,
            seq_num=seq,
            cl_ord_id=tags.get("11", ""),
            symbol=tags.get("55", ""),
            side=side,
            order_qty=float(tags.get("38", 0.0)),
            price=price,
            ord_type=tags.get("40", "2"),
            time_in_force=tags.get("59"),
            account=tags.get("1"),
            transact_time=tags.get("60"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


class OrderCancelRequest(FixMessage):
    """FIX 4.4 OrderCancelRequest Message (MsgType = F)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        orig_cl_ord_id: str,
        cl_ord_id: str,
        symbol: str,
        side: Union[Side, str],
        order_qty: Optional[float] = None,
        transact_time: Optional[Any] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.ORDER_CANCEL_REQUEST, sender_comp_id, target_comp_id, seq_num, sending_time)
        side_val = side.value if isinstance(side, Side) else str(side)
        self.tags.update({
            "41": str(orig_cl_ord_id),
            "11": str(cl_ord_id),
            "55": str(symbol),
            "54": side_val,
        })
        if order_qty is not None:
            self.tags["38"] = float(order_qty)
        if transact_time is not None:
            self.tags["60"] = format_fix_timestamp(transact_time)

    @property
    def orig_cl_ord_id(self) -> str:
        return str(self.tags.get("41", ""))

    @property
    def cl_ord_id(self) -> str:
        return str(self.tags.get("11", ""))

    @property
    def symbol(self) -> str:
        return str(self.tags.get("55", ""))

    @property
    def side(self) -> Side:
        val = self.tags.get("54", "1")
        return Side(val) if val in Side._value2member_map_ else Side.BUY

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "OrderCancelRequest":
        side_val = tags.get("54", "1")
        side = Side(side_val) if side_val in Side._value2member_map_ else side_val
        order_qty = float(tags["38"]) if "38" in tags else None
        msg = cls(
            sender_comp_id=sender,
            target_comp_id=target,
            seq_num=seq,
            orig_cl_ord_id=tags.get("41", ""),
            cl_ord_id=tags.get("11", ""),
            symbol=tags.get("55", ""),
            side=side,
            order_qty=order_qty,
            transact_time=tags.get("60"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


class OrderCancelReplace(FixMessage):
    """FIX 4.4 OrderCancelReplace Message (MsgType = G)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        orig_cl_ord_id: str,
        cl_ord_id: str,
        symbol: str,
        side: Union[Side, str],
        order_qty: float,
        price: float,
        ord_type: str = "2",
        transact_time: Optional[Any] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.ORDER_CANCEL_REPLACE, sender_comp_id, target_comp_id, seq_num, sending_time)
        side_val = side.value if isinstance(side, Side) else str(side)
        self.tags.update({
            "41": str(orig_cl_ord_id),
            "11": str(cl_ord_id),
            "55": str(symbol),
            "54": side_val,
            "38": float(order_qty),
            "44": float(price),
            "40": str(ord_type),
        })
        if transact_time is not None:
            self.tags["60"] = format_fix_timestamp(transact_time)

    @property
    def orig_cl_ord_id(self) -> str:
        return str(self.tags.get("41", ""))

    @property
    def cl_ord_id(self) -> str:
        return str(self.tags.get("11", ""))

    @property
    def symbol(self) -> str:
        return str(self.tags.get("55", ""))

    @property
    def side(self) -> Side:
        val = self.tags.get("54", "1")
        return Side(val) if val in Side._value2member_map_ else Side.BUY

    @property
    def order_qty(self) -> float:
        return float(self.tags.get("38", 0.0))

    @property
    def price(self) -> float:
        return float(self.tags.get("44", 0.0))

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "OrderCancelReplace":
        side_val = tags.get("54", "1")
        side = Side(side_val) if side_val in Side._value2member_map_ else side_val
        msg = cls(
            sender_comp_id=sender,
            target_comp_id=target,
            seq_num=seq,
            orig_cl_ord_id=tags.get("41", ""),
            cl_ord_id=tags.get("11", ""),
            symbol=tags.get("55", ""),
            side=side,
            order_qty=float(tags.get("38", 0.0)),
            price=float(tags.get("44", 0.0)),
            ord_type=tags.get("40", "2"),
            transact_time=tags.get("60"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


class OrderCancelReject(FixMessage):
    """FIX 4.4 OrderCancelReject Message (MsgType = 9)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        order_id: str,
        cl_ord_id: str,
        orig_cl_ord_id: str,
        ord_status: Union[OrdStatus, str],
        cxl_rej_response_to: Union[CxlRejResponseTo, str] = "1",
        cxl_rej_reason: Optional[Union[CxlRejReason, str, int]] = None,
        text: Optional[str] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.ORDER_CANCEL_REJECT, sender_comp_id, target_comp_id, seq_num, sending_time)
        status_val = ord_status.value if isinstance(ord_status, OrdStatus) else str(ord_status)
        resp_val = cxl_rej_response_to.value if isinstance(cxl_rej_response_to, Enum) else str(cxl_rej_response_to)
        self.tags.update({
            "37": str(order_id),
            "11": str(cl_ord_id),
            "41": str(orig_cl_ord_id),
            "39": status_val,
            "434": resp_val,
        })
        if cxl_rej_reason is not None:
            rej_val = cxl_rej_reason.value if isinstance(cxl_rej_reason, Enum) else str(cxl_rej_reason)
            self.tags["102"] = rej_val
        if text is not None:
            self.tags["58"] = str(text)

    @property
    def order_id(self) -> str:
        return str(self.tags.get("37", ""))

    @property
    def cl_ord_id(self) -> str:
        return str(self.tags.get("11", ""))

    @property
    def orig_cl_ord_id(self) -> str:
        return str(self.tags.get("41", ""))

    @property
    def ord_status(self) -> OrdStatus:
        val = self.tags.get("39", "0")
        return OrdStatus(val) if val in OrdStatus._value2member_map_ else OrdStatus.NEW

    @property
    def cxl_rej_response_to(self) -> str:
        return str(self.tags.get("434", "1"))

    @property
    def cxl_rej_reason(self) -> Optional[str]:
        return self.tags.get("102")

    @property
    def text(self) -> Optional[str]:
        return self.tags.get("58")

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "OrderCancelReject":
        status_val = tags.get("39", "0")
        ord_status = OrdStatus(status_val) if status_val in OrdStatus._value2member_map_ else status_val
        msg = cls(
            sender_comp_id=sender,
            target_comp_id=target,
            seq_num=seq,
            order_id=tags.get("37", "NONE"),
            cl_ord_id=tags.get("11", ""),
            orig_cl_ord_id=tags.get("41", ""),
            ord_status=ord_status,
            cxl_rej_response_to=tags.get("434", "1"),
            cxl_rej_reason=tags.get("102"),
            text=tags.get("58"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


class ExecutionReport(FixMessage):
    """FIX 4.4 ExecutionReport Message (MsgType = 8)."""
    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        order_id: str,
        cl_ord_id: str,
        exec_id: str,
        exec_type: Union[ExecType, OrdStatus, str],
        ord_status: Union[OrdStatus, str],
        symbol: str,
        side: Union[Side, str],
        leaves_qty: float,
        cum_qty: float,
        avg_px: float,
        orig_cl_ord_id: Optional[str] = None,
        order_qty: Optional[float] = None,
        price: Optional[float] = None,
        last_px: Optional[float] = None,
        last_qty: Optional[float] = None,
        ord_rej_reason: Optional[Union[int, str]] = None,
        text: Optional[str] = None,
        transact_time: Optional[Any] = None,
        sending_time: Optional[Any] = None,
    ):
        super().__init__(FixMsgType.EXECUTION_REPORT, sender_comp_id, target_comp_id, seq_num, sending_time)
        exec_val = exec_type.value if isinstance(exec_type, Enum) else str(exec_type)
        status_val = ord_status.value if isinstance(ord_status, Enum) else str(ord_status)
        side_val = side.value if isinstance(side, Side) else str(side)
        self.tags.update({
            "37": str(order_id),
            "11": str(cl_ord_id),
            "17": str(exec_id),
            "150": exec_val,
            "39": status_val,
            "55": str(symbol),
            "54": side_val,
            "151": float(leaves_qty),
            "14": float(cum_qty),
            "6": float(avg_px),
        })
        if orig_cl_ord_id is not None:
            self.tags["41"] = str(orig_cl_ord_id)
        if order_qty is not None:
            self.tags["38"] = float(order_qty)
        if price is not None:
            self.tags["44"] = float(price)
        if last_px is not None:
            self.tags["31"] = float(last_px)
        if last_qty is not None:
            self.tags["32"] = float(last_qty)
        if ord_rej_reason is not None:
            self.tags["103"] = str(ord_rej_reason)
        if text is not None:
            self.tags["58"] = str(text)
        if transact_time is not None:
            self.tags["60"] = format_fix_timestamp(transact_time)

    @property
    def order_id(self) -> str:
        return str(self.tags.get("37", ""))

    @property
    def cl_ord_id(self) -> str:
        return str(self.tags.get("11", ""))

    @property
    def orig_cl_ord_id(self) -> Optional[str]:
        return self.tags.get("41")

    @property
    def exec_id(self) -> str:
        return str(self.tags.get("17", ""))

    @property
    def exec_type(self) -> ExecType:
        val = self.tags.get("150", "0")
        return ExecType(val) if val in ExecType._value2member_map_ else ExecType.NEW

    @property
    def ord_status(self) -> OrdStatus:
        val = self.tags.get("39", "0")
        return OrdStatus(val) if val in OrdStatus._value2member_map_ else OrdStatus.NEW

    @property
    def symbol(self) -> str:
        return str(self.tags.get("55", ""))

    @property
    def side(self) -> Side:
        val = self.tags.get("54", "1")
        return Side(val) if val in Side._value2member_map_ else Side.BUY

    @property
    def leaves_qty(self) -> float:
        return float(self.tags.get("151", 0.0))

    @property
    def cum_qty(self) -> float:
        return float(self.tags.get("14", 0.0))

    @property
    def avg_px(self) -> float:
        return float(self.tags.get("6", 0.0))

    @property
    def last_px(self) -> Optional[float]:
        return float(self.tags["31"]) if "31" in self.tags else None

    @property
    def last_qty(self) -> Optional[float]:
        return float(self.tags["32"]) if "32" in self.tags else None

    @property
    def text(self) -> Optional[str]:
        return self.tags.get("58")

    @classmethod
    def _from_tags(cls, tags: Dict[str, str], begin_str: str, sender: str, target: str, seq: int, st: Any) -> "ExecutionReport":
        exec_val = tags.get("150", "0")
        exec_type = ExecType(exec_val) if exec_val in ExecType._value2member_map_ else exec_val
        status_val = tags.get("39", "0")
        ord_status = OrdStatus(status_val) if status_val in OrdStatus._value2member_map_ else status_val
        side_val = tags.get("54", "1")
        side = Side(side_val) if side_val in Side._value2member_map_ else side_val
        msg = cls(
            sender_comp_id=sender,
            target_comp_id=target,
            seq_num=seq,
            order_id=tags.get("37", ""),
            cl_ord_id=tags.get("11", ""),
            exec_id=tags.get("17", ""),
            exec_type=exec_type,
            ord_status=ord_status,
            symbol=tags.get("55", ""),
            side=side,
            leaves_qty=float(tags.get("151", 0.0)),
            cum_qty=float(tags.get("14", 0.0)),
            avg_px=float(tags.get("6", 0.0)),
            orig_cl_ord_id=tags.get("41"),
            order_qty=float(tags["38"]) if "38" in tags else None,
            price=float(tags["44"]) if "44" in tags else None,
            last_px=float(tags["31"]) if "31" in tags else None,
            last_qty=float(tags["32"]) if "32" in tags else None,
            ord_rej_reason=tags.get("103"),
            text=tags.get("58"),
            transact_time=tags.get("60"),
            sending_time=st,
        )
        msg.begin_string = begin_str
        return msg


# --- FIX Session State Machine & Recovery ---

# Known-legitimate state transitions, enumerated from every `self.state = X`
# assignment this module's own code actually performs (connect/disconnect/
# simulate_receive/_process_message_payload/_drain_gap_queue/restore_state).
# Consulted by FixSession._set_state() to WARN (never block) on a transition this
# table doesn't recognize -- a visibility fix, not an enforcement fix, since this
# pass didn't attempt to fully re-derive every edge case the current code handles.
_VALID_TRANSITIONS: Dict["FixSessionState", Set["FixSessionState"]] = {
    FixSessionState.DISCONNECTED: {
        FixSessionState.LOGON_SENT, FixSessionState.ACTIVE, FixSessionState.DISCONNECTED,
        # disconnect() unconditionally sets LOGOUT_SENT regardless of current state
        # (e.g. calling it on an already-disconnected/never-connected session), and
        # simulate_receive()'s gap detection can fire before any connect() ever
        # happened (e.g. a raw inbound message hitting a fresh session in tests).
        FixSessionState.LOGOUT_SENT, FixSessionState.RESEND_REQUESTED,
    },
    FixSessionState.CONNECTING: {
        FixSessionState.ACTIVE, FixSessionState.LOGON_SENT,
    },
    FixSessionState.LOGON_SENT: {
        FixSessionState.ACTIVE, FixSessionState.LOGON_SENT,
        FixSessionState.DISCONNECTED, FixSessionState.LOGOUT_SENT,
    },
    FixSessionState.LOGON_RECEIVED: {
        FixSessionState.ACTIVE,
    },
    FixSessionState.ACTIVE: {
        FixSessionState.RESEND_REQUESTED, FixSessionState.GAP_FILL_PROCESSING,
        FixSessionState.LOGOUT_SENT, FixSessionState.DISCONNECTED,
        FixSessionState.LOGON_SENT, FixSessionState.ACTIVE,
    },
    FixSessionState.RESEND_REQUESTED: {
        FixSessionState.GAP_FILL_PROCESSING, FixSessionState.ACTIVE,
        FixSessionState.RESEND_REQUESTED, FixSessionState.DISCONNECTED,
        FixSessionState.LOGOUT_SENT, FixSessionState.LOGON_SENT,
    },
    FixSessionState.GAP_FILL_PROCESSING: {
        FixSessionState.ACTIVE, FixSessionState.RESEND_REQUESTED,
        FixSessionState.DISCONNECTED, FixSessionState.LOGOUT_SENT,
        FixSessionState.LOGON_SENT,
    },
    FixSessionState.LOGOUT_SENT: {
        FixSessionState.DISCONNECTED,
    },
    FixSessionState.SUSPENDED: {
        FixSessionState.LOGON_SENT, FixSessionState.ACTIVE, FixSessionState.DISCONNECTED,
    },
}


class FixSession:
    """
    FIX 4.4 Institutional Session State Machine with Resilient Recovery.
    Features:
    - Accurate sequence number tracking (in_seq_num, out_seq_num).
    - Sequence gap detection: transitions to RESEND_REQUESTED, emits ResendRequest (35=2),
      buffers out-of-order messages in gap_queue.
    - Gap-Fill processing: transitions to GAP_FILL_PROCESSING, fast-forwards sequence,
      drains contiguous buffered messages from gap_queue, and returns to ACTIVE.
    - Peer resend handler: replays sent application messages with PossDupFlag="Y" and
      substitutes administrative messages with SequenceReset-GapFill.
    - Heartbeat & TestRequest watchdog for idle heartbeat generation and inactivity recovery.
    - Atomic session state serialization and persistence to output/fix_session_state.json.
    - Lifecycle event callbacks and in-memory order tracking.
    """
    def __init__(self, sender_comp_id: str, target_comp_id: str, heartbeat_int: int = 30):
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.heartbeat_int = int(heartbeat_int)
        self.session_id = f"{sender_comp_id}->{target_comp_id}"
        self._set_state(FixSessionState.DISCONNECTED)
        self.connected_at: Optional[float] = None

        self._in_seq_num = 1
        self._out_seq_num = 1
        self._last_received_time: float = time.time()
        self._last_sent_time: float = time.time()
        self.pending_resend_range: Optional[Tuple[int, int]] = None

        self.message_log: List[Dict[str, Any]] = []
        self.sent_messages: List[FixMessage] = []
        self.received_messages: List[FixMessage] = []
        self.order_book: Dict[str, Dict[str, Any]] = {}
        self._incoming_buffer: Dict[int, FixMessage] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # Callbacks
        self.on_execution_report: Optional[Callable[[ExecutionReport], Any]] = None
        self.on_reject: Optional[Callable[[Reject], Any]] = None
        self.on_cancel_reject: Optional[Callable[[OrderCancelReject], Any]] = None
        self.on_logon: Optional[Callable[[Logon], Any]] = None
        self.on_logout: Optional[Callable[[Logout], Any]] = None
        self.on_message: Optional[Callable[[FixMessage], Any]] = None
        self.on_heartbeat: Optional[Callable[[Heartbeat], Any]] = None

    # Properties for sequence numbers, gap queue, and timestamps
    @property
    def in_seq_num(self) -> int:
        return self._in_seq_num

    @in_seq_num.setter
    def in_seq_num(self, val: int) -> None:
        self._in_seq_num = int(val)

    @property
    def out_seq_num(self) -> int:
        return self._out_seq_num

    @out_seq_num.setter
    def out_seq_num(self, val: int) -> None:
        self._out_seq_num = int(val)

    @property
    def inbound_seq_num(self) -> int:
        return self._in_seq_num

    @inbound_seq_num.setter
    def inbound_seq_num(self, val: int) -> None:
        self._in_seq_num = int(val)

    @property
    def outbound_seq_num(self) -> int:
        return self._out_seq_num

    @outbound_seq_num.setter
    def outbound_seq_num(self, val: int) -> None:
        self._out_seq_num = int(val)

    @property
    def gap_queue(self) -> Dict[int, FixMessage]:
        return self._incoming_buffer

    @gap_queue.setter
    def gap_queue(self, val: Dict[int, FixMessage]) -> None:
        self._incoming_buffer = val

    @property
    def last_heard_at(self) -> float:
        return self._last_received_time

    @last_heard_at.setter
    def last_heard_at(self, val: float) -> None:
        self._last_received_time = float(val)

    @property
    def last_sent_at(self) -> float:
        return self._last_sent_time

    @last_sent_at.setter
    def last_sent_at(self, val: float) -> None:
        self._last_sent_time = float(val)

    def register_callback(self, event_name: str, callback: Callable) -> None:
        """Register a callback for session events."""
        event_lower = event_name.lower().strip()
        if event_lower in {"execution_report", "on_execution_report"}:
            self.on_execution_report = callback
        elif event_lower in {"reject", "on_reject"}:
            self.on_reject = callback
        elif event_lower in {"cancel_reject", "order_cancel_reject", "on_cancel_reject"}:
            self.on_cancel_reject = callback
        elif event_lower in {"logon", "on_logon"}:
            self.on_logon = callback
        elif event_lower in {"logout", "on_logout"}:
            self.on_logout = callback
        elif event_lower in {"message", "on_message"}:
            self.on_message = callback
        elif event_lower in {"heartbeat", "on_heartbeat"}:
            self.on_heartbeat = callback
        else:
            raise ValueError(f"Unknown event callback: {event_name}")

    def _invoke_callback(self, cb: Optional[Callable], *args: Any, **kwargs: Any) -> None:
        if cb is None:
            return
        try:
            res = cb(*args, **kwargs)
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    pass
        except Exception:
            pass

    def _set_state(self, new_state: "FixSessionState") -> None:
        """Set self.state, logging a WARNING (never raising or blocking) when the
        transition from the current state isn't one this module's own code is known
        to perform (see _VALID_TRANSITIONS above). This is a visibility fix, not an
        enforcement fix -- an unrecognized transition is still applied as requested.
        """
        old_state = getattr(self, "state", None)
        if old_state is not None and old_state != new_state:
            allowed = _VALID_TRANSITIONS.get(old_state, set())
            if new_state not in allowed:
                logger.warning(
                    "FixSession %s: unexpected state transition %s -> %s",
                    getattr(self, "session_id", "?"), old_state, new_state,
                )
        self.state = new_state

    async def connect(self, reset_seq: bool = False):
        """Initiate logon sequence and activate heartbeat loop."""
        async with self._lock:
            self._set_state(FixSessionState.LOGON_SENT)
            self.connected_at = time.time()
            await asyncio.sleep(0.01)
            logon_msg = Logon(
                self.sender_comp_id,
                self.target_comp_id,
                self.out_seq_num,
                heartbeat_int=self.heartbeat_int,
                reset_seq_num=reset_seq,
            )
            self._send(logon_msg)
            self._set_state(FixSessionState.ACTIVE)
            if self._heartbeat_task is not None and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self, text: Optional[str] = None):
        """Initiate logout sequence and cancel heartbeat task."""
        async with self._lock:
            self._set_state(FixSessionState.LOGOUT_SENT)
            logout_msg = Logout(self.sender_comp_id, self.target_comp_id, self.out_seq_num, text=text)
            self._send(logout_msg)
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
                self._heartbeat_task = None
            self._set_state(FixSessionState.DISCONNECTED)

    async def _heartbeat_loop(self):
        """Background heartbeat and inactivity watchdog generator loop."""
        try:
            while self.state in {FixSessionState.ACTIVE, FixSessionState.RESEND_REQUESTED, FixSessionState.GAP_FILL_PROCESSING}:
                sleep_interval = max(0.2, min(1.0, self.heartbeat_int / 2.0))
                await asyncio.sleep(sleep_interval)
                now = time.time()
                # Idle Heartbeat emission
                if now - self.last_sent_at >= self.heartbeat_int:
                    if self.state in {FixSessionState.ACTIVE, FixSessionState.RESEND_REQUESTED, FixSessionState.GAP_FILL_PROCESSING}:
                        hb = Heartbeat(self.sender_comp_id, self.target_comp_id, self.out_seq_num)
                        self._send(hb)
                # Inactivity TestRequest emission
                if now - self.last_heard_at >= self.heartbeat_int * 1.5:
                    if self.state in {FixSessionState.ACTIVE, FixSessionState.RESEND_REQUESTED, FixSessionState.GAP_FILL_PROCESSING}:
                        self.send_test_request()
        except asyncio.CancelledError:
            pass

    def check_watchdog(self, now: Optional[float] = None) -> List[FixMessage]:
        """
        Synchronously check watchdog timers and emit necessary Heartbeats or TestRequests.
        """
        cur_time = now if now is not None else time.time()
        emitted: List[FixMessage] = []
        
        if self.state in {FixSessionState.ACTIVE, FixSessionState.RESEND_REQUESTED, FixSessionState.GAP_FILL_PROCESSING}:
            if cur_time - self.last_heard_at >= self.heartbeat_int * 1.5:
                tid = f"TEST-{uuid.uuid4().hex[:8]}"
                msg = TestRequest(self.sender_comp_id, self.target_comp_id, self.out_seq_num, test_req_id=tid)
                self._send(msg)
                emitted.append(msg)
            elif cur_time - self.last_sent_at >= self.heartbeat_int:
                hb = Heartbeat(self.sender_comp_id, self.target_comp_id, self.out_seq_num)
                self._send(hb)
                emitted.append(hb)
                
        return emitted

    def _send(self, msg: FixMessage) -> FixMessage:
        """Send message, updating outbound sequence number and message log."""
        msg.seq_num = self.out_seq_num
        self.message_log.append(msg.to_dict())
        if len(self.message_log) > _MAX_MESSAGE_LOG_SIZE:
            del self.message_log[: len(self.message_log) - _MAX_MESSAGE_LOG_SIZE]
        self.sent_messages.append(msg)
        if len(self.sent_messages) > _MAX_MESSAGE_LOG_SIZE:
            del self.sent_messages[: len(self.sent_messages) - _MAX_MESSAGE_LOG_SIZE]
        self.out_seq_num += 1
        self.last_sent_at = time.time()
        return msg

    def send_order(
        self,
        symbol: str,
        side: Union[Side, str],
        qty: float,
        price: float,
        ord_type: str = "2",
        time_in_force: Optional[str] = "0",
        cl_ord_id: Optional[str] = None,
    ) -> str:
        """
        Send a NewOrderSingle (MsgType=D) and track order in order book.
        """
        if cl_ord_id is None:
            cl_ord_id = str(uuid.uuid4())

        side_enum = Side(side) if isinstance(side, str) and side in Side._value2member_map_ else side
        msg = NewOrderSingle(
            self.sender_comp_id,
            self.target_comp_id,
            self.out_seq_num,
            cl_ord_id=cl_ord_id,
            symbol=symbol,
            side=side_enum,
            order_qty=qty,
            price=price,
            ord_type=ord_type,
            time_in_force=time_in_force,
        )
        self._send(msg)
        self.order_book[cl_ord_id] = {
            "cl_ord_id": cl_ord_id,
            "orig_cl_ord_id": None,
            "order_id": None,
            "symbol": symbol,
            "side": side_enum,
            "qty": float(qty),
            "price": float(price),
            "ord_type": ord_type,
            "time_in_force": time_in_force,
            "status": OrdStatus.NEW,
            "filled": 0.0,
            "cum_qty": 0.0,
            "leaves_qty": float(qty),
            "avg_px": 0.0,
            "history": [msg.to_dict()],
        }
        return cl_ord_id

    def cancel_order(
        self,
        orig_cl_ord_id: str,
        symbol: Optional[str] = None,
        side: Optional[Union[Side, str]] = None,
        order_qty: Optional[float] = None,
        cl_ord_id: Optional[str] = None,
    ) -> str:
        """
        Send an OrderCancelRequest (MsgType=F) and transition order to PENDING_CANCEL.
        """
        if cl_ord_id is None:
            cl_ord_id = f"CXL-{uuid.uuid4().hex[:8]}"

        existing = self.order_book.get(orig_cl_ord_id, {})
        sym = symbol or existing.get("symbol", "")
        s = side or existing.get("side", Side.BUY)
        qty = order_qty if order_qty is not None else existing.get("leaves_qty", existing.get("qty", 0.0))

        side_enum = Side(s) if isinstance(s, str) and s in Side._value2member_map_ else s
        msg = OrderCancelRequest(
            self.sender_comp_id,
            self.target_comp_id,
            self.out_seq_num,
            orig_cl_ord_id=orig_cl_ord_id,
            cl_ord_id=cl_ord_id,
            symbol=sym,
            side=side_enum,
            order_qty=qty,
        )
        self._send(msg)
        if orig_cl_ord_id in self.order_book:
            self.order_book[orig_cl_ord_id]["status"] = OrdStatus.PENDING_CANCEL
            self.order_book[orig_cl_ord_id]["pending_cancel_id"] = cl_ord_id
            self.order_book[cl_ord_id] = {
                **self.order_book[orig_cl_ord_id],
                "cl_ord_id": cl_ord_id,
                "orig_cl_ord_id": orig_cl_ord_id,
                "status": OrdStatus.PENDING_CANCEL,
            }
        return cl_ord_id

    def replace_order(
        self,
        orig_cl_ord_id: str,
        symbol: Optional[str] = None,
        side: Optional[Union[Side, str]] = None,
        new_qty: Optional[float] = None,
        new_price: Optional[float] = None,
        ord_type: str = "2",
        cl_ord_id: Optional[str] = None,
    ) -> str:
        """
        Send an OrderCancelReplace (MsgType=G) and transition order to PENDING_REPLACE.
        """
        if cl_ord_id is None:
            cl_ord_id = f"RPL-{uuid.uuid4().hex[:8]}"

        existing = self.order_book.get(orig_cl_ord_id, {})
        sym = symbol or existing.get("symbol", "")
        s = side or existing.get("side", Side.BUY)
        qty = new_qty if new_qty is not None else existing.get("qty", 0.0)
        price = new_price if new_price is not None else existing.get("price", 0.0)

        side_enum = Side(s) if isinstance(s, str) and s in Side._value2member_map_ else s
        msg = OrderCancelReplace(
            self.sender_comp_id,
            self.target_comp_id,
            self.out_seq_num,
            orig_cl_ord_id=orig_cl_ord_id,
            cl_ord_id=cl_ord_id,
            symbol=sym,
            side=side_enum,
            order_qty=qty,
            price=price,
            ord_type=ord_type,
        )
        self._send(msg)
        if orig_cl_ord_id in self.order_book:
            self.order_book[orig_cl_ord_id]["status"] = OrdStatus.PENDING_REPLACE
            self.order_book[orig_cl_ord_id]["pending_replace_id"] = cl_ord_id
            # Create link for new cl_ord_id
            self.order_book[cl_ord_id] = {
                **self.order_book[orig_cl_ord_id],
                "cl_ord_id": cl_ord_id,
                "orig_cl_ord_id": orig_cl_ord_id,
                "qty": float(qty),
                "price": float(price),
                "status": OrdStatus.PENDING_REPLACE,
            }
        return cl_ord_id

    def send_test_request(self, test_req_id: Optional[str] = None) -> str:
        """Send a TestRequest (MsgType=1)."""
        tid = test_req_id or f"TEST-{uuid.uuid4().hex[:6]}"
        msg = TestRequest(self.sender_comp_id, self.target_comp_id, self.out_seq_num, test_req_id=tid)
        self._send(msg)
        return tid

    def send_heartbeat(self, test_req_id: Optional[str] = None) -> Heartbeat:
        """Send a Heartbeat (MsgType=0)."""
        hb = Heartbeat(self.sender_comp_id, self.target_comp_id, self.out_seq_num, test_req_id=test_req_id)
        self._send(hb)
        return hb

    def send_resend_request(self, begin_seq_no: int, end_seq_no: int = 0) -> ResendRequest:
        """Send a ResendRequest (MsgType=2)."""
        msg = ResendRequest(self.sender_comp_id, self.target_comp_id, self.out_seq_num, begin_seq_no, end_seq_no)
        self._send(msg)
        return msg

    def send_sequence_reset(self, new_seq_no: int, gap_fill: bool = True) -> SequenceReset:
        """Send a SequenceReset (MsgType=4)."""
        msg = SequenceReset(self.sender_comp_id, self.target_comp_id, self.out_seq_num, new_seq_no, gap_fill=gap_fill)
        self._send(msg)
        return msg

    def simulate_receive(self, raw_or_msg_or_dict: Union[str, Dict[str, Any], FixMessage]) -> Optional[FixMessage]:
        """
        Process an incoming FIX message (string, dict, or FixMessage object)
        through sequence gap validation, gap fill processor, and the session state machine.
        """
        if isinstance(raw_or_msg_or_dict, str):
            msg = FixMessage.from_fix_str(raw_or_msg_or_dict)
        elif isinstance(raw_or_msg_or_dict, dict):
            # Reconstruct from dict
            tags_copy = {str(k): str(v) for k, v in raw_or_msg_or_dict.items()}
            msg_type_str = tags_copy.get("35", "0")
            sender = tags_copy.get("49", self.target_comp_id)
            target = tags_copy.get("56", self.sender_comp_id)
            seq_num = int(tags_copy.get("34", "0"))
            st = tags_copy.get("52", time.time())
            begin_str = tags_copy.get("8", "FIX.4.4")

            subclass_map = {
                FixMsgType.LOGON.value: Logon,
                FixMsgType.LOGOUT.value: Logout,
                FixMsgType.HEARTBEAT.value: Heartbeat,
                FixMsgType.TEST_REQUEST.value: TestRequest,
                FixMsgType.RESEND_REQUEST.value: ResendRequest,
                FixMsgType.SEQUENCE_RESET.value: SequenceReset,
                FixMsgType.REJECT.value: Reject,
                FixMsgType.NEW_ORDER_SINGLE.value: NewOrderSingle,
                FixMsgType.ORDER_CANCEL_REQUEST.value: OrderCancelRequest,
                FixMsgType.ORDER_CANCEL_REPLACE.value: OrderCancelReplace,
                FixMsgType.ORDER_CANCEL_REJECT.value: OrderCancelReject,
                FixMsgType.EXECUTION_REPORT.value: ExecutionReport,
            }
            msg_class = subclass_map.get(msg_type_str, FixMessage)
            if msg_class is FixMessage:
                msg = FixMessage(msg_type_str, sender, target, seq_num, sending_time=st, begin_string=begin_str)
                msg.tags.update(tags_copy)
            else:
                msg = msg_class._from_tags(tags_copy, begin_str, sender, target, seq_num, st)
                for k, v in tags_copy.items():
                    if k not in {"8", "9", "35", "49", "56", "34", "52", "10"} and k not in msg.tags:
                        msg.tags[k] = v
        else:
            msg = raw_or_msg_or_dict

        self.last_heard_at = time.time()
        self.received_messages.append(msg)
        if len(self.received_messages) > _MAX_MESSAGE_LOG_SIZE:
            del self.received_messages[: len(self.received_messages) - _MAX_MESSAGE_LOG_SIZE]
        seq = msg.seq_num

        # Sequence number handling & gap detection
        if seq == 0:
            # Special bypass for seq 0 simulation
            self._process_message_payload(msg)
            return msg

        # FIX 4.4: SequenceReset (Reset mode, GapFill != Y) unconditionally resets sequence number
        if msg.msg_type_val == FixMsgType.SEQUENCE_RESET.value and msg.tags.get("123") != "Y":
            self._process_message_payload(msg)
            self._drain_gap_queue()
            return msg

        # Sequence Gap Detected! (Incoming MsgSeqNum > expected in_seq_num)
        if seq > self.in_seq_num:
            if seq not in self.gap_queue and len(self.gap_queue) >= _MAX_GAP_QUEUE_SIZE:
                oldest_seq = min(self.gap_queue.keys())
                del self.gap_queue[oldest_seq]
                logger.warning(
                    "FixSession %s: gap_queue exceeded max size %d, dropping oldest buffered seq %d",
                    self.session_id, _MAX_GAP_QUEUE_SIZE, oldest_seq,
                )
            self.gap_queue[seq] = msg
            self._set_state(FixSessionState.RESEND_REQUESTED)
            self.pending_resend_range = (self.in_seq_num, seq - 1)
            # Send ResendRequest for the missing gap [in_seq_num, 0]
            self.send_resend_request(begin_seq_no=self.in_seq_num, end_seq_no=0)
            return msg

        # Outdated sequence number (seq < in_seq_num)
        if seq < self.in_seq_num:
            # Check if PossDupFlag is set
            is_poss_dup = msg.tags.get("43") == "Y"
            if is_poss_dup:
                logging.debug(f"Ignored poss dup message seq {seq} < {self.in_seq_num}")
                return msg
            # Otherwise ignore outdated sequence number without advancing
            return msg

        # Expected sequence number (seq == self.in_seq_num)
        if msg.msg_type_val == FixMsgType.SEQUENCE_RESET.value and msg.tags.get("123") == "Y":
            self._set_state(FixSessionState.GAP_FILL_PROCESSING)
            self._process_message_payload(msg)
        else:
            self._process_message_payload(msg)
            self.in_seq_num += 1

        # Drain contiguous buffered messages from gap_queue
        self._drain_gap_queue()
        return msg

    def _drain_gap_queue(self) -> None:
        """Drain contiguous buffered messages from gap_queue in sequence."""
        while self.in_seq_num in self.gap_queue:
            buffered_msg = self.gap_queue.pop(self.in_seq_num)
            self._process_message_payload(buffered_msg)
            if buffered_msg.msg_type_val != FixMsgType.SEQUENCE_RESET.value:
                self.in_seq_num += 1

        if not self.gap_queue and self.state in {FixSessionState.RESEND_REQUESTED, FixSessionState.GAP_FILL_PROCESSING}:
            self._set_state(FixSessionState.ACTIVE)
            self.pending_resend_range = None

    def _process_message_payload(self, msg: FixMessage) -> None:
        """Process internal FIX state transition for a message."""
        msg_type = msg.msg_type_val

        if msg_type == FixMsgType.LOGON.value:
            if isinstance(msg, Logon) and msg.reset_seq_num_flag:
                self.in_seq_num = 1
                self.out_seq_num = 1
            if self.state in {FixSessionState.DISCONNECTED, FixSessionState.LOGON_SENT, FixSessionState.CONNECTING}:
                self._set_state(FixSessionState.ACTIVE)
            self._invoke_callback(self.on_logon, msg)

        elif msg_type == FixMsgType.LOGOUT.value:
            self._set_state(FixSessionState.DISCONNECTED)
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                self._heartbeat_task = None
            self._invoke_callback(self.on_logout, msg)

        elif msg_type == FixMsgType.HEARTBEAT.value:
            self._invoke_callback(self.on_heartbeat, msg)

        elif msg_type == FixMsgType.TEST_REQUEST.value:
            # Immediately respond with Heartbeat containing the TestReqID
            test_req_id = msg.tags.get("112")
            hb = Heartbeat(self.sender_comp_id, self.target_comp_id, self.out_seq_num, test_req_id=test_req_id)
            self._send(hb)

        elif msg_type == FixMsgType.RESEND_REQUEST.value:
            # Process peer's ResendRequest
            begin_seq = int(msg.tags.get("7", 1))
            end_seq = int(msg.tags.get("16", 0))
            if end_seq == 0 or end_seq >= self.out_seq_num:
                end_seq = self.out_seq_num - 1

            for s_msg in self.sent_messages:
                if begin_seq <= s_msg.seq_num <= end_seq:
                    # If administrative message, send SequenceReset GapFill
                    admin_types = {
                        FixMsgType.HEARTBEAT.value, FixMsgType.TEST_REQUEST.value,
                        FixMsgType.LOGON.value, FixMsgType.LOGOUT.value,
                        FixMsgType.RESEND_REQUEST.value, FixMsgType.SEQUENCE_RESET.value
                    }
                    if s_msg.msg_type_val in admin_types:
                        gap_msg = SequenceReset(
                            self.sender_comp_id, self.target_comp_id,
                            s_msg.seq_num, new_seq_no=s_msg.seq_num + 1, gap_fill=True
                        )
                        gap_msg.tags["43"] = "Y"
                        self.message_log.append(gap_msg.to_dict())
                    else:
                        # Resend application message with PossDupFlag="Y"
                        s_msg.tags["43"] = "Y"
                        s_msg.tags["122"] = format_fix_timestamp(s_msg.sending_time)
                        self.message_log.append(s_msg.to_dict())

        elif msg_type == FixMsgType.SEQUENCE_RESET.value:
            new_seq = int(msg.tags.get("36", self.in_seq_num))
            gap_fill = msg.tags.get("123") == "Y"
            if gap_fill:
                if new_seq >= self.in_seq_num:
                    self.in_seq_num = new_seq
            else:
                self.in_seq_num = new_seq

        elif msg_type == FixMsgType.REJECT.value:
            self._invoke_callback(self.on_reject, msg)

        elif msg_type == FixMsgType.ORDER_CANCEL_REJECT.value:
            orig_cl_ord_id = msg.tags.get("41")
            cl_ord_id = msg.tags.get("11")
            if orig_cl_ord_id and orig_cl_ord_id in self.order_book:
                order_rec = self.order_book[orig_cl_ord_id]
                # Revert pending status to active status
                status_in_msg = msg.tags.get("39")
                if status_in_msg:
                    order_rec["status"] = OrdStatus(status_in_msg) if status_in_msg in OrdStatus._value2member_map_ else status_in_msg
                elif order_rec.get("filled", 0.0) > 0:
                    order_rec["status"] = OrdStatus.PARTIALLY_FILLED
                else:
                    order_rec["status"] = OrdStatus.NEW
            if cl_ord_id and cl_ord_id in self.order_book:
                # For cancel_order, cl_ord_id shares the same dict as orig_cl_ord_id. 
                # For replace_order, it's a new dict. We only mark REJECTED if it's a separate dict.
                if orig_cl_ord_id not in self.order_book or id(self.order_book[cl_ord_id]) != id(self.order_book[orig_cl_ord_id]):
                    self.order_book[cl_ord_id]["status"] = OrdStatus.REJECTED
            self._invoke_callback(self.on_cancel_reject, msg)

        elif msg_type == FixMsgType.EXECUTION_REPORT.value:
            cl_ord_id = msg.tags.get("11", "")
            orig_cl_ord_id = msg.tags.get("41")
            status_val = msg.tags.get("39")
            ord_status = OrdStatus(status_val) if status_val in OrdStatus._value2member_map_ else status_val
            cum_qty = float(msg.tags.get("14", 0.0))
            leaves_qty = float(msg.tags.get("151", 0.0))
            avg_px = float(msg.tags.get("6", 0.0))
            order_id = msg.tags.get("37")

            target_id = cl_ord_id if cl_ord_id in self.order_book else (orig_cl_ord_id if orig_cl_ord_id and orig_cl_ord_id in self.order_book else None)

            if target_id:
                order_rec = self.order_book[target_id]
                order_rec["status"] = ord_status
                order_rec["filled"] = cum_qty
                order_rec["cum_qty"] = cum_qty
                order_rec["leaves_qty"] = leaves_qty
                order_rec["avg_px"] = avg_px
                if order_id:
                    order_rec["order_id"] = order_id
                if ord_status in {OrdStatus.REPLACED, OrdStatus.CANCELED, OrdStatus.REJECTED}:
                    if "38" in msg.tags:
                        order_rec["qty"] = float(msg.tags["38"])
                    if "44" in msg.tags:
                        order_rec["price"] = float(msg.tags["44"])
                    self.order_book[cl_ord_id] = order_rec
                    if orig_cl_ord_id:
                        self.order_book[orig_cl_ord_id] = order_rec
                        self.order_book[orig_cl_ord_id]["filled"] = cum_qty
                order_rec.setdefault("history", []).append(msg.to_dict())
            else:
                self.order_book[cl_ord_id] = {
                    "cl_ord_id": cl_ord_id,
                    "orig_cl_ord_id": orig_cl_ord_id,
                    "order_id": order_id,
                    "symbol": msg.tags.get("55", ""),
                    "side": msg.tags.get("54", "1"),
                    "qty": float(msg.tags.get("38", cum_qty + leaves_qty)),
                    "price": float(msg.tags.get("44", avg_px)),
                    "status": ord_status,
                    "filled": cum_qty,
                    "cum_qty": cum_qty,
                    "leaves_qty": leaves_qty,
                    "avg_px": avg_px,
                    "history": [msg.to_dict()],
                }

            self._invoke_callback(self.on_execution_report, msg)

        # General on_message callback
        self._invoke_callback(self.on_message, msg)

    def _is_unacknowledged(self, msg: FixMessage) -> bool:
        """Check if message represents an unacknowledged order request."""
        if msg.msg_type_val == FixMsgType.NEW_ORDER_SINGLE.value:
            cl_ord_id = msg.tags.get("11")
            if cl_ord_id and cl_ord_id in self.order_book:
                status = self.order_book[cl_ord_id].get("status")
                return status in {OrdStatus.NEW, OrdStatus.PENDING_NEW, OrdStatus.PARTIALLY_FILLED}
        return False

    def _serialize_order_rec(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize order record to JSON-safe dictionary."""
        out = {}
        for k, v in rec.items():
            if isinstance(v, Enum):
                out[k] = v.value
            elif k == "history" and isinstance(v, list):
                out[k] = [
                    {hk: (hv.value if isinstance(hv, Enum) else hv) for hk, hv in h.items()}
                    if isinstance(h, dict) else h
                    for h in v
                ]
            else:
                out[k] = v
        return out

    def persist_state(self, filepath: str = "output/fix_session_state.json") -> Dict[str, Any]:
        """
        Atomically serialize session state (in_seq_num, out_seq_num, session_id,
        state, last_heard_at, pending_resend_range, unacknowledged orders) to disk.
        """
        import os
        import json

        abs_path = os.path.abspath(filepath)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        state_dict = {
            "session_id": self.session_id,
            "sender_comp_id": self.sender_comp_id,
            "target_comp_id": self.target_comp_id,
            "state": self.state.value if isinstance(self.state, Enum) else str(self.state),
            "in_seq_num": self.in_seq_num,
            "out_seq_num": self.out_seq_num,
            "last_heard_at": self.last_heard_at,
            "last_sent_at": self.last_sent_at,
            "pending_resend_range": list(self.pending_resend_range) if self.pending_resend_range else None,
            "heartbeat_int": self.heartbeat_int,
            "unacknowledged_messages": [m.to_dict() for m in self.sent_messages if self._is_unacknowledged(m)],
            "order_book": {k: self._serialize_order_rec(v) for k, v in self.order_book.items()},
            "updated_at": format_fix_timestamp(time.time()),
        }

        tmp_path = f"{abs_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2)
        os.replace(tmp_path, abs_path)
        return state_dict

    def restore_state(self, filepath: str = "output/fix_session_state.json") -> bool:
        """
        Restore sequence numbers, session state, timestamps, and order book from disk.
        """
        import os
        import json

        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            return False

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                state_dict = json.load(f)

            self.session_id = state_dict.get("session_id", self.session_id)
            self.sender_comp_id = state_dict.get("sender_comp_id", self.sender_comp_id)
            self.target_comp_id = state_dict.get("target_comp_id", self.target_comp_id)
            
            st_val = state_dict.get("state", FixSessionState.DISCONNECTED.value)
            try:
                self._set_state(FixSessionState(st_val))
            except ValueError:
                self._set_state(FixSessionState.DISCONNECTED)

            self.in_seq_num = int(state_dict.get("in_seq_num", 1))
            self.out_seq_num = int(state_dict.get("out_seq_num", 1))
            self.last_heard_at = float(state_dict.get("last_heard_at", time.time()))
            self.last_sent_at = float(state_dict.get("last_sent_at", time.time()))
            
            pr = state_dict.get("pending_resend_range")
            self.pending_resend_range = tuple(pr) if pr else None
            self.heartbeat_int = int(state_dict.get("heartbeat_int", self.heartbeat_int))

            if "order_book" in state_dict:
                for k, v in state_dict["order_book"].items():
                    if isinstance(v, dict):
                        if "status" in v and isinstance(v["status"], str) and v["status"] in OrdStatus._value2member_map_:
                            v["status"] = OrdStatus(v["status"])
                        if "side" in v and isinstance(v["side"], str) and v["side"] in Side._value2member_map_:
                            v["side"] = Side(v["side"])
                    self.order_book[k] = v

            return True
        except Exception as e:
            logging.error(f"Failed to restore FIX session state from {filepath}: {e}")
            return False


class FixSessionManager:
    """
    Institutional FIX 4.4 Session Manager.
    Manages multi-session lifecycles, persistence, gap recovery, and session lookup.
    """
    def __init__(self, state_dir: str = "output"):
        self.state_dir = state_dir
        self.sessions: Dict[str, FixSession] = {}
        self._lock = asyncio.Lock()

    def get_or_create_session(
        self,
        sender_comp_id: str,
        target_comp_id: str,
        heartbeat_int: int = 30,
        auto_restore: bool = True
    ) -> FixSession:
        """Retrieve existing session or instantiate a new one."""
        import os

        session_id = f"{sender_comp_id}->{target_comp_id}"
        if session_id in self.sessions:
            return self.sessions[session_id]

        session = FixSession(sender_comp_id, target_comp_id, heartbeat_int=heartbeat_int)
        if auto_restore:
            state_file = os.path.join(self.state_dir, f"fix_session_{sender_comp_id}_{target_comp_id}.json")
            if os.path.exists(state_file):
                session.restore_state(state_file)
            else:
                global_file = os.path.join(self.state_dir, "fix_session_state.json")
                if os.path.exists(global_file):
                    session.restore_state(global_file)

        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[FixSession]:
        """Lookup session by session ID (SenderCompID->TargetCompID)."""
        return self.sessions.get(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List active sessions and their metadata."""
        return [
            {
                "session_id": s.session_id,
                "sender_comp_id": s.sender_comp_id,
                "target_comp_id": s.target_comp_id,
                "state": s.state.value if isinstance(s.state, Enum) else str(s.state),
                "in_seq_num": s.in_seq_num,
                "out_seq_num": s.out_seq_num,
                "last_heard_at": s.last_heard_at,
                "active_orders": len(s.order_book),
            }
            for s in self.sessions.values()
        ]

    def persist_all(self, filepath: Optional[str] = None) -> None:
        """Persist all managed sessions to disk."""
        import os

        for s in self.sessions.values():
            if filepath:
                s.persist_state(filepath)
            else:
                path = os.path.join(self.state_dir, f"fix_session_{s.sender_comp_id}_{s.target_comp_id}.json")
                s.persist_state(path)
        if self.sessions:
            first_session = next(iter(self.sessions.values()))
            default_path = filepath or os.path.join(self.state_dir, "fix_session_state.json")
            first_session.persist_state(default_path)

    def restore_all(self, filepath: Optional[str] = None) -> int:
        """Restore all sessions from individual or global JSON files."""
        import os
        import json

        restored = 0
        if filepath and os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            sender = data.get("sender_comp_id", "CLIENT")
            target = data.get("target_comp_id", "SERVER")
            session = self.get_or_create_session(sender, target, auto_restore=False)
            if session.restore_state(filepath):
                restored += 1
            return restored

        if os.path.exists(self.state_dir):
            for fname in os.listdir(self.state_dir):
                if fname.startswith("fix_session_") and fname.endswith(".json") and not fname.endswith(".tmp"):
                    fpath = os.path.join(self.state_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        sender = data.get("sender_comp_id")
                        target = data.get("target_comp_id")
                        if sender and target:
                            session = self.get_or_create_session(sender, target, auto_restore=False)
                            if session.restore_state(fpath):
                                restored += 1
                    except Exception as exc:
                        logger.warning(
                            "FixSessionManager.restore_all: failed to restore session from %s: %s",
                            fname, exc,
                        )
        return restored

    async def close_all(self) -> None:
        """Gracefully disconnect all sessions."""
        for s in self.sessions.values():
            if s.state != FixSessionState.DISCONNECTED:
                await s.disconnect()


# --- Multi-Venue Aggregator & Smart Order Router (SOR) ---

class RoutingPolicy(str, Enum):
    SMART_SWEEP = "SMART_SWEEP"
    FASTEST_VENUE = "FASTEST_VENUE"
    MAX_REBATE = "MAX_REBATE"


class VenueConfig:
    """
    Configuration and liquidity profile for an execution venue.
    Includes Maker-Taker fee schedules (positive for fees, negative for maker rebates),
    base network latency, and average top-of-book depth.
    """
    def __init__(
        self,
        name: str,
        base_latency_ms: float,
        liquidity_depth: float,
        fee_per_contract: float,
        maker_fee: float = 0.0,
        taker_fee: Optional[float] = None,
        maker_rebate: float = 0.0,
        quote_spread_cents: float = 2.0,
    ):
        self.name = name
        self.base_latency_ms = float(base_latency_ms)
        self.liquidity_depth = float(liquidity_depth)
        self.fee_per_contract = float(fee_per_contract)
        self.maker_fee = float(maker_fee)
        self.taker_fee = float(taker_fee if taker_fee is not None else fee_per_contract)
        self.maker_rebate = float(maker_rebate if maker_rebate != 0.0 else (-maker_fee if maker_fee < 0 else 0.0))
        self.quote_spread_cents = float(quote_spread_cents)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "venue": self.name,
            "base_latency_ms": self.base_latency_ms,
            "liquidity_depth": self.liquidity_depth,
            "fee_per_contract": self.fee_per_contract,
            "taker_fee": self.taker_fee,
            "maker_fee": self.maker_fee,
            "maker_rebate": self.maker_rebate,
            "quote_spread_cents": self.quote_spread_cents,
        }


class MultiVenueAggregator:
    """
    Simulates cross-exchange Smart Order Routing (SOR) and liquidity aggregation
    across major option & equity venues: CBOE, MIAX, BOX, PHLX, ARCA, EDGX.

    Features:
    - Maker-Taker fee schedules with positive fees and negative rebates.
    - Multi-venue NBBO synthesis.
    - Routing policies:
      * SMART_SWEEP (cost & price optimal: sweeps venues by lowest net cost/taker fee)
      * FASTEST_VENUE (latency optimal: routes to lowest latency venue)
      * MAX_REBATE (liquidity maker rebate optimal: routes to highest rebate venue)
    - Simulated adverse selection, price improvement, latency jitter, and partial fill mechanics.
    - FIX 4.4 audit log generation (ExecutionReports for each routed child slice).
    """
    def __init__(self, venues: Optional[Dict[str, VenueConfig]] = None):
        if venues is not None:
            self.venues = venues
        else:
            self.venues = {
                "CBOE": VenueConfig("CBOE", base_latency_ms=1.2, liquidity_depth=1000.0, fee_per_contract=0.45, maker_fee=0.15, taker_fee=0.45),
                "MIAX": VenueConfig("MIAX", base_latency_ms=0.8, liquidity_depth=500.0, fee_per_contract=0.25, maker_fee=-0.20, taker_fee=0.25, maker_rebate=0.20),
                "BOX":  VenueConfig("BOX",  base_latency_ms=2.5, liquidity_depth=300.0, fee_per_contract=0.10, maker_fee=-0.35, taker_fee=0.10, maker_rebate=0.35),
                "PHLX": VenueConfig("PHLX", base_latency_ms=1.5, liquidity_depth=800.0, fee_per_contract=0.40, maker_fee=0.20, taker_fee=0.40),
                "ARCA": VenueConfig("ARCA", base_latency_ms=1.0, liquidity_depth=700.0, fee_per_contract=0.35, maker_fee=-0.25, taker_fee=0.35, maker_rebate=0.25),
                "EDGX": VenueConfig("EDGX", base_latency_ms=0.6, liquidity_depth=450.0, fee_per_contract=0.30, maker_fee=-0.40, taker_fee=0.30, maker_rebate=0.40),
            }

    def synthesize_nbbo(self, symbol: str, reference_price: float = 100.0) -> Dict[str, Any]:
        """
        Synthesizes the National Best Bid and Offer (NBBO) across all active venues.
        """
        ref_px = float(reference_price if reference_price and reference_price > 0 else 100.0)
        venue_quotes: Dict[str, Dict[str, Any]] = {}

        for name, cfg in self.venues.items():
            spread_offset = round(random.uniform(0.01, 0.04), 2)
            bid = round(ref_px - spread_offset, 2)
            ask = round(ref_px + spread_offset, 2)
            bid_size = max(10.0, round(float(np.random.normal(cfg.liquidity_depth, cfg.liquidity_depth * 0.15)), 0))
            ask_size = max(10.0, round(float(np.random.normal(cfg.liquidity_depth, cfg.liquidity_depth * 0.15)), 0))
            lat = round(cfg.base_latency_ms + random.uniform(0.05, 0.35), 3)

            venue_quotes[name] = {
                "venue": name,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "latency_ms": lat,
            }

        best_bid = max(q["bid"] for q in venue_quotes.values())
        best_bid_venue = next(k for k, v in venue_quotes.items() if v["bid"] == best_bid)
        best_ask = min(q["ask"] for q in venue_quotes.values())
        best_ask_venue = next(k for k, v in venue_quotes.items() if v["ask"] == best_ask)
        spread = round(best_ask - best_bid, 4)
        mid_price = round((best_bid + best_ask) / 2.0, 4)

        return {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_bid_venue": best_bid_venue,
            "best_ask": best_ask,
            "best_ask_venue": best_ask_venue,
            "spread": spread,
            "mid_price": mid_price,
            "nbbo_string": f"{best_bid:.2f} @ {best_bid_venue} x {best_ask:.2f} @ {best_ask_venue}",
            "venue_quotes": venue_quotes,
        }

    def get_venues_info(self, symbol: Optional[str] = "SPY", spot_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Returns full profiles of all supported venues, including latency profiles,
        fee/rebate schedules, and simulated 3-level LOB book depth.
        """
        ref_px = float(spot_price if spot_price and spot_price > 0 else 100.0)
        venue_list = []

        for name, cfg in self.venues.items():
            # 3-level simulated LOB depth
            bids = [
                {"level": 1, "price": round(ref_px - 0.01, 2), "size": round(cfg.liquidity_depth * 0.40, 1)},
                {"level": 2, "price": round(ref_px - 0.02, 2), "size": round(cfg.liquidity_depth * 0.35, 1)},
                {"level": 3, "price": round(ref_px - 0.03, 2), "size": round(cfg.liquidity_depth * 0.25, 1)},
            ]
            asks = [
                {"level": 1, "price": round(ref_px + 0.01, 2), "size": round(cfg.liquidity_depth * 0.40, 1)},
                {"level": 2, "price": round(ref_px + 0.02, 2), "size": round(cfg.liquidity_depth * 0.35, 1)},
                {"level": 3, "price": round(ref_px + 0.03, 2), "size": round(cfg.liquidity_depth * 0.25, 1)},
            ]

            venue_dict = cfg.to_dict()
            venue_dict["simulated_book_depth"] = {"bids": bids, "asks": asks}
            venue_list.append(venue_dict)

        return {
            "venues": venue_list,
            "supported_policies": [p.value for p in RoutingPolicy],
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    async def route_order(
        self,
        symbol: str,
        side: Union[Side, str],
        qty: float,
        limit_price: float,
        routing_policy: Union[RoutingPolicy, str] = RoutingPolicy.SMART_SWEEP,
        detailed: bool = False,
        cl_ord_id: Optional[str] = None,
        sender_comp_id: str = "SOR_CLIENT",
        target_comp_id: str = "FIX_GATEWAY",
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Smart Order Router execution logic:
        1. Query simulated venue quotes and available depth.
        2. Order venues based on the chosen RoutingPolicy:
           - SMART_SWEEP: Sort by lowest taker fee, then lowest latency.
           - FASTEST_VENUE: Sort by lowest latency, then lowest taker fee.
           - MAX_REBATE: Sort by most negative maker fee (highest rebate), then lowest latency.
        3. Sweep venues sequentially with latency jitter, partial fill mechanics,
           and simulated adverse selection / price improvement.
        4. Emit FIX 4.4 ExecutionReports and audit trail.
        """
        # Normalize side
        if isinstance(side, Side):
            side_enum = side
            side_str = "BUY" if side in {Side.BUY, Side.BUY_MINUS} else "SELL"
        else:
            s_raw = str(side).upper().strip()
            if s_raw in {"1", "BUY", "BUY_MINUS"}:
                side_enum = Side.BUY
                side_str = "BUY"
            elif s_raw in {"2", "SELL", "SELL_PLUS", "SELL_SHORT", "SELL_SHORT_EXEMPT"}:
                side_enum = Side.SELL
                side_str = "SELL"
            else:
                side_enum = Side.BUY
                side_str = "BUY"

        is_buy = (side_str == "BUY")

        # Normalize routing policy
        if isinstance(routing_policy, RoutingPolicy):
            policy_str = routing_policy.value
        else:
            pol_raw = str(routing_policy).upper().strip() if routing_policy else "SMART_SWEEP"
            if pol_raw in {p.value for p in RoutingPolicy}:
                policy_str = pol_raw
            else:
                policy_str = RoutingPolicy.SMART_SWEEP.value

        # Collect quotes across venues
        quotes = []
        for name, config in self.venues.items():
            latency = config.base_latency_ms + random.uniform(0.05, 0.45)
            avail_qty = max(0.0, float(np.random.normal(config.liquidity_depth, config.liquidity_depth * 0.2)))
            quotes.append({
                "venue": name,
                "config": config,
                "latency_ms": latency,
                "avail_qty": avail_qty,
                "fee": config.fee_per_contract,
                "taker_fee": config.taker_fee,
                "maker_fee": config.maker_fee,
                "maker_rebate": config.maker_rebate,
            })

        # Sort according to policy
        if policy_str == RoutingPolicy.FASTEST_VENUE.value:
            quotes.sort(key=lambda x: (x["config"].base_latency_ms, x["taker_fee"]))
        elif policy_str == RoutingPolicy.MAX_REBATE.value:
            quotes.sort(key=lambda x: (x["maker_fee"], x["latency_ms"]))
        else:  # SMART_SWEEP
            quotes.sort(key=lambda x: (x["taker_fee"], x["latency_ms"]))

        remaining_qty = float(qty)
        total_filled = 0.0
        fills: List[Dict[str, Any]] = []
        fix_audit_log: List[str] = []

        if cl_ord_id is None:
            cl_ord_id = f"SOR-{uuid.uuid4().hex[:8]}"

        for q in quotes:
            if remaining_qty <= 0:
                break

            await asyncio.sleep(q["latency_ms"] / 1000.0)

            fill_qty = min(remaining_qty, q["avail_qty"])
            if fill_qty <= 0:
                continue

            # Price improvement / adverse selection simulation
            fill_price = float(limit_price)
            if q["latency_ms"] < 1.0 and fill_qty < 0.30 * q["config"].liquidity_depth:
                # Fast venue + small order: potential price improvement ($0.01)
                fill_price = round(limit_price - 0.01, 4) if is_buy else round(limit_price + 0.01, 4)
            elif q["latency_ms"] > 2.2 or fill_qty > 0.85 * q["config"].liquidity_depth:
                # Slower venue or deep book sweep: limit price fill without improvement
                fill_price = float(limit_price)

            # Calculate fees & rebates
            fee_rate = q["maker_fee"] if policy_str == RoutingPolicy.MAX_REBATE.value else q["taker_fee"]
            fee_amount = round(fill_qty * fee_rate, 4)
            rebate_amount = round(max(0.0, -fee_amount), 4) if fee_rate < 0 else 0.0
            net_fee = round(fee_amount if fee_rate >= 0 else -rebate_amount, 4)

            total_filled += fill_qty
            remaining_qty -= fill_qty
            leaves_qty = max(0.0, remaining_qty)

            exec_status = OrdStatus.FILLED if leaves_qty == 0 else OrdStatus.PARTIALLY_FILLED
            exec_type = ExecType.FILL if leaves_qty == 0 else ExecType.PARTIAL_FILL
            exec_id = f"EXEC-{q['venue']}-{uuid.uuid4().hex[:8]}"
            order_id = f"ORD-{q['venue']}-{uuid.uuid4().hex[:6]}"

            current_vwap = round(
                (sum(f["fill_qty"] * f["fill_price"] for f in fills) + fill_qty * fill_price) / total_filled, 4
            )

            # Generate canonical FIX 4.4 ExecutionReport
            er = ExecutionReport(
                sender_comp_id=target_comp_id,
                target_comp_id=sender_comp_id,
                seq_num=len(fills) + 1,
                order_id=order_id,
                cl_ord_id=cl_ord_id,
                exec_id=exec_id,
                exec_type=exec_type,
                ord_status=exec_status,
                symbol=symbol,
                side=side_enum,
                leaves_qty=leaves_qty,
                cum_qty=total_filled,
                avg_px=current_vwap,
                last_px=fill_price,
                last_qty=fill_qty,
                text=f"Venue: {q['venue']} | Policy: {policy_str} | Latency: {q['latency_ms']:.2f}ms | NetFee: ${net_fee:.2f}",
            )
            er.tags["30"] = q["venue"]
            er.tags["12"] = str(net_fee)
            raw_fix_str = er.to_fix_str()
            fix_audit_log.append(raw_fix_str)

            fills.append({
                "venue": q["venue"],
                "fill_qty": fill_qty,
                "fill_price": fill_price,
                "fee": net_fee,
                "rebate": rebate_amount,
                "latency_ms": round(q["latency_ms"], 3),
                "exec_id": exec_id,
                "ord_status": exec_status.value,
                "raw_fix": raw_fix_str,
            })

        if not detailed:
            return fills

        vwap = round(sum(f["fill_qty"] * f["fill_price"] for f in fills) / total_filled, 4) if total_filled > 0 else float(limit_price)
        total_net_fee = round(sum(f["fee"] for f in fills), 4)
        total_rebates = round(sum(f["rebate"] for f in fills), 4)
        total_cost = round(sum(f["fill_qty"] * f["fill_price"] for f in fills) + total_net_fee, 4)
        avg_latency = round(sum(f["latency_ms"] for f in fills) / len(fills), 3) if fills else 0.0
        max_latency = round(max(f["latency_ms"] for f in fills), 3) if fills else 0.0
        nbbo = self.synthesize_nbbo(symbol, limit_price)

        return {
            "symbol": symbol,
            "side": side_str,
            "quantity": float(qty),
            "limit_price": float(limit_price),
            "routing_policy": policy_str,
            "status": "FILLED" if remaining_qty <= 0 else ("PARTIALLY_FILLED" if total_filled > 0 else "UNFILLED"),
            "total_filled_qty": total_filled,
            "leaves_qty": max(0.0, remaining_qty),
            "weighted_avg_price": vwap,
            "total_net_fee": total_net_fee,
            "total_rebates": total_rebates,
            "total_cost": total_cost,
            "avg_latency_ms": avg_latency,
            "max_latency_ms": max_latency,
            "fills": fills,
            "nbbo": nbbo,
            "fix_audit_log": fix_audit_log,
        }


# --- Global Singleton FIX Session Helper ---

_global_fix_session: Optional[FixSession] = None


def get_global_fix_session() -> FixSession:
    """Returns or creates the shared institutional FIX 4.4 session singleton.

    On first construction, attempts to restore persisted sequence numbers, session
    state, and order book from disk -- mirroring FixSessionManager.get_or_create_session's
    own restore order: a session-specific state file first
    (output/fix_session_INVESTYO_PWA_FIX_GATEWAY.json), falling back to the shared
    default file (output/fix_session_state.json) -- so a process restart doesn't
    silently reset sequence numbers to a hardcoded starting point out from under a
    real venue counterparty. restore_state() never raises (it logs and returns False
    on a missing/corrupt file), so this falls back to the prior hardcoded-fresh-session
    behavior whenever no valid state file exists.
    """
    global _global_fix_session
    if _global_fix_session is None:
        import os

        _global_fix_session = FixSession(
            sender_comp_id="INVESTYO_PWA",
            target_comp_id="FIX_GATEWAY",
            heartbeat_int=settings.FIX_HEARTBEAT_INTERVAL_SECONDS,
        )
        state_dir = "output"
        session_file = os.path.join(state_dir, "fix_session_INVESTYO_PWA_FIX_GATEWAY.json")
        global_file = os.path.join(state_dir, "fix_session_state.json")
        restored = False
        if os.path.exists(session_file):
            restored = _global_fix_session.restore_state(session_file)
        if not restored and os.path.exists(global_file):
            restored = _global_fix_session.restore_state(global_file)

        if not restored:
            _global_fix_session._set_state(FixSessionState.CONNECTED)
            _global_fix_session.out_seq_num = 142
            _global_fix_session.in_seq_num = 142
            _global_fix_session.last_heard_at = time.time()
            _global_fix_session.last_sent_at = time.time()
    return _global_fix_session


# Alias for backward compatibility
SmartOrderRouter = MultiVenueAggregator
