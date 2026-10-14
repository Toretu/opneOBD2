"""Tests for the ELM327 driver using a scripted fake connection."""

from obd2reader.elm327 import ELM327


class RecordingConnection:
    """Records written commands and returns canned responses."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.written = []
        self._last = ""
        self.closed = False

    def write(self, data):
        self._last = data.strip().upper()
        self.written.append(self._last)

    def read_until_prompt(self):
        return self.responses.get(self._last, "")

    def close(self):
        self.closed = True


def test_connect_sends_init_sequence():
    conn = RecordingConnection()
    elm = ELM327(conn)
    elm.connect()

    assert elm.connected is True
    assert conn.written == ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]


def test_query_cleans_response():
    conn = RecordingConnection({"03": "SEARCHING...\r43 01 03 80\r\rOK"})
    elm = ELM327(conn)
    cleaned = elm.query("03")

    assert "SEARCHING" not in cleaned
    assert "OK" not in cleaned
    assert "43 01 03 80" in cleaned


def test_close_closes_connection():
    conn = RecordingConnection()
    ELM327(conn).close()
    assert conn.closed is True
