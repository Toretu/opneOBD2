"""Command-line interface for the OBD-II reader."""

from __future__ import annotations

import argparse
import sys
from typing import List

from .elm327 import ELM327, SerialConnection
from .mock import MockConnection
from .reader import OBD2Reader


def _list_ports() -> List[str]:
    from serial.tools import list_ports  # lazy import; only needed for real hardware

    return [port.device for port in list_ports.comports()]


def _build_reader(args: argparse.Namespace) -> tuple[OBD2Reader, ELM327]:
    if args.mock:
        connection = MockConnection()
    elif getattr(args, "bluetooth", None):
        from .ble import BleConnection

        connection = BleConnection(args.bluetooth)
    else:
        if not args.port:
            raise SystemExit(
                "error: --port is required (or use --mock or --bluetooth). "
                "Run 'obd2 ports' or 'obd2 scan' to list adapters."
            )
        connection = SerialConnection(args.port, baudrate=args.baud)

    elm = ELM327(connection)
    elm.connect()
    return OBD2Reader(elm), elm


def _cmd_ports(_: argparse.Namespace) -> int:
    ports = _list_ports()
    if not ports:
        print("No serial ports found. Pair your Bluetooth ELM327 adapter first.")
        print("  Linux: sudo rfcomm bind 0 <adapter-MAC>  ->  /dev/rfcomm0")
        print("  Windows: pair in Bluetooth settings  ->  a COM port is created")
        return 0
    print("Available serial ports:")
    for port in ports:
        print(f"  {port}")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    from .ble import scan_bluetooth

    print(f"Scanning for Bluetooth LE devices ({args.timeout:.0f}s)...")
    devices = scan_bluetooth(args.timeout)
    if not devices:
        print("No Bluetooth LE devices found. Make sure the adapter is powered and nearby.")
        return 0
    print("Found Bluetooth LE device(s):")
    for address, name in devices:
        print(f"  {address}  {name}")
    print("\nConnect with:  obd2 read --bluetooth <ADDRESS>")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    reader, elm = _build_reader(args)
    try:
        dtcs = reader.read_dtcs()
    finally:
        elm.close()

    if not dtcs:
        print("No diagnostic trouble codes found. The vehicle reports no faults.")
        return 0

    print(f"Found {len(dtcs)} diagnostic trouble code(s):\n")
    for dtc in dtcs:
        print(f"  [{dtc.status:>9}] {dtc.code}  {dtc.description}")
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    reader, elm = _build_reader(args)
    try:
        if args.codes:
            cleared = reader.clear_dtcs(args.codes)
            if cleared:
                print("Cleared code(s): " + ", ".join(cleared))
            not_cleared = [c.upper() for c in args.codes if c.upper() not in cleared]
            if not_cleared:
                print("Could not clear: " + ", ".join(not_cleared))
            return 0 if cleared else 1

        ok = reader.clear_all()
        print("All codes cleared." if ok else "Clear request was not acknowledged.")
        return 0 if ok else 1
    finally:
        elm.close()


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", help="Serial/Bluetooth port (e.g. COM5 or /dev/rfcomm0)")
    parser.add_argument("--baud", type=int, default=38400, help="Baud rate (default 38400)")
    parser.add_argument(
        "--bluetooth",
        metavar="ADDRESS",
        help="BLE adapter address/MAC (direct Bluetooth Low Energy; run 'obd2 scan')",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the built-in simulated vehicle instead of real hardware",
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="obd2",
        description="Read and clear OBD-II trouble codes from a VW Sharan (Bluetooth ELM327).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ports_parser = subparsers.add_parser("ports", help="List available serial ports")
    ports_parser.set_defaults(func=_cmd_ports)

    scan_parser = subparsers.add_parser("scan", help="Scan for nearby Bluetooth LE adapters")
    scan_parser.add_argument(
        "--timeout", type=float, default=8.0, help="Scan duration in seconds (default 8)"
    )
    scan_parser.set_defaults(func=_cmd_scan)

    read_parser = subparsers.add_parser("read", help="Read all trouble codes")
    _add_connection_args(read_parser)
    read_parser.set_defaults(func=_cmd_read)

    clear_parser = subparsers.add_parser(
        "clear", help="Clear all codes, or specific codes with --codes"
    )
    _add_connection_args(clear_parser)
    clear_parser.add_argument(
        "--codes",
        nargs="+",
        metavar="CODE",
        help="Specific codes to clear (e.g. P0380 C1011); omit to clear all",
    )
    clear_parser.set_defaults(func=_cmd_clear)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
