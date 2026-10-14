"""ELM327 adapter communication over a (Bluetooth) serial connection.

A Bluetooth ELM327 adapter is exposed to the operating system as a virtual
serial port:

* Windows: a COM port (for example ``COM5``) after pairing the adapter.
* Linux:   an rfcomm device (for example ``/dev/rfcomm0``) created with
           ``sudo rfcomm bind 0 <adapter-MAC>``.

Both are handled transparently through :class:`SerialConnection`.
"""

from __future__ import annotations

from typing import Protocol


class Connection(Protocol):
    """Minimal transport interface the ELM327 driver depends on."""

    def write(self, data: str) -> None:
        ...

    def read_until_prompt(self) -> str:
        ...

    def close(self) -> None:
        ...


class SerialConnection:
    """A real serial/Bluetooth transport backed by :mod:`pyserial`."""

    def __init__(self, port: str, baudrate: int = 38400, timeout: float = 5.0) -> None:
        import serial  # imported lazily so tests run without hardware/pyserial

        self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)

    def write(self, data: str) -> None:
        self._serial.write((data + "\r").encode("ascii"))

    def read_until_prompt(self) -> str:
        buffer = bytearray()
        while True:
            byte = self._serial.read(1)
            if not byte:  # read timeout
                break
            if byte == b">":  # ELM327 command prompt marks end of response
                break
            buffer += byte
        return buffer.decode("ascii", errors="ignore")

    def close(self) -> None:
        self._serial.close()


class ELM327:
    """Drives an ELM327 adapter: initialisation and raw command queries."""

    _INIT_COMMANDS = ("ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0")

    def __init__(self, connection: Connection) -> None:
        self._conn = connection
        self.connected = False

    def connect(self) -> None:
        """Reset the adapter and configure it for automatic protocol detection."""

        for command in self._INIT_COMMANDS:
            self._conn.write(command)
            self._conn.read_until_prompt()
        self.connected = True

    def query(self, command: str) -> str:
        """Send an OBD command and return the cleaned textual response."""

        self._conn.write(command)
        return self._clean(self._conn.read_until_prompt())

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _clean(raw: str) -> str:
        lines = []
        for line in raw.replace("\r", "\n").split("\n"):
            line = line.strip()
            if not line or line.upper() in ("OK", "SEARCHING...", "SEARCHING"):
                continue
            lines.append(line)
        return "\n".join(lines)
