"""A stateful in-memory ELM327 simulator for hardware-free demos and tests.

It emulates a vehicle reporting a realistic mix of faults, including glow-plug
and ABS/EBS codes, and honours clear requests (mode 04 and UDS 0x14).
"""

from __future__ import annotations

from typing import List

from .dtc import bytes_to_code, code_to_bytes


def _build_dtc_response(response_byte: int, codes: List[str]) -> str:
    if not codes:
        return f"{response_byte:02X} 00"
    parts = [f"{response_byte:02X}", f"{len(codes):02X}"]
    for code in codes:
        byte_a, byte_b = code_to_bytes(code)
        parts.append(f"{byte_a:02X}{byte_b:02X}")
    return " ".join(parts)


class MockConnection:
    """Implements the :class:`~obd2reader.elm327.Connection` transport interface."""

    def __init__(self) -> None:
        self.stored: List[str] = ["P0380", "P0671", "P2002", "U0100", "C1011"]
        self.pending: List[str] = ["P0401"]
        self.permanent: List[str] = []
        self._last = ""

    def write(self, data: str) -> None:
        self._last = data.strip().upper()

    def read_until_prompt(self) -> str:
        command = self._last

        if command.startswith("AT"):
            return "ELM327 v1.5" if command == "ATZ" else "OK"
        if command == "0100":
            return "41 00 BE 3F B8 13"
        if command == "03":
            return _build_dtc_response(0x43, self.stored)
        if command == "07":
            return _build_dtc_response(0x47, self.pending)
        if command == "0A":
            return _build_dtc_response(0x4A, self.permanent)
        if command == "04":
            self.stored = []
            self.pending = []
            return "44"
        if command.startswith("14") and len(command) >= 6:
            code = bytes_to_code(int(command[2:4], 16), int(command[4:6], 16))
            self.stored = [c for c in self.stored if c != code]
            self.pending = [c for c in self.pending if c != code]
            return "54"
        return "NO DATA"

    def close(self) -> None:
        pass
