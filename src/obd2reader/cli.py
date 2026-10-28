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


def _build_connection(args: argparse.Namespace):
    if args.mock:
        return MockConnection()
    if getattr(args, "bluetooth", None):
        from .ble import BleConnection

        return BleConnection(args.bluetooth)
    if getattr(args, "spp", None):
        from .spp import RfcommConnection

        return RfcommConnection(args.spp, channel=args.channel)
    if not args.port:
        raise SystemExit(
            "error: --port is required (or use --mock, --bluetooth or --spp). "
            "Run 'obd2 ports', 'obd2 scan' or 'obd2 spp-scan' to list adapters."
        )
    return SerialConnection(args.port, baudrate=args.baud)


def _build_reader(args: argparse.Namespace) -> tuple[OBD2Reader, ELM327]:
    connection = _build_connection(args)
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


def _cmd_spp_scan(_: argparse.Namespace) -> int:
    from .spp import list_paired_spp_devices

    devices = list_paired_spp_devices()
    if not devices:
        print("No paired Bluetooth Classic (SPP) adapters found.")
        print("Pair the adapter first (e.g. with bluetoothctl), then try again.")
        return 0
    print("Paired Bluetooth Classic (SPP) device(s):")
    for address, name in devices:
        print(f"  {address}  {name}")
    print("\nConnect with:  obd2 read --spp <ADDRESS>")
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


def _cmd_fullscan(args: argparse.Namespace) -> int:
    from .uds import scan_modules

    if args.mock:
        from .mockvehicle import MockVehicleConnection, default_mock_vehicle

        connection = MockVehicleConnection(default_mock_vehicle())
    else:
        connection = _build_connection(args)
    print("Scanning control modules (manufacturer UDS)... this can take a minute.")
    try:
        reports = scan_modules(
            connection,
            discover=args.discover,
            active_only=args.active_only,
            brand=args.brand,
        )
    finally:
        connection.close()

    if not reports:
        print("No control modules responded. Is the adapter connected to the car?")
        return 0

    total = 0
    for report in reports:
        print(f"\n### {report.name}  [req {report.request_id:03X}]")
        if not report.dtcs:
            print("    No active codes." if args.active_only else "    No stored codes.")
            continue
        for dtc in report.dtcs:
            total += 1
            mark = "*" if dtc.active else " "
            print(
                f"  {mark} {dtc.code}-{dtc.failure_type:02X}  "
                f"[{dtc.status:02X}: {dtc.status_text}]  {dtc.description}"
            )

    print(
        f"\n{total} code(s) across {len(reports)} responding module(s). "
        "'*' marks confirmed / warning-light codes."
    )
    return 0


def _parse_module_address(value: str) -> int:
    try:
        return int(value, 16)
    except ValueError:
        raise SystemExit(f"error: invalid module address {value!r} (use hex, e.g. 03)")


def _cmd_tp20_read(args: argparse.Namespace) -> int:
    from .canbus import SocketCanBus
    from .tp20 import Tp20Error, read_module_dtcs

    address = _parse_module_address(args.module)
    bus = SocketCanBus(channel=args.channel, bitrate=args.bitrate)
    try:
        dtcs = read_module_dtcs(bus, address)
    except Tp20Error as exc:
        print(f"TP2.0 error: {exc}")
        return 1
    finally:
        bus.close()

    if not dtcs:
        print(f"Module 0x{address:02X}: no stored trouble codes.")
        return 0
    print(f"Module 0x{address:02X}: {len(dtcs)} trouble code(s):\n")
    for dtc in dtcs:
        print(f"  {dtc.label}  (status 0x{dtc.status:02X})")
    return 0


def _cmd_tp20_clear(args: argparse.Namespace) -> int:
    from .canbus import SocketCanBus
    from .tp20 import Tp20Error, clear_module_dtcs

    address = _parse_module_address(args.module)
    bus = SocketCanBus(channel=args.channel, bitrate=args.bitrate)
    try:
        ok = clear_module_dtcs(bus, address)
    except Tp20Error as exc:
        print(f"TP2.0 error: {exc}")
        return 1
    finally:
        bus.close()

    print("Codes cleared." if ok else "Clear request was not acknowledged.")
    return 0 if ok else 1


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
        "--spp",
        metavar="ADDRESS",
        help="Bluetooth Classic adapter MAC (direct SPP/RFCOMM; run 'obd2 spp-scan')",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=None,
        help="RFCOMM channel for --spp (default: auto-detect, else 1)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the built-in simulated vehicle instead of real hardware",
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="obd2",
        description="Read and clear OBD-II trouble codes from any vehicle (Bluetooth/serial ELM327).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ports_parser = subparsers.add_parser("ports", help="List available serial ports")
    ports_parser.set_defaults(func=_cmd_ports)

    scan_parser = subparsers.add_parser("scan", help="Scan for nearby Bluetooth LE adapters")
    scan_parser.add_argument(
        "--timeout", type=float, default=8.0, help="Scan duration in seconds (default 8)"
    )
    scan_parser.set_defaults(func=_cmd_scan)

    spp_scan_parser = subparsers.add_parser(
        "spp-scan", help="List paired Bluetooth Classic (SPP) adapters"
    )
    spp_scan_parser.set_defaults(func=_cmd_spp_scan)

    read_parser = subparsers.add_parser("read", help="Read all trouble codes")
    _add_connection_args(read_parser)
    read_parser.set_defaults(func=_cmd_read)

    fullscan_parser = subparsers.add_parser(
        "fullscan",
        help="Read DTCs from all control modules (ABS, airbag, etc.) via manufacturer UDS",
    )
    _add_connection_args(fullscan_parser)
    fullscan_parser.add_argument(
        "--active-only",
        action="store_true",
        help="Show only confirmed / warning-light codes (hide passive history)",
    )
    fullscan_parser.add_argument(
        "--discover",
        action="store_true",
        help="Probe a wider address range to find modules not in the built-in map",
    )
    fullscan_parser.add_argument(
        "--brand",
        choices=["vag", "kia"],
        default="vag",
        help="Module address map to use (kia = Kia/Hyundai EV, e.g. Soul EV)",
    )
    fullscan_parser.set_defaults(func=_cmd_fullscan)

    tp20_read_parser = subparsers.add_parser(
        "tp20-read",
        help="Read DTCs from a VAG module over VW TP2.0 (needs a raw-CAN adapter, [can] extra)",
    )
    tp20_read_parser.add_argument("--channel", default="can0", help="SocketCAN interface (default can0)")
    tp20_read_parser.add_argument("--bitrate", type=int, default=500000, help="CAN bitrate (default 500000)")
    tp20_read_parser.add_argument("--module", required=True, metavar="ADDR",
                                  help="Module address in hex (e.g. 03 for ABS, 15 for airbag)")
    tp20_read_parser.set_defaults(func=_cmd_tp20_read)

    tp20_clear_parser = subparsers.add_parser(
        "tp20-clear", help="Clear DTCs in a VAG module over VW TP2.0 (needs a raw-CAN adapter)",
    )
    tp20_clear_parser.add_argument("--channel", default="can0", help="SocketCAN interface (default can0)")
    tp20_clear_parser.add_argument("--bitrate", type=int, default=500000, help="CAN bitrate (default 500000)")
    tp20_clear_parser.add_argument("--module", required=True, metavar="ADDR",
                                   help="Module address in hex (e.g. 03 for ABS)")
    tp20_clear_parser.set_defaults(func=_cmd_tp20_clear)

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
