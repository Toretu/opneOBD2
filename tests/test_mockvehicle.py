"""Tests for the configurable UDS-aware mock vehicle."""

from obd2reader.elm327 import ELM327
from obd2reader.mock import MockConnection
from obd2reader.mockvehicle import (
    FAULT_CATALOG,
    STATUS_ACTIVE,
    STATUS_PASSIVE,
    MockFault,
    MockModule,
    MockVehicle,
    MockVehicleConnection,
    default_mock_vehicle,
    vehicle_from_selection,
)
from obd2reader.reader import OBD2Reader
from obd2reader import uds


def _vehicle_with(*modules: MockModule) -> MockVehicle:
    return MockVehicle(modules=list(modules))


def test_scan_reads_dtcs_from_multiple_modules():
    vehicle = _vehicle_with(
        MockModule(0x7E0, "Engine", [MockFault("P0380", 0x13, STATUS_ACTIVE)]),
        MockModule(0x713, "ABS / ESP brakes", [MockFault("C1011", 0x08, STATUS_ACTIVE)]),
    )
    conn = MockVehicleConnection(vehicle)

    reports = uds.scan_modules(conn, request_ids=[0x7E0, 0x713, 0x715])
    by_id = {r.request_id: r for r in reports}

    assert by_id[0x7E0].dtcs[0].code == "P0380"
    assert by_id[0x7E0].dtcs[0].failure_type == 0x13
    assert by_id[0x7E0].dtcs[0].active is True
    assert by_id[0x713].dtcs[0].code == "C1011"
    # A module queried but not present in the vehicle simply doesn't respond.
    assert 0x715 not in by_id


def test_passive_fault_is_not_active():
    vehicle = _vehicle_with(
        MockModule(0x7E0, "Engine", [MockFault("P0401", 0x00, STATUS_PASSIVE)]),
    )
    report = uds.scan_modules(MockVehicleConnection(vehicle), request_ids=[0x7E0])[0]
    assert report.dtcs[0].active is False


def test_multiframe_many_faults_roundtrip():
    # 10 faults forces ISO-TP multi-frame; all must decode back correctly.
    faults = [MockFault(f"C10{n:02d}", n, STATUS_ACTIVE) for n in range(10)]
    vehicle = _vehicle_with(MockModule(0x713, "ABS", faults))

    report = uds.scan_modules(MockVehicleConnection(vehicle), request_ids=[0x713])[0]
    assert len(report.dtcs) == 10
    assert [d.failure_type for d in report.dtcs] == list(range(10))


def test_active_only_filters_passive():
    vehicle = _vehicle_with(
        MockModule(0x7E0, "Engine", [
            MockFault("P0380", 0x13, STATUS_ACTIVE),
            MockFault("P0401", 0x00, STATUS_PASSIVE),
        ]),
    )
    report = uds.scan_modules(
        MockVehicleConnection(vehicle), request_ids=[0x7E0], active_only=True
    )[0]
    assert [d.code for d in report.dtcs] == ["P0380"]


def test_clear_module_dtcs_removes_faults():
    vehicle = _vehicle_with(
        MockModule(0x713, "ABS", [MockFault("C1011", 0x08), MockFault("C1041", 0x02)]),
    )
    conn = MockVehicleConnection(vehicle)

    assert uds.clear_module_dtcs(conn, 0x713) is True
    report = uds.scan_modules(conn, request_ids=[0x713])[0]
    assert report.dtcs == []


def test_legacy_mode03_reflects_engine_faults():
    vehicle = _vehicle_with(
        MockModule(0x7E0, "Engine", [MockFault("P0380", 0x13, STATUS_ACTIVE)]),
    )
    elm = ELM327(MockVehicleConnection(vehicle))
    elm.connect()
    reader = OBD2Reader(elm)
    codes = {d.code for d in reader.read_dtcs()}
    assert "P0380" in codes


def test_mode04_clears_engine():
    vehicle = _vehicle_with(
        MockModule(0x7E0, "Engine", [MockFault("P0380", 0x13, STATUS_ACTIVE)]),
    )
    elm = ELM327(MockVehicleConnection(vehicle))
    elm.connect()
    reader = OBD2Reader(elm)
    assert reader.clear_all() is True
    assert reader.read_dtcs() == []


def test_default_vehicle_has_active_faults():
    vehicle = default_mock_vehicle()
    assert vehicle.total_faults() > 0


def test_vehicle_from_selection_includes_empty_modules():
    selection = {0x713: [FAULT_CATALOG[0x713][0]]}
    vehicle = vehicle_from_selection(selection)

    abs_module = vehicle.module(0x713)
    assert abs_module is not None and len(abs_module.faults) == 1
    # Other catalog modules are present but empty (shown as OK).
    engine = vehicle.module(0x7E0)
    assert engine is not None and engine.faults == []


def test_plain_mock_connection_still_works():
    # The original single-ECU stub remains intact.
    elm = ELM327(MockConnection())
    elm.connect()
    codes = {d.code for d in OBD2Reader(elm).read_dtcs()}
    assert "P0380" in codes
