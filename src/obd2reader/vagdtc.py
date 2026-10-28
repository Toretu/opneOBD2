"""Human-readable descriptions for common VAG (KWP2000/TP2.0) trouble codes.

VAG modules reached over VW TP2.0 report DTCs as a 2-byte number, conventionally
shown as a 5-digit decimal (e.g. ``00668``). The full mapping is VW proprietary;
this is a curated subset of frequently seen codes. Unknown numbers fall back to a
generic description so the tool stays useful on any VAG module.
"""

from __future__ import annotations

# VAG DTC number (decimal) -> description.
VAG_DTC_DATABASE: dict[int, str] = {
    # --- Power supply / general ------------------------------------------
    532: "Supply voltage B+ terminal 30",
    668: "Supply voltage terminal 30",
    928: "Supply voltage terminal 15",
    1044: "Control module incorrectly coded",
    1312: "Powertrain data bus (CAN) malfunction",
    1314: "Engine control module no communication",
    1316: "ABS control module no communication",
    1321: "Steering wheel electronics no communication",
    1324: "Control module fault",
    # --- ABS / brakes (address 03) ---------------------------------------
    283: "ABS wheel speed sensor front left (G47)",
    285: "ABS wheel speed sensor front right (G45)",
    287: "ABS wheel speed sensor rear left (G46)",
    289: "ABS wheel speed sensor rear right (G44)",
    290: "ABS wheel speed sensor signal implausible",
    480: "ABS hydraulic pump (V64)",
    526: "Brake light switch (F)",
    1130: "ABS operation / function restricted",
    1435: "Brake pressure sensor 1 (G201)",
    1495: "ESP / steering angle sensor (G85) not calibrated",
    # --- Airbag / SRS (address 15) ---------------------------------------
    588: "Airbag igniter; driver side (N95)",
    589: "Airbag igniter; passenger side (N131)",
    912: "Seat belt tensioner; driver side (N153)",
    1217: "Side airbag igniter; driver side (N199)",
    1218: "Side airbag igniter; passenger side (N200)",
    # --- Steering (address 44) -------------------------------------------
    1509: "Power steering control module fault",
    1511: "Steering angle sensor (G85)",
    # --- Instruments (address 17) ----------------------------------------
    2240: "Instrument cluster control module fault",
}


def describe_vag(number: int) -> str:
    """Return a description for a VAG DTC number, with a generic fallback."""

    if number in VAG_DTC_DATABASE:
        return VAG_DTC_DATABASE[number]
    return "Manufacturer-specific fault (no description available)"
