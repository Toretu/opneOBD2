"""VW Transport Protocol 2.0 (TP2.0) over CAN.

TP2.0 is VAG's proprietary transport that carries KWP2000 diagnostics to control
modules that do *not* answer ISO-TP/UDS (typically ABS, airbag, steering and
gateway on PQ-platform cars). A generic ELM327 cannot speak it; a raw-CAN
adapter (e.g. a CANable on SocketCAN) can.

Protocol outline (as implemented here):

* **Channel setup** — the tester broadcasts on CAN id ``0x200``:
  ``[dest, 0xC0, 0x00, 0x10, 0x00, 0x03, 0x01]``. The module replies on
  ``0x200 + dest`` with ``[dest, 0xD0, rx_lo, rx_hi, tx_lo, tx_hi, 0x01]``,
  assigning the CAN ids for the channel (``tx`` = tester→module,
  ``rx`` = module→tester).
* **Channel parameters** — ``0xA0`` request / ``0xA1`` response exchange timing.
* **Data transfer** — KWP telegrams (a 2-byte big-endian length followed by the
  KWP message) are split into frames whose first byte is ``opcode<<4 | seq``.

The frame opcodes and setup byte offsets are centralised as constants so they
can be calibrated against a real capture; the framing/state-machine logic is
exercised by :class:`FakeTp20Ecu` in the tests.
"""

from __future__ import annotations

import time
from typing import List, Optional

from .canbus import CanBus, CanFrame
SETUP_ID = 0x200

# Channel-setup opcodes (on SETUP_ID / SETUP_ID+dest).
OP_SETUP_REQUEST = 0xC0
OP_SETUP_RESPONSE = 0xD0
OP_SETUP_REJECT = 0xD8

# Data-frame opcodes (high nibble of the first byte).
OP_MORE_ACK = 0x0  # more frames follow, ACK expected
OP_LAST_ACK = 0x1  # last frame of telegram, ACK expected
OP_MORE_NOACK = 0x2  # more frames follow, no ACK
OP_LAST_NOACK = 0x3  # last frame of telegram, no ACK
OP_ACK_READY = 0xB  # acknowledge, ready for next block
OP_ACK_WAIT = 0x9  # acknowledge, not ready
OP_PARAM_REQUEST = 0xA0
OP_PARAM_RESPONSE = 0xA1
OP_DISCONNECT = 0xA8

# Default channel parameters (block size + T1..T4 timing bytes).
PARAM_REQUEST = bytes([OP_PARAM_REQUEST, 0x0F, 0x8A, 0xFF, 0x32, 0xFF])
PARAM_RESPONSE = bytes([OP_PARAM_RESPONSE, 0x0F, 0x8A, 0xFF, 0x4A, 0xFF])


def _decode_timing_parameter(value: int) -> float:
    units = (0.0001, 0.001, 0.01, 0.1)
    return units[(value >> 6) & 0x03] * (value & 0x3F)


# ---------------------------------------------------------------------------
# Pure framing helpers (no bus, fully testable).
# ---------------------------------------------------------------------------

def build_telegram(kwp: bytes) -> bytes:
    """Prefix a KWP message with its 2-byte big-endian length."""

    return bytes([(len(kwp) >> 8) & 0xFF, len(kwp) & 0xFF]) + kwp


def encode_data_frames(kwp: bytes, start_seq: int = 0) -> List[bytes]:
    """Split a KWP message into TP2.0 data frame payloads (first byte = PCI)."""

    telegram = build_telegram(kwp)
    chunks = [telegram[i:i + 7] for i in range(0, len(telegram), 7)] or [b""]
    frames: List[bytes] = []
    seq = start_seq & 0x0F
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        opcode = OP_LAST_ACK if last else OP_MORE_NOACK
        frames.append(bytes([(opcode << 4) | seq]) + chunk)
        seq = (seq + 1) & 0x0F
    return frames


def decode_telegram(frames: List[bytes]) -> bytes:
    """Reassemble data frame payloads back into the KWP message."""

    payload = bytearray()
    for frame in frames:
        payload += frame[1:]
    if len(payload) < 2:
        return b""
    length = (payload[0] << 8) | payload[1]
    return bytes(payload[2:2 + length])


def frame_opcode(frame: bytes) -> int:
    return frame[0] >> 4


