"""KWP2000 (ISO 14230) diagnostic application codec for VAG modules.

Older VAG control modules (ABS, airbag, steering, ...) answer manufacturer
diagnostics in KWP2000 carried over the VW TP2.0 transport (see :mod:`tp20`).
This module builds the request bytes and parses the response bytes for the two
operations we need — reading and clearing trouble codes — as pure functions, so
they are fully testable without any CAN hardware.

A VAG KWP DTC is a 2-byte code plus a 1-byte status, conventionally shown as a
5-digit decimal number (e.g. ``00668``). Mapping those numbers to text requires
VW's proprietary database, so we expose the raw number and status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

# KWP2000 service identifiers.
SVC_START_DIAGNOSTIC_SESSION = 0x10
SVC_CLEAR_DTC = 0x14  # clearDiagnosticInformation
SVC_READ_DTC_BY_STATUS = 0x18  # readDiagnosticTroubleCodesByStatus
SVC_TESTER_PRESENT = 0x3E

POSITIVE_RESPONSE_OFFSET = 0x40  # positive response SID = request SID + 0x40
NEGATIVE_RESPONSE = 0x7F

# KWP request type and "all DTCs" selector used by VAG modules.
DTC_REQUEST_STORED_KWP = 0x02
GROUP_ALL = 0xFF00


@dataclass(frozen=True)
class KwpDtc:
    """A trouble code read from a module over KWP2000."""

    number: int  # 2-byte VAG DTC number
    status: int  # KWP status byte

    @property
    def label(self) -> str:
        return f"VAG {self.number:05d}"


class KwpError(Exception):
    """Raised when a module returns a negative or malformed KWP response."""


def build_start_session(session: int = 0x89) -> bytes:
    """StartDiagnosticSession. VAG commonly uses 0x89 for the diagnostic session."""

    return bytes([SVC_START_DIAGNOSTIC_SESSION, session])


def parse_start_session(response: bytes) -> None:
    """Validate a positive StartDiagnosticSession response."""

    _check_positive(response, SVC_START_DIAGNOSTIC_SESSION)


def build_read_dtcs(status_mask: int = DTC_REQUEST_STORED_KWP, group: int = GROUP_ALL) -> bytes:
    """Read stored native KWP DTCs for all groups by default."""

    return bytes([SVC_READ_DTC_BY_STATUS, status_mask & 0xFF,
                  (group >> 8) & 0xFF, group & 0xFF])


def build_clear_dtcs(group: int = GROUP_ALL) -> bytes:
    """clearDiagnosticInformation request for (by default) all DTC groups."""

    return bytes([SVC_CLEAR_DTC, (group >> 8) & 0xFF, group & 0xFF])


def build_tester_present() -> bytes:
    return bytes([SVC_TESTER_PRESENT, 0x01])


def _check_positive(response: bytes, request_sid: int) -> None:
    if not response:
        raise KwpError("empty KWP response")
    if response[0] == NEGATIVE_RESPONSE:
        nrc = response[2] if len(response) >= 3 else 0
        raise KwpError(f"negative response to service 0x{request_sid:02X} (NRC 0x{nrc:02X})")
    expected = (request_sid + POSITIVE_RESPONSE_OFFSET) & 0xFF
    if response[0] != expected:
        raise KwpError(
            f"unexpected response 0x{response[0]:02X} (wanted 0x{expected:02X})"
        )


def parse_read_dtcs(response: bytes) -> List[KwpDtc]:
    """Parse a ``0x58`` readDTCByStatus response into a list of :class:`KwpDtc`.

    Layout: ``58 <count> [dtc_hi dtc_lo status] * count``.
    """

    _check_positive(response, SVC_READ_DTC_BY_STATUS)
    if len(response) < 2:
        return []
    count = response[1]
    records = response[2:]
    dtcs: List[KwpDtc] = []
    for i in range(0, min(count * 3, len(records) - len(records) % 3), 3):
        number = (records[i] << 8) | records[i + 1]
        status = records[i + 2]
        if number == 0:
            continue
        dtcs.append(KwpDtc(number=number, status=status))
    return dtcs


def parse_clear_dtcs(response: bytes) -> bool:
    """Return ``True`` when a clearDiagnosticInformation response is positive."""

    _check_positive(response, SVC_CLEAR_DTC)
    return True
