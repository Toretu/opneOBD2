"""A minimal CAN bus abstraction.

Decouples the TP2.0 / UDS-over-CAN logic from the physical interface so it can
be driven by a real SocketCAN adapter (e.g. a CANable) or by an in-memory fake
in tests. Only the handful of operations the diagnostic layers need are exposed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import os
from typing import Deque, List, Optional, Protocol


@dataclass(frozen=True)
class CanFrame:
    """A classic 11-bit CAN data frame."""

    arbitration_id: int
    data: bytes


class CanBus(Protocol):
    """The transport interface the TP2.0 / CAN diagnostic layers depend on."""

    def send(self, frame: CanFrame) -> None:
        ...

    def recv(self, timeout: float) -> Optional[CanFrame]:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class CanAdapter:
    """A detected CAN interface, either SocketCAN or a serial SLCAN device."""

    kind: str
    channel: str
    label: str


def is_canable_usb(vid: Optional[int], pid: Optional[int], description: str = "") -> bool:
    """Recognize common CANable USB identities, including CANable 2.0."""

    return (vid, pid) == (0x16D0, 0x117E) or "canable" in description.lower()


def discover_can_adapters() -> List[CanAdapter]:
    """Find active SocketCAN interfaces and attached CANable serial adapters."""

    adapters: List[CanAdapter] = []
    net_path = "/sys/class/net"
    try:
        for name in os.listdir(net_path):
            try:
                with open(os.path.join(net_path, name, "type"), encoding="ascii") as stream:
                    is_can = stream.read().strip() == "280"  # ARPHRD_CAN
            except OSError:
                continue
            if is_can:
                adapters.append(CanAdapter("socketcan", name, f"{name} (SocketCAN)"))
    except OSError:
        pass

    try:
        from serial.tools import list_ports

        for port in list_ports.comports():
            description = " ".join(
                value for value in (port.description, port.product, port.manufacturer)
                if value
            )
            if is_canable_usb(port.vid, port.pid, description):
                detail = "CANable 2.0" if (port.vid, port.pid) == (0x16D0, 0x117E) else "CANable USB"
                adapters.append(CanAdapter("slcan", port.device, f"{detail} ({port.device})"))
    except Exception:
        pass
    return adapters


class SocketCanBus:
    """A real SocketCAN interface backed by :mod:`python-can`.

    ``python-can`` is an optional dependency (install the ``[can]`` extra). It is
    imported lazily so the rest of the package works without it. On Linux a
    CANable or USB2CAN adapter typically appears as ``can0`` after bring-up::

        sudo ip link set can0 up type can bitrate 500000
    """

    def __init__(self, channel: str = "can0", bitrate: int = 500000) -> None:
        import can  # lazy: only needed for real hardware

        self._bus = can.Bus(interface="socketcan", channel=channel, bitrate=bitrate)

    def send(self, frame: CanFrame) -> None:
        import can

        self._bus.send(
            can.Message(
                arbitration_id=frame.arbitration_id,
                data=frame.data,
                is_extended_id=False,
            )
        )

    def recv(self, timeout: float) -> Optional[CanFrame]:
        message = self._bus.recv(timeout=timeout)
        if message is None:
            return None
        return CanFrame(message.arbitration_id, bytes(message.data))

    def close(self) -> None:
        try:
            self._bus.shutdown()
        except Exception:
            pass


class SlcanCanBus:
    """A CANable serial device opened directly through python-can's SLCAN driver."""

    def __init__(self, channel: str, bitrate: int = 500000) -> None:
        import can  # lazy: only needed for real hardware

        self._bus = can.Bus(interface="slcan", channel=channel, bitrate=bitrate)

    def send(self, frame: CanFrame) -> None:
        import can

        self._bus.send(
            can.Message(
                arbitration_id=frame.arbitration_id,
                data=frame.data,
                is_extended_id=False,
            )
        )

    def recv(self, timeout: float) -> Optional[CanFrame]:
        message = self._bus.recv(timeout=timeout)
        if message is None:
            return None
        return CanFrame(message.arbitration_id, bytes(message.data))

    def close(self) -> None:
        try:
            self._bus.shutdown()
        except Exception:
            pass


class Responder(Protocol):
    """A simulated ECU: given a sent frame, returns frames to inject onto the bus."""

    def handle(self, frame: CanFrame) -> List[CanFrame]:
        ...


class FakeCanBus:
    """In-memory CAN bus for tests, optionally driven by a simulated ECU.

    Frames sent are passed to the attached :class:`Responder`, whose returned
    frames are queued for :meth:`recv`. This lets a ``FakeTp20Ecu`` react to the
    tester's frames synchronously, exercising the full channel state machine.
    """

    def __init__(self, responder: Optional[Responder] = None) -> None:
        self.responder = responder
        self.sent: List[CanFrame] = []
        self._rx: Deque[CanFrame] = deque()

    def send(self, frame: CanFrame) -> None:
        self.sent.append(frame)
        if self.responder is not None:
            for reply in self.responder.handle(frame):
                self._rx.append(reply)

    def recv(self, timeout: float) -> Optional[CanFrame]:
        if self._rx:
            return self._rx.popleft()
        return None

    def inject(self, frame: CanFrame) -> None:
        """Queue a frame as if it arrived from the bus (test helper)."""

        self._rx.append(frame)

    def close(self) -> None:
        self._rx.clear()
