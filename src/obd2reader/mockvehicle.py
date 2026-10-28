"""A configurable, UDS-aware simulated vehicle for hardware-free demos and tests.

Unlike :class:`obd2reader.mock.MockConnection` (a minimal single-ECU ELM327
stub), this module simulates a whole car: several control modules, each with its
own trouble codes, answering both legacy OBD-II (mode 03/04) and manufacturer
UDS service ``0x19``/``0x14`` requests. The set of faults is fully configurable,
so the GUI can let a user pick which errors appear and preview exactly how a real
scan would look.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from .dtc import bytes_to_code, code_to_bytes, describe
from .uds import response_id_for

# Status byte presets (ISO 14229 DTC status).
STATUS_ACTIVE = 0x08 | 0x80  # confirmed + warning light on
STATUS_PASSIVE = 0x50  # not currently failing (history only)


@dataclass(frozen=True)
class MockFault:
    """A single simulated DTC within a module."""

    code: str
    failure_type: int = 0x00
    status: int = STATUS_ACTIVE
    label: str = ""  # human-friendly component name for the fault chooser

    @property
    def description(self) -> str:
        return describe(self.code)

    @property
    def title(self) -> str:
        """Friendly chooser label, falling back to the decoded description."""

        return self.label or describe(self.code)


@dataclass
class MockModule:
    """A simulated control module and its faults."""

    request_id: int
    name: str
    faults: List[MockFault] = field(default_factory=list)

    @property
    def response_id(self) -> int:
        return response_id_for(self.request_id)


@dataclass
class MockVehicle:
    """A configurable simulated vehicle: a collection of control modules."""

    modules: List[MockModule] = field(default_factory=list)

    def module(self, request_id: int) -> Optional[MockModule]:
        for module in self.modules:
            if module.request_id == request_id:
                return module
        return None

    def clear_module(self, request_id: int) -> None:
        module = self.module(request_id)
        if module is not None:
            module.faults = []

    def clear_all(self) -> None:
        for module in self.modules:
            module.faults = []

    def total_faults(self) -> int:
        return sum(len(m.faults) for m in self.modules)


# ---------------------------------------------------------------------------
# Catalog of selectable preset faults, grouped by module. Used by the GUI's
# "configure mock vehicle" chooser so a user can tick faults and preview them.
# ---------------------------------------------------------------------------

# request_id -> friendly module name (mirrors the VAG map in uds.py).
MOCK_MODULE_NAMES: Dict[int, str] = {
    0x7E0: "Engine",
    0x7E1: "Transmission",
    0x713: "ABS / ESP brakes",
    0x714: "Instrument cluster",
    0x715: "Airbag / SRS",
    0x712: "Steering assist",
}

# request_id -> list of selectable (code, failure_type, default status) presets.
FAULT_CATALOG: Dict[int, List[MockFault]] = {
    0x7E0: [
        MockFault("P0671", 0x13, STATUS_ACTIVE, "Glow plug – cylinder 1"),
        MockFault("P0672", 0x13, STATUS_ACTIVE, "Glow plug – cylinder 2"),
        MockFault("P0673", 0x13, STATUS_ACTIVE, "Glow plug – cylinder 3"),
        MockFault("P0674", 0x13, STATUS_ACTIVE, "Glow plug – cylinder 4"),
        MockFault("P0380", 0x13, STATUS_ACTIVE, "Glow plug control circuit"),
        MockFault("P0299", 0x10, STATUS_ACTIVE, "Turbocharger underboost"),
        MockFault("P2002", 0x18, STATUS_ACTIVE, "Diesel particulate filter (DPF)"),
        MockFault("P0401", 0x00, STATUS_PASSIVE, "EGR flow insufficient (history)"),
    ],
    0x7E1: [
        MockFault("P0700", 0x00, STATUS_ACTIVE, "Transmission control fault"),
        MockFault("P0705", 0x00, STATUS_PASSIVE, "Gear range sensor (history)"),
    ],
    0x713: [
        MockFault("C1011", 0x08, STATUS_ACTIVE, "ABS control unit fault"),
        MockFault("C1041", 0x02, STATUS_ACTIVE, "Wheel speed sensor (front left)"),
        MockFault("C1200", 0x04, STATUS_ACTIVE, "Hydraulic pump / solenoid"),
        MockFault("C1611", 0x01, STATUS_ACTIVE, "ESP module supply voltage"),
    ],
    0x714: [
        MockFault("U0100", 0x00, STATUS_ACTIVE, "Lost comms with engine ECU"),
        MockFault("U0121", 0x00, STATUS_PASSIVE, "Lost comms with ABS (history)"),
    ],
    0x715: [
        MockFault("B1000", 0x11, STATUS_ACTIVE, "Airbag control unit"),
        MockFault("B1005", 0x08, STATUS_ACTIVE, "Driver airbag igniter circuit"),
    ],
    0x712: [
        MockFault("C1511", 0x08, STATUS_ACTIVE, "Steering angle sensor"),
    ],
}


def catalog_module_name(request_id: int) -> str:
    return MOCK_MODULE_NAMES.get(request_id, f"Module 0x{request_id:03X}")


def default_mock_vehicle() -> MockVehicle:
    """A realistic default: a few active faults across engine, ABS and airbag."""

    return MockVehicle(
        modules=[
            MockModule(0x7E0, "Engine", [MockFault("P0380", 0x13), MockFault("P0401", 0x00, STATUS_PASSIVE)]),
            MockModule(0x713, "ABS / ESP brakes", [MockFault("C1011", 0x08), MockFault("C1041", 0x02)]),
            MockModule(0x715, "Airbag / SRS", [MockFault("B1000", 0x11)]),
            MockModule(0x714, "Instrument cluster", []),
        ]
    )


def vehicle_from_selection(selected: Dict[int, List[MockFault]]) -> MockVehicle:
    """Build a vehicle from a {request_id: [faults]} selection (chooser output).

    Modules with no selected faults are still included (so they show as "OK").
    """

    modules: List[MockModule] = []
    for request_id in FAULT_CATALOG:
        faults = [replace(f) for f in selected.get(request_id, [])]
        modules.append(MockModule(request_id, catalog_module_name(request_id), faults))
    return MockVehicle(modules=modules)


# ---------------------------------------------------------------------------
# ISO-TP framing helpers (mirror what a real ECU puts on the wire with ATH1 +
# ATS0: each line is "<RESPID><framebytes>" in hex, frames separated by CR).
# ---------------------------------------------------------------------------

def _isotp_frames(response_id: int, payload: bytes) -> str:
    rid = f"{response_id:03X}"
    n = len(payload)
    if n <= 7:  # single frame
        return f"{rid}{n:02X}{payload.hex().upper()}\r"

    lines = []
    first = bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]
    lines.append(f"{rid}{first.hex().upper()}")
    seq = 1
    index = 6
    while index < n:
        chunk = payload[index:index + 7]
        pci = bytes([0x20 | (seq & 0x0F)])
        lines.append(f"{rid}{(pci + chunk).hex().upper()}")
        seq += 1
        index += 7
    return "\r".join(lines) + "\r"


def _encode_1902(module: MockModule) -> bytes:
    """Build the UDS 59 02 <mask> + records payload for a module."""

    payload = bytearray([0x59, 0x02, 0xFF])
    for fault in module.faults:
        byte_a, byte_b = code_to_bytes(fault.code)
        payload += bytes([byte_a, byte_b, fault.failure_type & 0xFF, fault.status & 0xFF])
    return bytes(payload)


class MockVehicleConnection:
    """A :class:`~obd2reader.elm327.Connection` backed by a :class:`MockVehicle`.

    Answers legacy OBD-II (mode 03/07/0A/04) for the engine module and
    manufacturer UDS (service 0x19 subfunction 0x02, and clear service 0x14) for
    whichever module the last ``ATSH`` selected.
    """

    def __init__(self, vehicle: Optional[MockVehicle] = None) -> None:
        self.vehicle = vehicle if vehicle is not None else default_mock_vehicle()
        self._last = ""
        self._tx_header: Optional[int] = None
        self._headers = False  # ATH0 by default (toggled by ATH1)

    # -- Connection interface ---------------------------------------------

    def write(self, data: str) -> None:
        self._last = data.strip().upper()

    def read_until_prompt(self) -> str:
        command = self._last
        if command == "ATH1":
            self._headers = True
            return "OK"
        if command == "ATH0":
            self._headers = False
            return "OK"
        if command.startswith("ATSH"):
            try:
                self._tx_header = int(command[4:], 16)
            except ValueError:
                self._tx_header = None
            return "OK"
        if command.startswith("AT"):
            return "ELM327 v1.5" if command == "ATZ" else "OK"
        if command == "0100":
            return self._with_header("06 41 00 BE 3F B8 13")
        if command == "03":
            return self._legacy_mode(0x43)
        if command == "07":
            return self._legacy_mode(0x47, pending_only=True)
        if command == "0A":
            return self._with_header("02 4A 00")
        if command == "04":
            self.vehicle.clear_module(0x7E0)
            return self._with_header("01 44")
        if command.startswith("1902"):
            return self._uds_read_dtcs()
        if command.startswith("14"):
            return self._uds_clear()
        return "NO DATA"

    def close(self) -> None:
        pass

    # -- Internals --------------------------------------------------------

    def _with_header(self, body: str) -> str:
        """Prefix the engine response id only when headers (ATH1) are on."""

        return f"7E8 {body}" if self._headers else body

    def _engine_codes(self, pending: bool) -> List[str]:
        engine = self.vehicle.module(0x7E0)
        if engine is None:
            return []
        wanted_passive = pending  # pending bucket shows history-only codes
        return [
            f.code
            for f in engine.faults
            if (f.status == STATUS_PASSIVE) == wanted_passive
        ]

    def _legacy_mode(self, response_byte: int, pending_only: bool = False) -> str:
        codes = self._engine_codes(pending=pending_only)
        if not codes:
            return self._with_header(f"02 {response_byte:02X} 00")
        body_parts = [f"{response_byte:02X}", f"{len(codes):02X}"]
        for code in codes:
            byte_a, byte_b = code_to_bytes(code)
            body_parts.append(f"{byte_a:02X}{byte_b:02X}")
        return self._with_header(" ".join(body_parts))

    def _uds_read_dtcs(self) -> str:
        if self._tx_header is None:
            return "NO DATA"
        module = self.vehicle.module(self._tx_header)
        if module is None:
            return "NO DATA"
        payload = _encode_1902(module)
        return _isotp_frames(module.response_id, payload)

    def _uds_clear(self) -> str:
        if self._tx_header is None:
            return "NO DATA"
        module = self.vehicle.module(self._tx_header)
        if module is None:
            return "NO DATA"
        # 14 <3-byte group>; group FFFFFF clears everything in the module.
        group = self._last[2:8]
        if group == "FFFFFF" or len(group) < 6:
            module.faults = []
        else:
            try:
                code = bytes_to_code(int(group[0:2], 16), int(group[2:4], 16))
                module.faults = [f for f in module.faults if f.code != code]
            except ValueError:
                pass
        return f"{module.response_id:03X} 01 54"
