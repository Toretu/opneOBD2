"""Unified multi-module scanning across transports.

The GUI and callers want one shape of result regardless of how a car is reached:
a list of :class:`~obd2reader.uds.ModuleReport`, each holding per-module DTCs. Two
transports produce them:

* :class:`UdsScanner` — ISO-TP/UDS over an ELM327-style ``Connection`` (engine,
  cluster, airbag, ... on cars that answer generic UDS).
* :class:`Tp20Scanner` — VW TP2.0 + KWP2000 over a raw :class:`~obd2reader.canbus.CanBus`
  (ABS, steering, gateway on VAG cars behind the gateway).

Both expose the same :class:`Scanner` interface (``scan`` / ``clear`` / ``close``)
so higher layers don't care which is in use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence

from . import uds
from .canbus import CanBus
from .elm327 import Connection
from .kwp import KwpError
from .uds import ModuleReport
from .vagdtc import describe_vag

# VAG TP2.0 logical module addresses -> friendly name.
VAG_TP20_MODULES = {
    0x01: "Engine",
    0x02: "Transmission",
    0x03: "ABS / ESP brakes",
    0x08: "Air conditioning",
    0x09: "Central electrics",
    0x15: "Airbag / SRS",
    0x16: "Steering wheel",
    0x17: "Instrument cluster",
    0x19: "Gateway",
    0x25: "Immobilizer",
    0x44: "Power steering",
    0x46: "Comfort / central convenience",
}


@dataclass(frozen=True)
class VagModuleDtc:
    """A TP2.0/KWP DTC presented with the same attributes as a UDS ``ModuleDTC``."""

    number: int
    status: int

    @property
    def code(self) -> str:
        return f"VAG {self.number:05d}"

    @property
    def failure_type(self) -> int:
        return 0  # KWP has no UDS failure-type byte; kept for a uniform view

    @property
    def description(self) -> str:
        return describe_vag(self.number)

    @property
    def active(self) -> bool:
        # readDTCByStatus returns currently-present faults; treat them as active.
        return True


def scan_tp20_modules(
    bus: CanBus, addresses: Optional[Sequence[int]] = None
) -> List[ModuleReport]:
    """Scan VAG modules over TP2.0, returning a report per responding module."""

    from .tp20 import Tp20Error, read_module_dtcs

    candidates = list(addresses) if addresses is not None else list(VAG_TP20_MODULES)
    reports: List[ModuleReport] = []
    failures: List[str] = []
    for address in candidates:
        try:
            kwp_dtcs = read_module_dtcs(bus, address)
        except Tp20Error as exc:
            name = VAG_TP20_MODULES.get(address, f"Module 0x{address:02X}")
            failures.append(f"{name} (0x{address:02X}): {exc}")
            continue
        except KwpError as exc:
            name = VAG_TP20_MODULES.get(address, f"Module 0x{address:02X}")
            failures.append(f"{name} (0x{address:02X}): {exc}")
            continue
        dtcs = [VagModuleDtc(d.number, d.status) for d in kwp_dtcs]
        name = VAG_TP20_MODULES.get(address, f"Module 0x{address:02X}")
        reports.append(ModuleReport(request_id=address, name=name, dtcs=dtcs))
    if not reports and failures:
        raise Tp20Error("No modules returned DTC data. Probe failures: " + "; ".join(failures))
    return reports


class Scanner(Protocol):
    """A transport-agnostic multi-module scanner."""

    def scan(self) -> List[ModuleReport]:
        ...

    def clear(self, request_id: int) -> bool:
        ...

    def close(self) -> None:
        ...


class UdsScanner:
    """Scanner backed by ISO-TP/UDS over an ELM327-style connection."""

    def __init__(self, connection: Connection, active_only: bool = False) -> None:
        self._conn = connection
        self._active_only = active_only

    def scan(self) -> List[ModuleReport]:
        return uds.scan_modules(self._conn, active_only=self._active_only)

    def clear(self, request_id: int) -> bool:
        return uds.clear_module_dtcs(self._conn, request_id)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class Tp20Scanner:
    """Scanner backed by VW TP2.0 + KWP2000 over a raw CAN bus."""

    def __init__(self, bus: CanBus, addresses: Optional[Sequence[int]] = None) -> None:
        self._bus = bus
        self._addresses = addresses

    def scan(self) -> List[ModuleReport]:
        return scan_tp20_modules(self._bus, self._addresses)

    def clear(self, request_id: int) -> bool:
        from .tp20 import clear_module_dtcs

        return clear_module_dtcs(self._bus, request_id)

    def close(self) -> None:
        try:
            self._bus.close()
        except Exception:
            pass
