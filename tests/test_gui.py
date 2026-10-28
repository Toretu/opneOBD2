from urllib.parse import parse_qs, urlparse

from obd2reader import gui


def test_code_lookup_url_includes_vehicle_module_and_code():
    url = gui._code_lookup_url("ABS / ESP brakes", "VAG 00283")

    assert url.startswith("https://www.google.com/search?")
    assert parse_qs(urlparse(url).query)["q"] == [
        "Volkswagen Sharan 2012 ABS / ESP brakes VAG 00283 fault code"
    ]


def test_append_error_log_writes_timestamp_and_traceback(tmp_path, monkeypatch):
    log_path = tmp_path / "obd2reader-error.log"
    monkeypatch.setattr(gui, "ERROR_LOG_PATH", log_path)

    try:
        raise RuntimeError("sample scan failure")
    except RuntimeError as error:
        written_path = gui._append_error_log(error)

    assert written_path == str(log_path)
    contents = log_path.read_text(encoding="utf-8")
    assert "GUI warning: sample scan failure" in contents
    assert "RuntimeError: sample scan failure" in contents
    assert "Traceback" in contents