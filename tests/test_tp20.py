"""Tests for the CAN bus abstraction, KWP2000 codec and VW TP2.0 channel."""

import pytest

from obd2reader import kwp, tp20
from obd2reader.canbus import CanFrame, FakeCanBus


# -- CanFrame / FakeCanBus -------------------------------------------------

def test_fake_bus_delivers_responder_frames():
    def echo(frame):
        return [CanFrame(frame.arbitration_id + 1, frame.data)]

    bus = FakeCanBus(responder=type("R", (), {"handle": staticmethod(echo)})())
    bus.send(CanFrame(0x100, b"\x01\x02"))
    out = bus.recv(0.1)
    assert out == CanFrame(0x101, b"\x01\x02")


# -- KWP2000 codec ---------------------------------------------------------

def test_build_read_dtcs_default_all():
    assert kwp.build_read_dtcs() == bytes([0x18, 0x02, 0xFF, 0x00])


def test_build_start_session_default_vag_session():
    assert kwp.build_start_session() == bytes([0x10, 0x89])


def test_parse_start_session_positive():
    assert kwp.parse_start_session(bytes([0x50, 0x89])) is None


def test_build_clear_dtcs_default_all():
    assert kwp.build_clear_dtcs() == bytes([0x14, 0xFF, 0x00])


def test_parse_read_dtcs_decodes_records():
    # 58 02 | 00 10 EA | 02 9A 24
    response = bytes([0x58, 0x02, 0x00, 0x10, 0xEA, 0x02, 0x9A, 0x24])
    dtcs = kwp.parse_read_dtcs(response)
    assert [(d.number, d.status) for d in dtcs] == [(0x0010, 0xEA), (0x029A, 0x24)]
    assert dtcs[0].label == "VAG 00016"


def test_parse_read_dtcs_empty():
    assert kwp.parse_read_dtcs(bytes([0x58, 0x00])) == []


def test_parse_clear_dtcs_positive():
    assert kwp.parse_clear_dtcs(bytes([0x54])) is True


def test_parse_negative_response_raises():
    with pytest.raises(kwp.KwpError):
        kwp.parse_read_dtcs(bytes([0x7F, 0x18, 0x11]))


# -- TP2.0 framing (pure) --------------------------------------------------

def test_build_telegram_prefixes_length():
    assert tp20.build_telegram(b"\x58\x00") == bytes([0x00, 0x02, 0x58, 0x00])


def test_decode_t3_timing_parameter():
    assert tp20._decode_timing_parameter(0x4A) == pytest.approx(0.01)


def test_encode_decode_single_frame_roundtrip():
    frames = tp20.encode_data_frames(bytes([0x54]))
    assert len(frames) == 1
    assert tp20.is_last(frames[0])
    assert tp20.decode_telegram(frames) == bytes([0x54])


def test_encode_decode_multiframe_roundtrip():
    payload = bytes(range(20))  # forces several frames
    frames = tp20.encode_data_frames(payload)
    assert len(frames) > 1
    assert not tp20.is_last(frames[0])
    assert tp20.is_last(frames[-1])
    assert tp20.decode_telegram(frames) == payload


# -- TP2.0 channel against a simulated ECU ---------------------------------

def _ecu_bus(address, dtcs=(), cleared_flag=None):
    def handler(request: bytes) -> bytes:
        if request[0] == kwp.SVC_START_DIAGNOSTIC_SESSION:
            return bytes([0x50, request[1]])
        if request[0] == kwp.SVC_READ_DTC_BY_STATUS:
            body = bytearray([0x58, len(dtcs)])
            for number, status in dtcs:
                body += bytes([(number >> 8) & 0xFF, number & 0xFF, status])
            return bytes(body)
        if request[0] == kwp.SVC_CLEAR_DTC:
            if cleared_flag is not None:
                cleared_flag.append(True)
            return bytes([0x54])
        return bytes([0x7F, request[0], 0x11])

    ecu = tp20.FakeTp20Ecu(address, handler)
    return FakeCanBus(responder=ecu)


def test_channel_open_assigns_ids():
    bus = _ecu_bus(0x03)
    channel = tp20.Tp20Channel(bus, 0x03)
    channel.open()
    assert channel._tx_id == 0x303
    assert channel._rx_id == 0x383


def test_read_module_dtcs_end_to_end():
    bus = _ecu_bus(0x03, dtcs=[(0x0290, 0x24), (0x0101, 0xE0)])
    dtcs = tp20.read_module_dtcs(bus, 0x03)
    assert [(d.number, d.status) for d in dtcs] == [(0x0290, 0x24), (0x0101, 0xE0)]


def test_read_module_dtcs_starts_session_before_read():
    requests = []
    delays = []
    original_sleep = tp20.time.sleep
    tp20.time.sleep = delays.append

    def handler(request):
        requests.append(request)
        if request[0] == kwp.SVC_START_DIAGNOSTIC_SESSION:
            return bytes([0x50, request[1]])
        return bytes([0x58, 0x00])

    try:
        bus = FakeCanBus(responder=tp20.FakeTp20Ecu(0x03, handler))

        assert tp20.read_module_dtcs(bus, 0x03) == []
        assert requests == [kwp.build_start_session(), kwp.build_read_dtcs()]
        acknowledgements = [
            frame.data[0]
            for frame in bus.sent
            if frame.data and frame.data[0] >> 4 == tp20.OP_ACK_READY
        ]
        assert acknowledgements[0] == 0xB1
        assert delays and all(delay == pytest.approx(0.01) for delay in delays)
    finally:
        tp20.time.sleep = original_sleep


def test_read_module_dtcs_multiframe():
    many = [(0x0100 + n, 0x24) for n in range(8)]  # response spans multiple frames
    bus = _ecu_bus(0x03, dtcs=many)
    dtcs = tp20.read_module_dtcs(bus, 0x03)
    assert [d.number for d in dtcs] == [0x0100 + n for n in range(8)]


def test_clear_module_dtcs_end_to_end():
    flag = []
    bus = _ecu_bus(0x15, cleared_flag=flag)
    assert tp20.clear_module_dtcs(bus, 0x15) is True
    assert flag == [True]


def test_open_raises_when_module_absent():
    bus = _ecu_bus(0x03)  # ECU at 0x03, we address 0x44
    channel = tp20.Tp20Channel(bus, 0x44)
    with pytest.raises(tp20.Tp20Error, match=r"CAN trace: TX 200: 44 C0 00 10 00 03 01"):
        channel.open()
