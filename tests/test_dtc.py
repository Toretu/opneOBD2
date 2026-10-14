"""Tests for DTC encoding, decoding, parsing and descriptions."""

import pytest

from obd2reader.dtc import (
    bytes_to_code,
    code_to_bytes,
    describe,
    parse_dtc_response,
)


@pytest.mark.parametrize(
    "byte_a, byte_b, code",
    [
        (0x03, 0x80, "P0380"),  # glow plug circuit A
        (0x06, 0x71, "P0671"),  # cylinder 1 glow plug
        (0x50, 0x11, "C1011"),  # ABS/EBS control unit
        (0xC1, 0x00, "U0100"),  # lost comms with ECM
        (0x20, 0x02, "P2002"),  # DPF efficiency
    ],
)
def test_bytes_to_code(byte_a, byte_b, code):
    assert bytes_to_code(byte_a, byte_b) == code


@pytest.mark.parametrize("code", ["P0380", "P0671", "C1011", "U0100", "B1234"])
def test_code_byte_roundtrip(code):
    byte_a, byte_b = code_to_bytes(code)
    assert bytes_to_code(byte_a, byte_b) == code


def test_code_to_bytes_rejects_invalid():
    with pytest.raises(ValueError):
        code_to_bytes("X0000")
    with pytest.raises(ValueError):
        code_to_bytes("P038")


def test_parse_mode03_multiple_codes():
    raw = "43 05 03 80 06 71 20 02 C1 00 50 11"
    codes = parse_dtc_response(raw, 0x43)
    assert codes == ["P0380", "P0671", "P2002", "U0100", "C1011"]


def test_parse_skips_padding_zeros():
    raw = "43 01 03 80 00 00 00 00"
    assert parse_dtc_response(raw, 0x43) == ["P0380"]


def test_parse_handles_no_data():
    assert parse_dtc_response("NO DATA", 0x43) == []
    assert parse_dtc_response("", 0x43) == []


def test_parse_strips_frame_counters():
    raw = "0: 43 06 03 80 06 71\n1: 20 02 C1 00 50 11 00"
    codes = parse_dtc_response(raw, 0x43)
    assert "P0380" in codes and "C1011" in codes


def test_describe_glow_plug():
    assert "glow plug" in describe("P0380").lower()
    assert "glow plug" in describe("P0671").lower()


def test_describe_ebs():
    assert "ebs" in describe("C1011").lower()


def test_describe_unknown_fallback():
    text = describe("P3456")
    assert "Powertrain" in text
    assert "no detailed description" in text.lower()
