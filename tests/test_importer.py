import json
import pytest
import importlib
from unittest.mock import patch, MagicMock
from pathlib import Path
import requests

import sys
import importlib.util

script_path = Path(__file__).parent.parent / "anki-csv-importer.py"
spec = importlib.util.spec_from_file_location("anki_csv_importer", script_path)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def test_probe_ankiconnect_offline():
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        online, err = importer.probe_ankiconnect("http://127.0.0.1:8765", timeout=0.1)
        assert online is False
        assert "Connection refused" in err


def test_probe_ankiconnect_online():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": 6, "error": None}
    with patch("requests.post", return_value=mock_resp):
        online, err = importer.probe_ankiconnect("http://127.0.0.1:8765", timeout=0.1)
        assert online is True
        assert err is None


def test_write_import_log_entry(tmp_path):
    log_file = tmp_path / "test-import.log"
    importer.write_import_log_entry(str(log_file), "20260818190300:export:anki", "INFO", "Starting import")
    content = log_file.read_text(encoding="utf-8")
    assert "[20260818190300:export:anki]" in content
    assert "[INFO]" in content
    assert "Starting import" in content


def test_emit_error_and_exit(tmp_path, capsys):
    log_file = tmp_path / "test-import.log"
    with pytest.raises(SystemExit) as exc_info:
        importer.emit_error_and_exit(
            code="ERR_ANKI_NOT_RUNNING",
            message="Anki is closed",
            details={"url": "http://127.0.0.1:8765"},
            zid="20260818190300",
            trace_id="20260818190300:export:anki",
            log_file=str(log_file)
        )
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "error"
    assert payload["code"] == "ERR_ANKI_NOT_RUNNING"
    assert payload["zid"] == "20260818190300"
    assert payload["trace_id"] == "20260818190300:export:anki"
    
    log_content = log_file.read_text(encoding="utf-8")
    assert "[ERR_ANKI_NOT_RUNNING]" in log_content