def is_last(frame: bytes) -> bool:
    return frame_opcode(frame) in (OP_LAST_ACK, OP_LAST_NOACK)


def wants_ack(frame: bytes) -> bool:
    return frame_opcode(frame) in (OP_MORE_ACK, OP_LAST_ACK)


# ---------------------------------------------------------------------------
# Channel (drives a real or fake CanBus).
# ---------------------------------------------------------------------------

class Tp20Error(Exception):
    """Raised on TP2.0 setup/timeout/protocol errors."""


class Tp20Channel:
    """An open TP2.0 channel to one module, used to exchange KWP telegrams."""

    def __init__(self, bus: CanBus, address: int, timeout: float = 1.0) -> None:
        self._bus = bus
        self._addr = address
        self._timeout = timeout
        self._tx_id: Optional[int] = None
        self._rx_id: Optional[int] = None
        self._tx_seq = 0
        self._rx_seq = 0
        self._trace: List[str] = []
        self._t3_seconds = 0.0

    def _record_frame(self, direction: str, frame: CanFrame) -> None:
        self._trace.append(f"{direction} {frame.arbitration_id:03X}: {frame.data.hex(' ').upper()}")
        del self._trace[:-20]

    def _raise(self, message: str) -> None:
        trace = "; ".join(self._trace) if self._trace else "no CAN frames received"
        raise Tp20Error(f"{message}; CAN trace: {trace}")

    def _send(self, frame: CanFrame) -> None:
        self._record_frame("TX", frame)
        self._bus.send(frame)
        if self._t3_seconds:
            time.sleep(self._t3_seconds)

    # -- setup ------------------------------------------------------------

    def open(self) -> None:
        self._send(CanFrame(SETUP_ID, bytes(
            [self._addr, OP_SETUP_REQUEST, 0x00, 0x10, 0x00, 0x03, 0x01])))
        resp = self._recv_on(SETUP_ID + self._addr)
        if resp is None:
            self._raise(f"no TP2.0 setup response from module 0x{self._addr:02X}")
        if resp.data[1] != OP_SETUP_RESPONSE:
            self._raise(f"module 0x{self._addr:02X} rejected channel setup")
        self._rx_id = resp.data[2] | (resp.data[3] << 8)
        self._tx_id = resp.data[4] | (resp.data[5] << 8)

        self._send(CanFrame(self._tx_id, PARAM_REQUEST))
        param = self._recv_on(self._rx_id)
        if param is None or len(param.data) < 6 or param.data[0] != OP_PARAM_RESPONSE:
            self._raise("TP2.0 channel parameters not acknowledged")
        self._t3_seconds = _decode_timing_parameter(param.data[4])

    def close(self) -> None:
        if self._tx_id is not None:
            try:
                self._send(CanFrame(self._tx_id, bytes([OP_DISCONNECT, 0x00, 0x03, 0x01])))
            except Exception:
                pass

    # -- data exchange ----------------------------------------------------

    def request(self, kwp_request: bytes) -> bytes:
        """Send a KWP request telegram and return the KWP response telegram."""

        if self._tx_id is None or self._rx_id is None:
            raise Tp20Error("channel is not open")

        frames = encode_data_frames(kwp_request, self._tx_seq)
        for frame in frames:
            self._send(CanFrame(self._tx_id, frame))
            self._tx_seq = (self._tx_seq + 1) & 0x0F
            if wants_ack(frame):
                self._await_ack()
        return self._receive_telegram()

    def _await_ack(self) -> None:
        frame = self._recv_on(self._rx_id)
        while frame is not None and frame_opcode(frame.data) == OP_ACK_WAIT:
            frame = self._recv_on(self._rx_id)
        if frame is None or frame_opcode(frame.data) != OP_ACK_READY:
            self._raise("missing TP2.0 ACK")

    def _receive_telegram(self) -> bytes:
        frames: List[bytes] = []
        while True:
            frame = self._recv_on(self._rx_id)
            if frame is None:
                self._raise("timed out waiting for TP2.0 response")
            opcode = frame_opcode(frame.data)
            if opcode in (OP_ACK_READY, OP_ACK_WAIT):
                continue  # stray ack
            frames.append(frame.data)
            if wants_ack(frame.data):
                self._send_ack()
            if is_last(frame.data):
                break
        return decode_telegram(frames)

    def _send_ack(self) -> None:
        self._rx_seq = (self._rx_seq + 1) & 0x0F
        self._send(CanFrame(self._tx_id, bytes([(OP_ACK_READY << 4) | self._rx_seq])))

    def _recv_on(self, arbitration_id: Optional[int]) -> Optional[CanFrame]:
        deadline_tries = 64  # bounded poll for the fake bus; real bus honours timeout
        for _ in range(deadline_tries):
            frame = self._bus.recv(self._timeout)
            if frame is None:
                return None
            self._record_frame("RX", frame)
            if arbitration_id is None or frame.arbitration_id == arbitration_id:
                return frame
        return None


