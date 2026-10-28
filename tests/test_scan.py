"""Tests for VAG DTC descriptions, the TP2.0 multi-module scan and scanners."""

import pytest

from obd2reader import tp20, kwp
from obd2reader.canbus import FakeCanBus
from obd2reader.mockvehicle import MockModule, MockVehicle, MockFault, MockVehicleConnection
from obd2reader.scan import (
    Tp20Scanner,
    UdsScanner,
    VagModuleDtc,
    scan_tp20_modules,
)
from obd2reader.vagdtc import describe_vag


# -- VAG DTC descriptions --------------------------------------------------

def test_describe_known_vag_code():
    assert "wheel speed sensor" in describe_vag(283).lower()


def test_describe_unknown_vag_code_falls_back():
    assert "no description" in describe_vag(65000).lower()


def test_vag_module_dtc_attributes():
    dtc = VagModuleDtc(number=283, status=0xE0)
    assert dtc.code == "VAG 00283"
    assert dtc.failure_type == 0
    assert dtc.active is True
    assert "wheel speed sensor" in dtc.description.lower()


# -- TP2.0 multi-module scan against simulated ECUs ------------------------

def _multi_ecu_bus(dtc_map, rejected_addresses=()):
    """Build a FakeCanBus carrying one FakeTp20Ecu per address in dtc_map."""

    ecus = []
    for address, dtcs in dtc_map.items():
        def make_handler(dtcs, address):
            def handler(request):
                if request[0] == kwp.SVC_START_DIAGNOSTIC_SESSION:
                    return bytes([0x50, request[1]])
                if address in rejected_addresses and request[0] == kwp.SVC_READ_DTC_BY_STATUS:
                    return bytes([0x7F, request[0], 0x12])
                if request[0] == kwp.SVC_READ_DTC_BY_STATUS:
                    body = bytearray([0x58, len(dtcs)])
                    for number, status in dtcs:
                        body += bytes([(number >> 8) & 0xFF, number & 0xFF, status])
                    return bytes(body)
                if request[0] == kwp.SVC_CLEAR_DTC:
                    return bytes([0x54])
                return bytes([0x7F, request[0], 0x11])
            return handler
        ecus.append(tp20.FakeTp20Ecu(address, make_handler(dtcs, address)))

    class MultiResponder:
        def handle(self, frame):
            replies = []
            for ecu in ecus:
                replies.extend(ecu.handle(frame))
            return replies

    return FakeCanBus(responder=MultiResponder())


def test_scan_tp20_modules_reports_responders_only():
    bus = _multi_ecu_bus({0x03: [(283, 0xE0)], 0x15: [(588, 0xA0)]})
    reports = scan_tp20_modules(bus, addresses=[0x03, 0x15, 0x44])
    by_id = {r.request_id: r for r in reports}

    assert set(by_id) == {0x03, 0x15}  # 0x44 absent -> skipped
    assert by_id[0x03].name == "ABS / ESP brakes"
    assert by_id[0x03].dtcs[0].code == "VAG 00283"
    assert by_id[0x15].dtcs[0].code == "VAG 00588"


def test_scan_tp20_modules_continues_after_unsupported_request():
    bus = _multi_ecu_bus(
        {0x03: [], 0x15: [(588, 0xA0)]}, rejected_addresses={0x03}
    )

    reports = scan_tp20_modules(bus, addresses=[0x03, 0x15])

    assert [report.request_id for report in reports] == [0x15]
    assert reports[0].dtcs[0].code == "VAG 00588"


def test_scan_tp20_modules_reports_rejections_when_none_are_readable():
    bus = _multi_ecu_bus({0x03: []}, rejected_addresses={0x03})

    with pytest.raises(tp20.Tp20Error, match=r"Probe failures: ABS / ESP brakes \(0x03\).*NRC 0x12"):
        scan_tp20_modules(bus, addresses=[0x03])


def test_scan_tp20_modules_reports_setup_failures_when_none_respond():
    bus = _multi_ecu_bus({})

    with pytest.raises(tp20.Tp20Error, match=r"Engine \(0x01\).*no TP2\.0 setup response"):
        scan_tp20_modules(bus, addresses=[0x01])


def test_tp20_scanner_scan_and_clear():
    bus = _multi_ecu_bus({0x03: [(283, 0xE0)]})
    scanner = Tp20Scanner(bus, addresses=[0x03])

    reports = scanner.scan()
    assert reports[0].dtcs[0].number == 283
    assert scanner.clear(0x03) is True


# -- UdsScanner over the mock vehicle (same interface) ---------------------

def test_uds_scanner_over_mock_vehicle():
    vehicle = MockVehicle(modules=[
        MockModule(0x7E0, "Engine", [MockFault("P0380", 0x13)]),
        MockModule(0x713, "ABS / ESP brakes", [MockFault("C1011", 0x08)]),
    ])
    scanner = UdsScanner(MockVehicleConnection(vehicle))
    reports = scanner.scan()
    by_id = {r.request_id: r for r in reports}
    assert by_id[0x7E0].dtcs[0].code == "P0380"
    assert scanner.clear(0x713) is True


def test_scanner_interface_is_uniform():
    # Both scanners expose scan/clear/close with the same report shape.
    bus = _multi_ecu_bus({0x03: [(283, 0xE0)]})
    tp = Tp20Scanner(bus, addresses=[0x03])
    mock = UdsScanner(MockVehicleConnection(
        MockVehicle(modules=[MockModule(0x713, "ABS", [MockFault("C1011", 0x08)])])))

    for scanner in (tp, mock):
        reports = scanner.scan()
        assert reports and hasattr(reports[0].dtcs[0], "code")
        assert hasattr(reports[0].dtcs[0], "active")
        scanner.close()
