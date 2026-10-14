"""Tests for OBD2Reader, including full integration against the mock vehicle."""

from obd2reader.elm327 import ELM327
from obd2reader.mock import MockConnection
from obd2reader.reader import OBD2Reader


def make_reader():
    elm = ELM327(MockConnection())
    elm.connect()
    return OBD2Reader(elm)


def test_read_dtcs_returns_all_faults():
    reader = make_reader()
    codes = {dtc.code for dtc in reader.read_dtcs()}

    # Glow plug, EBS/ABS and other VW faults are all surfaced.
    assert {"P0380", "P0671", "C1011", "P2002", "U0100", "P0401"} <= codes


def test_read_dtcs_includes_descriptions_and_status():
    reader = make_reader()
    by_code = {dtc.code: dtc for dtc in reader.read_dtcs()}

    assert "glow plug" in by_code["P0380"].description.lower()
    assert "ebs" in by_code["C1011"].description.lower()
    assert by_code["P0380"].status == "stored"
    assert by_code["P0401"].status == "pending"


def test_clear_all_removes_stored_and_pending():
    reader = make_reader()
    assert reader.clear_all() is True
    assert reader.read_dtcs() == []


def test_clear_selected_code():
    reader = make_reader()
    cleared = reader.clear_dtcs(["P0380"])

    assert cleared == ["P0380"]
    remaining = {dtc.code for dtc in reader.read_dtcs()}
    assert "P0380" not in remaining
    assert "C1011" in remaining  # other codes are untouched


class CapturingELM:
    """Captures queries so we can assert the exact UDS command formatting."""

    def __init__(self):
        self.queries = []

    def query(self, command):
        self.queries.append(command)
        return "54"  # positive UDS response


def test_clear_selected_sends_correct_uds_command():
    elm = CapturingELM()
    reader = OBD2Reader(elm)

    reader.clear_dtcs(["P0380"])
    # Service 0x14 + DTC bytes 03 80 + failure-type 0xFF.
    assert elm.queries == ["140380FF"]
