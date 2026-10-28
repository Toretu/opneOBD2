"""Direct Bluetooth Classic (SPP/RFCOMM) support for ELM327 adapters.

Most cheap ELM327 dongles are Bluetooth *Classic* devices that expose the
Serial Port Profile (SPP). Traditionally you had to bind them to a virtual
serial port first (``sudo rfcomm bind 0 <MAC>`` -> ``/dev/rfcomm0``), but the
``rfcomm`` tool has been removed from modern BlueZ releases.

This module instead opens the adapter's RFCOMM channel directly with a native
Bluetooth socket from the standard library, exposing the same synchronous
transport interface as :class:`~obd2reader.elm327.SerialConnection`. No extra
dependencies and no ``/dev/rfcomm0`` are required (Linux and other platforms
whose Python ships ``AF_BLUETOOTH`` support).
"""

from __future__ import annotations

import socket
from typing import List, Optional, Tuple

# Virtually all ELM327 SPP clones advertise their serial port on RFCOMM
# channel 1; this is used when a channel cannot be (or is not) discovered.
_DEFAULT_RFCOMM_CHANNEL = 1


def _rfcomm_supported() -> bool:
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


class RfcommConnection:
    """A Bluetooth Classic SPP transport backed by a native RFCOMM socket."""

    def __init__(
        self,
        address: str,
        channel: Optional[int] = None,
        timeout: float = 5.0,
    ) -> None:
        if not _rfcomm_supported():
            raise RuntimeError(
                "This platform's Python has no Bluetooth RFCOMM socket support. "
                "Pair the adapter and connect through a serial port instead."
            )

        if channel is None:
            channel = find_rfcomm_channel(address) or _DEFAULT_RFCOMM_CHANNEL

        self._sock = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
        )
        self._sock.settimeout(timeout)
        self._sock.connect((address, channel))

    def write(self, data: str) -> None:
        self._sock.sendall((data + "\r").encode("ascii"))

    def read_until_prompt(self) -> str:
        buffer = bytearray()
        while True:
            try:
                chunk = self._sock.recv(1)
            except socket.timeout:  # no more data arrived in time
                break
            if not chunk:  # peer closed the connection
                break
            if chunk == b">":  # ELM327 command prompt marks end of response
                break
            buffer += chunk
        return buffer.decode("ascii", errors="ignore")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def find_rfcomm_channel(address: str) -> Optional[int]:
    """Best-effort SDP lookup of the Serial Port channel for ``address``.

    Returns the RFCOMM channel advertised by the device's Serial Port Profile
    record, or ``None`` when it cannot be determined (no BlueZ/D-Bus access, no
    SPP record, etc.). Callers should fall back to a sensible default.
    """

    try:
        return _find_rfcomm_channel_dbus(address)
    except Exception:
        return None


def _find_rfcomm_channel_dbus(address: str) -> Optional[int]:
    """Resolve the SPP RFCOMM channel from BlueZ's cached SDP records via D-Bus."""

    from dbus_fast import BusType  # provided transitively by bleak's dependency
    from dbus_fast.aio import MessageBus
    import asyncio

    async def query() -> Optional[int]:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            introspection = await bus.introspect("org.bluez", "/")
            obj = bus.get_proxy_object("org.bluez", "/", introspection)
            manager = obj.get_interface("org.freedesktop.DBus.ObjectManager")
            objects = await manager.call_get_managed_objects()

            device_path = "dev_" + address.upper().replace(":", "_")
            for path, interfaces in objects.items():
                if not path.endswith(device_path):
                    continue
                props = interfaces.get("org.bluez.Device1")
                if props is None:
                    continue
                # BlueZ does not expose raw SDP channels over its stable API, so
                # fall back to the conventional SPP channel when present.
                uuids = props.get("UUIDs")
                if uuids is not None and any(
                    str(u).lower().startswith("00001101") for u in uuids.value
                ):
                    return _DEFAULT_RFCOMM_CHANNEL
            return None
        finally:
            bus.disconnect()

    try:
        return asyncio.run(query())
    except RuntimeError:
        # Already inside a running loop: run in a fresh thread with its own loop.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(query())).result()


def list_paired_spp_devices() -> List[Tuple[str, str]]:
    """List paired Bluetooth Classic devices advertising the Serial Port Profile.

    Returns a list of ``(address, name)`` tuples. Requires BlueZ via D-Bus
    (Linux); returns an empty list when that is unavailable.
    """

    try:
        return _list_paired_spp_dbus()
    except Exception:
        return []


def _list_paired_spp_dbus() -> List[Tuple[str, str]]:
    import asyncio

    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    async def query() -> List[Tuple[str, str]]:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            introspection = await bus.introspect("org.bluez", "/")
            obj = bus.get_proxy_object("org.bluez", "/", introspection)
            manager = obj.get_interface("org.freedesktop.DBus.ObjectManager")
            objects = await manager.call_get_managed_objects()

            devices: List[Tuple[str, str]] = []
            for _path, interfaces in objects.items():
                props = interfaces.get("org.bluez.Device1")
                if props is None:
                    continue
                uuids = props.get("UUIDs")
                if uuids is None or not any(
                    str(u).lower().startswith("00001101") for u in uuids.value
                ):
                    continue
                address = props["Address"].value
                name = props["Name"].value if "Name" in props else address
                devices.append((address, name))
            return devices
        finally:
            bus.disconnect()

    return asyncio.run(query())
