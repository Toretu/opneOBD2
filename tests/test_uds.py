"""Tests for UDS service 0x19 DTC decoding and ISO-TP reassembly."""

from obd2reader.uds import (
    ModuleDTC,
    decode_dtc_records,
    reassemble_isotp,
    response_id_for,
)


def test_response_id_for_powertrain():
    assert response_id_for(0x7E0) == 0x7E8
    assert response_id_for(0x7E1) == 0x7E9


def test_response_id_for_vag_offset():
    assert response_id_for(0x714) == 0x77E
    assert response_id_for(0x715) == 0x77F
    assert response_id_for(0x710) == 0x77A


def test_decode_dtc_records_empty():
    # 59 02 <mask> with no records
    assert decode_dtc_records(bytes([0x59, 0x02, 0xFF])) == []


def test_decode_dtc_records_rejects_wrong_service():
    assert decode_dtc_records(bytes([0x43, 0x00])) == []


def test_decode_dtc_records_single():
    # 59 02 FF | DTC 00 10 EA | status 08 (confirmed)
    message = bytes([0x59, 0x02, 0xFF, 0x00, 0x10, 0xEA, 0x08])
    dtcs = decode_dtc_records(message)
    assert len(dtcs) == 1
    dtc = dtcs[0]
    assert dtc.code == "P0010"
    assert dtc.failure_type == 0xEA
    assert dtc.status == 0x08
    assert dtc.active is True


def test_decode_dtc_records_status_and_active_flag():
    # status 0x50 = not-completed flags only -> not active
    message = bytes([0x59, 0x02, 0xFF, 0x41, 0x20, 0x00, 0x50])
    dtc = decode_dtc_records(message)[0]
    assert dtc.code == "C0120"
    assert dtc.active is False
    assert "not completed this cycle" in dtc.status_text


def test_decode_dtc_records_skips_padding():
    message = bytes(
        [0x59, 0x02, 0xFF, 0x00, 0x10, 0xEA, 0x08, 0x00, 0x00, 0x00, 0x00]
    )
    assert len(decode_dtc_records(message)) == 1


def test_reassemble_single_frame():
    raw = "77F 05 59 02 FF 00 \r>"
    message = reassemble_isotp(raw, 0x77F)
    assert message == bytes([0x59, 0x02, 0xFF, 0x00])


def test_reassemble_multi_frame():
    # First frame declares 11 (0x00B) bytes; payload split across frames.
    raw = (
        "77E 10 0B 59 02 FF 00 10 EA\r"
        "77E 21 08 41 20 00 50 00 00\r>"
    )
    message = reassemble_isotp(raw, 0x77E)
    assert message is not None
    assert message[:3] == bytes([0x59, 0x02, 0xFF])
    assert len(message) == 0x0B
    dtcs = decode_dtc_records(message)
    assert [d.code for d in dtcs] == ["P0010", "C0120"]


def test_reassemble_ignores_negative_response():
    # 7F 19 78 (response pending) then the real positive response.
    raw = (
        "77F 03 7F 19 78\r"
        "77F 05 59 02 FF 00 \r>"
    )
    message = reassemble_isotp(raw, 0x77F)
    assert message == bytes([0x59, 0x02, 0xFF, 0x00])


def test_reassemble_returns_none_without_match():
    assert reassemble_isotp("NO DATA\r>", 0x77D) is None
