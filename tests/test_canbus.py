"""Tests for CAN adapter discovery helpers."""

from obd2reader.canbus import is_canable_usb


def test_detects_canable2_usb_identity():
    assert is_canable_usb(0x16D0, 0x117E)


def test_detects_canable_by_description():
    assert is_canable_usb(0x1234, 0x5678, "CANable2 USB adapter")


def test_does_not_misidentify_unrelated_serial_devices():
    assert not is_canable_usb(0x1234, 0x5678, "USB Serial Device")
