"""High-level OBD-II operations: reading and clearing trouble codes."""

from __future__ import annotations

from typing import Iterable, List

from .dtc import DTC, code_to_bytes, describe, parse_dtc_response
from .elm327 import ELM327


class OBD2Reader:
    """Reads and clears diagnostic trouble codes through an ELM327 adapter."""

    # command -> (status label, expected service response byte)
    _READ_MODES = {
        "03": ("stored", 0x43),
        "07": ("pending", 0x47),
        "0A": ("permanent", 0x4A),
    }

    def __init__(self, elm: ELM327) -> None:
        self._elm = elm

    def read_dtcs(self) -> List[DTC]:
        """Read stored, pending and permanent DTCs from the vehicle."""

        results: List[DTC] = []
        for command, (status, response_byte) in self._READ_MODES.items():
            raw = self._elm.query(command)
            for code in parse_dtc_response(raw, response_byte):
                results.append(DTC(code=code, description=describe(code), status=status))
        return results

    def clear_all(self) -> bool:
        """Clear all emission-related DTCs (OBD-II mode 04).

        Returns ``True`` when the adapter reports a positive response (``44``).
        """

        raw = self._elm.query("04")
        return "44" in raw.replace(" ", "").upper()

    def clear_dtcs(self, codes: Iterable[str]) -> List[str]:
        """Attempt to clear individual codes via UDS ClearDiagnosticInformation.

        Standard OBD-II (mode 04) can only clear *all* codes at once. Clearing a
        single code uses the manufacturer UDS service ``0x14`` and is best-effort:
        it depends on the vehicle/ECU accepting the request. Returns the list of
        codes the adapter confirmed as cleared (positive response ``54``).
        """

        cleared: List[str] = []
        for code in codes:
            byte_a, byte_b = code_to_bytes(code)
            # Service 0x14 + 3-byte groupOfDTC (code bytes + "all failure types").
            command = f"14{byte_a:02X}{byte_b:02X}FF"
            raw = self._elm.query(command)
            if "54" in raw.replace(" ", "").upper():
                cleared.append(code.strip().upper())
        return cleared