# ---------------------------------------------------------------------------
# High-level read / clear over a TP2.0 channel.
# ---------------------------------------------------------------------------

def read_module_dtcs(bus: CanBus, address: int, timeout: float = 1.0):
    """Open a TP2.0 channel to ``address`` and read its DTCs via KWP2000."""

    from . import kwp

    channel = Tp20Channel(bus, address, timeout)
    channel.open()
    try:
        session_response = channel.request(kwp.build_start_session())
        kwp.parse_start_session(session_response)
        response = channel.request(kwp.build_read_dtcs())
        return kwp.parse_read_dtcs(response)
    finally:
        channel.close()


def clear_module_dtcs(bus: CanBus, address: int, timeout: float = 1.0) -> bool:
    """Open a TP2.0 channel to ``address`` and clear its DTCs via KWP2000."""

    from . import kwp

    channel = Tp20Channel(bus, address, timeout)
    channel.open()
    try:
        session_response = channel.request(kwp.build_start_session())
        kwp.parse_start_session(session_response)
        response = channel.request(kwp.build_clear_dtcs())
        return kwp.parse_clear_dtcs(response)
    finally:
        channel.close()


# ---------------------------------------------------------------------------
# Simulated ECU for tests (a canbus.Responder).
# ---------------------------------------------------------------------------
class FakeTp20Ecu:
    """A minimal TP2.0 + KWP module used to test the channel end to end.

    Reacts to channel setup, parameter exchange and KWP data telegrams, replying
    with frames on the assigned channel. ``handler(kwp_request) -> kwp_response``
    supplies the application-layer behaviour (e.g. read/clear DTCs).
    """

    def __init__(self, address: int, handler) -> None:
        self.address = address
        self._handler = handler
        self.tx_id = 0x300 + address  # tester -> ECU
        self.rx_id = 0x380 + address  # ECU -> tester
        self._rx_frames: List[bytes] = []
        self._seq = 0

    def handle(self, frame: CanFrame) -> List[CanFrame]:
        if frame.arbitration_id == SETUP_ID and frame.data and frame.data[0] == self.address:
            if frame.data[1] == OP_SETUP_REQUEST:
                reply = bytes([
                    self.address, OP_SETUP_RESPONSE,
                    self.rx_id & 0xFF, (self.rx_id >> 8) & 0xFF,  # tester receives here
                    self.tx_id & 0xFF, (self.tx_id >> 8) & 0xFF,  # tester transmits here
                    0x01,
                ])
                return [CanFrame(SETUP_ID + self.address, reply)]
            return []

        if frame.arbitration_id != self.tx_id or not frame.data:
            return []

        opcode = frame_opcode(frame.data)
        if opcode == OP_PARAM_REQUEST // 16 or frame.data[0] == OP_PARAM_REQUEST:
            return [CanFrame(self.rx_id, PARAM_RESPONSE)]
        if frame_opcode(frame.data) in (OP_ACK_READY, OP_ACK_WAIT):
            return []  # ack for our response; ignore

        # Accumulate request data frames.
        self._rx_frames.append(frame.data)
        replies: List[CanFrame] = []
        if wants_ack(frame.data):
            replies.append(CanFrame(self.rx_id, bytes([(OP_ACK_READY << 4)])))
        if is_last(frame.data):
            kwp_request = decode_telegram(self._rx_frames)
            self._rx_frames = []
            kwp_response = self._handler(kwp_request)
            for data in encode_data_frames(kwp_response, 0):
                replies.append(CanFrame(self.rx_id, data))
        return replies
