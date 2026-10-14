"""Tests for the BLE helper logic that does not require hardware or bleak."""

import threading

import pytest

from obd2reader.ble import _NotificationBuffer, select_characteristics


def test_select_characteristics_prefers_known_hm10():
    chars = [
        ("0000ffe1-0000-1000-8000-00805f9b34fb", ["write-without-response", "notify"]),
        ("00002a00-0000-1000-8000-00805f9b34fb", ["read"]),
    ]
    write, notify = select_characteristics(chars)
    assert write == "0000ffe1-0000-1000-8000-00805f9b34fb"
    assert notify == "0000ffe1-0000-1000-8000-00805f9b34fb"


def test_select_characteristics_split_write_notify():
    chars = [
        ("0000fff1-0000-1000-8000-00805f9b34fb", ["notify"]),
        ("0000fff2-0000-1000-8000-00805f9b34fb", ["write"]),
    ]
    write, notify = select_characteristics(chars)
    assert write == "0000fff2-0000-1000-8000-00805f9b34fb"
    assert notify == "0000fff1-0000-1000-8000-00805f9b34fb"


def test_select_characteristics_generic_fallback():
    chars = [
        ("12345678-0000-1000-8000-00805f9b34fb", ["write", "notify"]),
    ]
    write, notify = select_characteristics(chars)
    assert write == "12345678-0000-1000-8000-00805f9b34fb"
    assert notify == "12345678-0000-1000-8000-00805f9b34fb"


def test_select_characteristics_raises_without_match():
    with pytest.raises(RuntimeError):
        select_characteristics([("0000abcd-0000-1000-8000-00805f9b34fb", ["read"])])


def test_notification_buffer_returns_text_up_to_prompt():
    buffer = _NotificationBuffer()
    buffer.feed(b"41 00 BE 3F\r>")
    result = buffer.read_until_prompt(timeout=1.0)
    assert result == "41 00 BE 3F\r"


def test_notification_buffer_waits_for_chunked_data():
    buffer = _NotificationBuffer()

    def feed_later():
        buffer.feed(b"43 01 ")
        buffer.feed(b"03 80\r>")

    threading.Timer(0.05, feed_later).start()
    result = buffer.read_until_prompt(timeout=2.0)
    assert "43 01 03 80" in result


def test_notification_buffer_times_out_with_partial_data():
    buffer = _NotificationBuffer()
    buffer.feed(b"NO PROMPT HERE")
    result = buffer.read_until_prompt(timeout=0.1)
    assert result == "NO PROMPT HERE"
