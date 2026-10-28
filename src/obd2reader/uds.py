"""Manufacturer DTC reading via UDS service 0x19 over CAN (ISO 15765-4).

Generic OBD-II mode 03 only returns emission-related powertrain codes from the
engine ECU. Many warning lights (ABS, airbag, instrument cluster, ...) live in
other control modules that only answer manufacturer UDS requests. This module
reads those with ReadDTCInformation (service ``0x19``, subfunction ``0x02``
reportDTCByStatusMask) by addressing each module on the CAN bus.

Module addresses are manufacturer-specific; the built-in map targets VW / Audi /
SEAT / Skoda (VAG) 11-bit CAN modules. Modules that do not respond are skipped,
so running a scan on another make simply returns whatever answers.

Note: on some vehicles chassis modules (ABS, steering, gateway) sit on a
separate bus behind a gateway and are not reachable with a generic ELM327.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .dtc import bytes_to_code, describe
from .elm327 import Connection

# UDS DTC status bits (ISO 14229-1, DTCStatusAvailabilityMask).
_STATUS_FLAGS: Sequence[Tuple[int, str]] = (
    (0x01, "test failed"),
    (0x02, "failed this cycle"),
    (0x04, "pending"),
    (0x08, "confirmed"),
    (0x10, "not completed since clear"),
    (0x20, "failed since clear"),
    (0x40, "not completed this cycle"),
    (0x80, "warning light on"),
)

# VAG 11-bit UDS modules: request CAN id -> friendly name. The response id is
# ``request + 8`` for the powertrain ids (0x7E0/0x7E1) and ``request + 0x6A``
# for the rest, matching the VAG diagnostic addressing scheme.
VAG_MODULES: Dict[int, str] = {
    0x7E0: "Engine",
    0x7E1: "Transmission",
    0x710: "Gateway",
    0x712: "Steering assist",
    0x713: "ABS / ESP brakes",
    0x714: "Instrument cluster",
    0x715: "Airbag / SRS",
    0x716: "Steering column electronics",
    0x70A: "Door electronics, driver",
    0x70C: "Door electronics, passenger",
    0x70E: "Central electrics",
    0x717: "Comfort / convenience",
}


# Kia/Hyundai EV: request -> name; response is request + 8. Addresses from the
# JejuSoul/OBD-PIDs-for-HKMC-EVs Soul EV docs (ABS/airbag/parking are not documented
# there, so use --discover to find them).
KIA_MODULES: Dict[int, str] = {
    0x7E2: "Vehicle motor control (VMCU)",
    0x7E4: "Battery management (BMS)",
    0x794: "On-board charger (OBC)",
    0x7C5: "DC-DC converter (LDC)",
    0x7C6: "Instrument cluster",
    0x7D6: "Tyre pressure (TPMS)",
}

KIA_DISCOVER_RANGE = range(0x780, 0x7E8)


@dataclass(frozen=True)
class ModuleDTC:
    """A DTC read from a control module via UDS service 0x19."""

    code: str
    failure_type: int  # UDS failure-type byte (3rd DTC byte)
    status: int  # UDS DTC status byte
    description: str

    @property
    def active(self) -> bool:
        """True when the code is confirmed or currently requesting a warning light."""

        return bool(self.status & 0x08) or bool(self.status & 0x80)

    @property
    def status_text(self) -> str:
        flags = [name for bit, name in _STATUS_FLAGS if self.status & bit]
        return ", ".join(flags) if flags else "no flags"


@dataclass(frozen=True)
class ModuleReport:
    """DTCs read from a single control module."""

    request_id: int
    name: str
    dtcs: List[ModuleDTC]


def response_id_for(request_id: int) -> int:
    """Return the CAN response id for a VAG module request id."""

    if request_id >= 0x740:
        return request_id + 8
    return request_id + 0x6A


def decode_dtc_records(message: bytes) -> List[ModuleDTC]:
    """Decode a positive ``59 02`` response into a list of :class:`ModuleDTC`.

    ``message`` is the reassembled UDS payload starting with ``59 02``. The
    response is ``59 02 <availabilityMask>`` followed by records of a 3-byte DTC
    plus a 1-byte status. Returns an empty list when there are no records.
    """

    if len(message) < 3 or message[0] != 0x59 or message[1] != 0x02:
        return []

    body = message[3:]  # skip 59 02 <availabilityMask>
    dtcs: List[ModuleDTC] = []
    for i in range(0, len(body) - 3, 4):
        b1, b2, b3, status = body[i], body[i + 1], body[i + 2], body[i + 3]
        if b1 == 0 and b2 == 0 and b3 == 0:
            continue  # padding
        code = bytes_to_code(b1, b2)
        dtcs.append(
            ModuleDTC(
                code=code,
                failure_type=b3,
                status=status,
                description=describe(code),
            )
        )
    return dtcs


def reassemble_isotp(raw: str, response_id: int) -> Optional[bytes]:
    """Reassemble the last positive ``59 02`` ISO-TP message from an ELM reply.

    Expects headers enabled (``ATH1``). Frames whose header matches
    ``response_id`` are combined following ISO-TP single/first/consecutive frame
    rules. Negative responses (``7F 19 xx``) are ignored. Returns ``None`` when
    no positive response is present.
    """

    hex_id = f"{response_id:03X}"
    messages: List[bytes] = []
    current = bytearray()
    expected = 0

    for line in raw.replace("\r", "\n").split("\n"):
        line = line.strip().replace(" ", "")
        if not line.startswith(hex_id):
            continue
        frame_hex = line[len(hex_id):]
        if len(frame_hex) % 2:
            frame_hex = frame_hex[:-1]
        if len(frame_hex) < 2:
            continue
        try:
            frame = bytes.fromhex(frame_hex)
        except ValueError:
            continue

        pci = frame[0] >> 4
        if pci == 0:  # single frame
            length = frame[0] & 0x0F
            messages.append(bytes(frame[1:1 + length]))
            current, expected = bytearray(), 0
        elif pci == 1:  # first frame
            expected = ((frame[0] & 0x0F) << 8) | frame[1]
            current = bytearray(frame[2:])
        elif pci == 2:  # consecutive frame
            current.extend(frame[1:])
            if expected and len(current) >= expected:
                messages.append(bytes(current[:expected]))
                current, expected = bytearray(), 0

    if expected and current:
        messages.append(bytes(current[:expected]))

    for message in reversed(messages):
        if len(message) >= 3 and message[0] == 0x59 and message[1] == 0x02:
            return message
    return None


def _setup(conn: Connection) -> None:
    """Initialise the adapter for raw UDS addressing on CAN 11/500."""

    for command in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATSP6", "ATCAF1"):
        conn.write(command)
        conn.read_until_prompt()
    conn.write("0100")  # wake the bus / trigger protocol detection
    conn.read_until_prompt()


def _read_module(conn: Connection, request_id: int) -> Optional[List[ModuleDTC]]:
    """Read DTCs from one module. Returns ``None`` when the module is silent."""

    response_id = response_id_for(request_id)
    conn.write(f"ATSH{request_id:03X}")
    conn.read_until_prompt()
    conn.write(f"ATCRA{response_id:03X}")
    conn.read_until_prompt()

    conn.write("1902FF")  # ReadDTCInformation, reportDTCByStatusMask, mask=FF
    raw = conn.read_until_prompt()

    compact = raw.replace("\r", "").replace(" ", "").upper()
    if "5902" not in compact and "7F19" not in compact:
        return None  # module did not answer

    message = reassemble_isotp(raw, response_id)
    if message is None:
        return []
    return decode_dtc_records(message)


def scan_modules(
    conn: Connection,
    request_ids: Optional[Sequence[int]] = None,
    discover: bool = False,
    active_only: bool = False,
    brand: str = "vag",
) -> List[ModuleReport]:
    """Scan control modules for DTCs over UDS.

    ``brand`` is ``"vag"`` or ``"kia"`` and selects the module map.

    ``request_ids`` overrides the module list. When ``discover`` is true, a wider
    range of VAG 11-bit addresses is probed to find modules not in the built-in
    map. ``active_only`` keeps just confirmed / warning-light codes.
    """

    _setup(conn)

    if request_ids is not None:
        candidates: List[int] = list(request_ids)
    elif brand == "kia":
        candidates = list(KIA_DISCOVER_RANGE) if discover else list(KIA_MODULES)
    elif discover:
        candidates = list(range(0x700, 0x740)) + list(range(0x7E0, 0x7E8))
    else:
        candidates = list(VAG_MODULES.keys())
    names = KIA_MODULES if brand == "kia" else VAG_MODULES

    reports: List[ModuleReport] = []
    for request_id in candidates:
        dtcs = _read_module(conn, request_id)
        if dtcs is None:
            continue
        if active_only:
            dtcs = [d for d in dtcs if d.active]
        name = names.get(request_id, f"Module 0x{request_id:03X}")
        reports.append(ModuleReport(request_id=request_id, name=name, dtcs=dtcs))
    return reports


def clear_module_dtcs(conn: Connection, request_id: int, setup: bool = True) -> bool:
    """Clear all DTCs in one control module via UDS ClearDiagnosticInformation.

    Sends service ``0x14`` with group ``0xFFFFFF`` (all DTCs) to ``request_id``.
    Returns ``True`` when the module acknowledges with a positive ``0x54``
    response. Set ``setup=False`` to skip adapter re-initialisation when clearing
    several modules in a row.
    """

    if setup:
        _setup(conn)

    response_id = response_id_for(request_id)
    conn.write(f"ATSH{request_id:03X}")
    conn.read_until_prompt()
    conn.write(f"ATCRA{response_id:03X}")
    conn.read_until_prompt()

    conn.write("14FFFFFF")  # ClearDiagnosticInformation, all groups
    raw = conn.read_until_prompt()
    return "54" in raw.replace("\r", "").replace(" ", "").upper()

