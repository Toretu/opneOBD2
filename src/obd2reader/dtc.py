"""Diagnostic Trouble Code (DTC) encoding, decoding and descriptions.

A DTC is two bytes. The first two bits select the system letter, the next two
bits are the second character, and the remaining nibbles are hex digits:

    byte A: LL SS D3 D3   ->  letter (P/C/B/U), second char (0-3), third char
    byte B: D4 D4 D5 D5   ->  fourth and fifth hex characters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

_LETTERS = "PCBU"
_LETTER_TO_CATEGORY = {letter: index for index, letter in enumerate(_LETTERS)}


@dataclass(frozen=True)
class DTC:
    """A single diagnostic trouble code and its decoded meaning."""

    code: str
    description: str
    status: str  # "stored", "pending" or "permanent"


def bytes_to_code(byte_a: int, byte_b: int) -> str:
    """Decode two raw DTC bytes into a code such as ``P0380``."""

    letter = _LETTERS[(byte_a >> 6) & 0x03]
    second = (byte_a >> 4) & 0x03
    third = byte_a & 0x0F
    fourth = (byte_b >> 4) & 0x0F
    fifth = byte_b & 0x0F
    return f"{letter}{second}{third:X}{fourth:X}{fifth:X}"


def code_to_bytes(code: str) -> tuple[int, int]:
    """Encode a code such as ``P0380`` back into its two raw DTC bytes."""

    code = code.strip().upper()
    if len(code) != 5 or code[0] not in _LETTER_TO_CATEGORY:
        raise ValueError(f"Invalid DTC code: {code!r}")

    category = _LETTER_TO_CATEGORY[code[0]]
    second = int(code[1], 16)
    if second > 3:
        raise ValueError(f"Invalid DTC code: {code!r}")
    third = int(code[2], 16)
    fourth = int(code[3], 16)
    fifth = int(code[4], 16)

    byte_a = (category << 6) | (second << 4) | third
    byte_b = (fourth << 4) | fifth
    return byte_a, byte_b


def _extract_hex(raw: str) -> str:
    """Pull a continuous hex string out of a (possibly multi-line) ELM reply."""

    tokens: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper in ("NO DATA", "NODATA", "UNABLE TO CONNECT", "STOPPED", "OK", "?"):
            continue
        for part in line.split():
            # Drop ISO-TP frame counters such as "0:" or "1:".
            if len(part) >= 2 and part[-1] == ":" and all(
                c in "0123456789ABCDEFabcdef" for c in part[:-1]
            ):
                continue
            tokens.append(part)

    hex_str = "".join(c for c in "".join(tokens) if c in "0123456789ABCDEFabcdef")
    if len(hex_str) % 2:
        hex_str = hex_str[:-1]
    return hex_str.upper()


def parse_dtc_response(raw: str, response_byte: int) -> List[str]:
    """Decode an ELM327 mode 03/07/0A reply into a list of DTC codes.

    ``response_byte`` is the expected service response id (``0x43`` for mode 03,
    ``0x47`` for mode 07, ``0x4A`` for mode 0A). The parser assumes the ISO
    15765-4 (CAN) framing used by modern VW vehicles, where the byte following
    the service id is the number of codes.
    """

    hex_str = _extract_hex(raw)
    if not hex_str:
        return []

    data = bytes.fromhex(hex_str)
    try:
        index = data.index(response_byte)
    except ValueError:
        return []

    payload = data[index + 1:]
    if not payload:
        return []

    # Skip the CAN DTC-count byte, then decode the remaining byte pairs.
    rest = payload[1:]
    codes: List[str] = []
    for i in range(0, len(rest) - 1, 2):
        byte_a, byte_b = rest[i], rest[i + 1]
        if byte_a == 0 and byte_b == 0:
            continue  # padding
        codes.append(bytes_to_code(byte_a, byte_b))
    return codes


# ---------------------------------------------------------------------------
# Human-readable descriptions.
#
# A curated set of common, cross-manufacturer OBD-II codes (standardised SAE
# J2012 definitions) plus a few frequently seen diesel/brake faults. Codes not
# listed here still get a meaningful subsystem-level description from
# `describe()`, so the tool works on any OBD-II vehicle.
# ---------------------------------------------------------------------------

DTC_DATABASE: dict[str, str] = {
    # --- Misfire ----------------------------------------------------------
    "P0300": "Random / multiple cylinder misfire detected",
    "P0301": "Cylinder 1 misfire detected",
    "P0302": "Cylinder 2 misfire detected",
    "P0303": "Cylinder 3 misfire detected",
    "P0304": "Cylinder 4 misfire detected",
    "P0305": "Cylinder 5 misfire detected",
    "P0306": "Cylinder 6 misfire detected",
    # --- Fuel / air metering & oxygen sensors -----------------------------
    "P0171": "System too lean (bank 1)",
    "P0172": "System too rich (bank 1)",
    "P0174": "System too lean (bank 2)",
    "P0175": "System too rich (bank 2)",
    "P0130": "O2 sensor circuit malfunction (bank 1, sensor 1)",
    "P0133": "O2 sensor circuit slow response (bank 1, sensor 1)",
    "P0135": "O2 sensor heater circuit malfunction (bank 1, sensor 1)",
    "P0401": "Exhaust gas recirculation (EGR) flow insufficient",
    "P0402": "Exhaust gas recirculation (EGR) flow excessive",
    "P0404": "EGR control circuit range/performance",
    # --- Catalyst / emissions ---------------------------------------------
    "P0420": "Catalyst system efficiency below threshold (bank 1)",
    "P0430": "Catalyst system efficiency below threshold (bank 2)",
    "P0440": "Evaporative emission (EVAP) system malfunction",
    "P0442": "EVAP system leak detected (small leak)",
    "P0455": "EVAP system leak detected (large leak)",
    # --- Sensors ----------------------------------------------------------
    "P0101": "Mass air flow (MAF) sensor range/performance",
    "P0113": "Intake air temperature sensor circuit high",
    "P0118": "Engine coolant temperature sensor circuit high",
    "P0128": "Coolant thermostat (below regulating temperature)",
    "P0500": "Vehicle speed sensor malfunction",
    # --- Diesel: glow plug / pre-heating ----------------------------------
    "P0380": "Glow plug / heater circuit A malfunction",
    "P0381": "Glow plug / heater indicator circuit malfunction",
    "P0382": "Glow plug / heater circuit B malfunction",
    "P0670": "Glow plug control module circuit malfunction",
    "P0671": "Cylinder 1 glow plug circuit malfunction",
    "P0672": "Cylinder 2 glow plug circuit malfunction",
    "P0673": "Cylinder 3 glow plug circuit malfunction",
    "P0674": "Cylinder 4 glow plug circuit malfunction",
    "P0675": "Cylinder 5 glow plug circuit malfunction",
    "P0676": "Cylinder 6 glow plug circuit malfunction",
    "P0677": "Cylinder 7 glow plug circuit malfunction",
    "P0678": "Cylinder 8 glow plug circuit malfunction",
    # --- Diesel: turbo / fuel / DPF ---------------------------------------
    "P0087": "Fuel rail / system pressure too low",
    "P0088": "Fuel rail / system pressure too high",
    "P0234": "Turbocharger overboost condition",
    "P0299": "Turbocharger underboost (low boost pressure)",
    "P0471": "Exhaust pressure sensor range/performance",
    "P2002": "Diesel particulate filter (DPF) efficiency below threshold",
    "P2015": "Intake manifold runner / flap position sensor (bank 1)",
    "P242F": "Diesel particulate filter restricted - ash accumulation",
    # --- Electrical / transmission ----------------------------------------
    "P0562": "System voltage low",
    "P0563": "System voltage high",
    "P0700": "Transmission control system malfunction",
    "P0705": "Transmission range sensor circuit malfunction",
    # --- ABS / EBS brake system (manufacturer-specific chassis codes) -----
    "C1011": "ABS/EBS control unit fault",
    "C1041": "ABS/EBS wheel speed sensor signal fault",
    "C1200": "ABS/EBS hydraulic pump / solenoid fault",
    "C1611": "EBS/ESP control module supply voltage fault",
    # --- Network / communication ------------------------------------------
    "U0100": "Lost communication with engine control module (ECM)",
    "U0101": "Lost communication with transmission control module (TCM)",
    "U0121": "Lost communication with ABS/EBS control module",
}

_SYSTEM_NAMES = {"P": "Powertrain", "C": "Chassis", "B": "Body", "U": "Network"}

# Powertrain subsystem by the third character of the code (SAE J2012 grouping).
_P_SUBSYSTEMS = {
    "0": "fuel and air metering",
    "1": "fuel and air metering",
    "2": "fuel and air metering (injector circuit)",
    "3": "ignition system or misfire",
    "4": "auxiliary emission controls",
    "5": "vehicle speed, idle control and auxiliary inputs",
    "6": "computer output circuit / auxiliary outputs",
    "7": "transmission",
    "8": "transmission",
    "9": "transmission",
    "A": "hybrid propulsion",
    "B": "hybrid propulsion",
    "C": "hybrid propulsion",
}


def _is_manufacturer_specific(code: str) -> bool:
    letter, second = code[0], code[1]
    if letter == "P":
        if second == "1":
            return True
        if second == "3":  # P30xx-P33xx are manufacturer, P34xx-P39xx generic
            return code[2] in "0123"
        return False
    return second == "1"  # C/B/U: second char 1 = manufacturer-specific


def describe(code: str) -> str:
    """Return a human-readable description for a DTC code.

    Known codes use the curated database; any other valid code falls back to a
    subsystem-level description, so the tool is useful on any OBD-II vehicle.
    """

    code = code.strip().upper()
    if code in DTC_DATABASE:
        return DTC_DATABASE[code]

    letter = code[:1]
    system = _SYSTEM_NAMES.get(letter, "Unknown")
    scope = "manufacturer-specific" if _is_manufacturer_specific(code) else "generic/standard"

    if letter == "P" and len(code) >= 3 and code[2] in _P_SUBSYSTEMS:
        return f"{system} - {_P_SUBSYSTEMS[code[2]]} ({scope} code)"
    return f"{system} ({scope} trouble code)"

