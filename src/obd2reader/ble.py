"""Direct Bluetooth Low Energy (BLE) support for ELM327 adapters.

Many newer OBD-II dongles are BLE devices that do *not* appear as a serial
port. This module talks to them over GATT using :mod:`bleak`, exposing the same
synchronous transport interface as :class:`~obd2reader.elm327.SerialConnection`.

``bleak`` is an optional dependency; install it with ``pip install obd2reader[ble]``.
It is imported lazily so the rest of the package works without it.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable, List, Optional, Sequence, Tuple

# Known characteristic UUIDs used by common ELM327 BLE clones, in preference
# order. Discovery falls back to any characteristic with the right properties.
_NOTIFY_PREFERRED = (
    "0000ffe1-0000-1000-8000-00805f9b34fb",  # HM-10 style (single characteristic)
    "0000fff1-0000-1000-8000-00805f9b34fb",  # split notify characteristic
    "6e400003-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART TX
)
_WRITE_PREFERRED = (
    "0000ffe1-0000-1000-8000-00805f9b34fb",
    "0000fff2-0000-1000-8000-00805f9b34fb",  # split write characteristic
    "6e400002-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART RX
)


def _pick(
    chars: Sequence[Tuple[str, Iterable[str]]],
    preferred: Sequence[str],
    required: Sequence[str],
) -> Optional[str]:
    lowered = [(uuid.lower(), {p.lower() for p in props}) for uuid, props in chars]
    required_set = {p.lower() for p in required}
    for pref in preferred:
        for uuid, props in lowered:
            if uuid == pref and props & required_set:
                return uuid
    for uuid, props in lowered:
        if props & required_set:
            return uuid
    return None


def select_characteristics(
    chars: Sequence[Tuple[str, Iterable[str]]],
) -> Tuple[str, str]:
    """Choose (write_uuid, notify_uuid) from a list of (uuid, properties).

    Raises :class:`RuntimeError` when no suitable characteristics are present.
    """

    write = _pick(chars, _WRITE_PREFERRED, ("write", "write-without-response"))
    notify = _pick(chars, _NOTIFY_PREFERRED, ("notify", "indicate"))
    if write is None or notify is None:
        raise RuntimeError("No suitable BLE characteristics found on the adapter")
    return write, notify


class _NotificationBuffer:
    """Thread-safe byte buffer that yields data up to the ELM327 ``>`` prompt."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._cond = threading.Condition()

    def feed(self, data: bytes) -> None:
        with self._cond:
            self._buffer.extend(data)
            self._cond.notify_all()

    def read_until_prompt(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self._cond:
            while b">" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)

            if b">" in self._buffer:
                index = self._buffer.index(b">")
                chunk = bytes(self._buffer[:index])
                del self._buffer[: index + 1]
            else:  # timed out: return whatever arrived
                chunk = bytes(self._buffer)
                self._buffer.clear()
        return chunk.decode("ascii", errors="ignore")


class BleConnection:
    """A BLE transport for ELM327 adapters, driven through :mod:`bleak`."""

    def __init__(
        self,
        address: str,
        write_uuid: Optional[str] = None,
        notify_uuid: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        import asyncio

        from bleak import BleakClient

        self._timeout = timeout
        self._buffer = _NotificationBuffer()
        self._write_uuid = write_uuid
        self._notify_uuid = notify_uuid

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        self._client = BleakClient(address, timeout=timeout)
        self._run(self._async_connect())

    def _run(self, coro):
        import asyncio

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(self._timeout + 5)

    async def _async_connect(self) -> None:
        await self._client.connect()
        if self._write_uuid is None or self._notify_uuid is None:
            chars = [
                (char.uuid, char.properties)
                for service in self._client.services
                for char in service.characteristics
            ]
            write, notify = select_characteristics(chars)
            self._write_uuid = self._write_uuid or write
            self._notify_uuid = self._notify_uuid or notify
        await self._client.start_notify(self._notify_uuid, self._on_notify)

    def _on_notify(self, _characteristic, data: bytearray) -> None:
        self._buffer.feed(bytes(data))

    def write(self, data: str) -> None:
        payload = (data + "\r").encode("ascii")
        self._run(self._client.write_gatt_char(self._write_uuid, payload, response=False))

    def read_until_prompt(self) -> str:
        return self._buffer.read_until_prompt(self._timeout)

    def close(self) -> None:
        try:
            self._run(self._client.disconnect())
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def scan_bluetooth(timeout: float = 8.0) -> List[Tuple[str, str]]:
    """Discover nearby BLE devices, returning a list of (address, name)."""

    import asyncio

    return asyncio.run(_async_scan(timeout))


async def _async_scan(timeout: float) -> List[Tuple[str, str]]:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout)
    return [(device.address, device.name or "Unknown") for device in devices]
