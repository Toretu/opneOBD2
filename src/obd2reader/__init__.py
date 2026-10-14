"""Cross-platform OBD-II diagnostic reader for any vehicle (Bluetooth/serial ELM327)."""

from .dtc import DTC, describe, parse_dtc_response, bytes_to_code, code_to_bytes
from .elm327 import ELM327, SerialConnection
from .reader import OBD2Reader
from .mock import MockConnection
from .ble import BleConnection, scan_bluetooth

__all__ = [
    "DTC",
    "describe",
    "parse_dtc_response",
    "bytes_to_code",
    "code_to_bytes",
    "ELM327",
    "SerialConnection",
    "OBD2Reader",
    "MockConnection",
    "BleConnection",
    "scan_bluetooth",
]
