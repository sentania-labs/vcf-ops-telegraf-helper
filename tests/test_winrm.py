"""Tests for WinRM executor and remote Windows management."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor


def test_winrm_executor_init():
    """Verify WinRM executor attributes and endpoint construction."""
    exec_http = WinRMExecutor(hostname="win-host.local", port=5985, username="admin", password="secret")
    assert exec_http.hostname == "win-host.local"
    assert exec_http.port == 5985
    assert not exec_http.use_ssl

    exec_https = WinRMExecutor(hostname="win-host.local", port=5986, username="admin", password="secret", use_ssl=True)
    assert exec_https.port == 5986
    assert exec_https.use_ssl


def test_winrm_executor_test_connection_success():
    """Verify test_connection returns True when PowerShell responds."""
    executor = WinRMExecutor(hostname="win-host.local", username="admin", password="secret")

    mock_session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 0
    mock_res.std_out = b"5\r\n"
    mock_res.std_err = b""
    mock_session.run_ps.return_value = mock_res

    with patch.object(executor, "_get_session", return_value=mock_session):
        assert executor.test_connection() is True


def test_winrm_executor_test_connection_failure():
    """Verify test_connection returns False on command error."""
    executor = WinRMExecutor(hostname="win-host.local", username="admin", password="secret")

    mock_session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 1
    mock_res.std_out = b""
    mock_res.std_err = b"Access Denied"
    mock_session.run_ps.return_value = mock_res

    with patch.object(executor, "_get_session", return_value=mock_session):
        assert executor.test_connection() is False


def test_winrm_executor_upload():
    """Verify upload encodes content as base64 and invokes PowerShell streaming."""
    executor = WinRMExecutor(hostname="win-host.local", username="admin", password="secret")

    mock_session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 0
    mock_res.std_out = b""
    mock_res.std_err = b""
    mock_session.run_ps.return_value = mock_res

    content = "test configuration payload"
    with patch.object(executor, "_get_session", return_value=mock_session):
        executor.upload(content, "C:/telegraf/telegraf.d/test.conf")

    called_cmd = mock_session.run_ps.call_args[0][0]
    expected_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    assert expected_b64 in called_cmd
    assert "C:\\telegraf\\telegraf.d\\test.conf" in called_cmd
    assert "[System.IO.FileMode]::Append" in called_cmd


def test_winrm_executor_file_exists():
    """Verify file_exists parses PowerShell Test-Path output."""
    executor = WinRMExecutor(hostname="win-host.local", username="admin", password="secret")

    mock_session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 0
    mock_res.std_out = b"True\r\n"
    mock_res.std_err = b""
    mock_session.run_ps.return_value = mock_res

    with patch.object(executor, "_get_session", return_value=mock_session):
        assert executor.file_exists("C:/telegraf/telegraf.exe") is True


def test_winrm_discover_databases_escapes_single_quotes():
    """Verify single quotes in credentials are escaped against PowerShell script injection."""
    executor = WinRMExecutor(hostname="win-host.local", username="admin", password="secret")
    mock_session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 0
    mock_res.std_out = b'[{"name":"master","state":"ONLINE"}]\r\n'
    mock_res.std_err = b""
    mock_session.run_ps.return_value = mock_res

    with patch.object(executor, "_get_session", return_value=mock_session):
        dbs = executor.discover_databases(
            db_type="mssql",
            auth_mode="sql",
            username="sa'--",
            password="pass'word",
            port=1433,
        )

    called_cmd = mock_session.run_ps.call_args[0][0]
    assert "sa''--" in called_cmd
    assert "pass''word" in called_cmd
    assert len(dbs) == 1
    assert dbs[0].name == "master"


def test_winrm_discover_perfmon_sets_default_counters():
    """Verify discovered counter sets default to wildcard counters instead of empty list."""
    executor = WinRMExecutor(hostname="win-host.local", username="admin", password="secret")
    mock_session = MagicMock()
    mock_res = MagicMock()
    mock_res.status_code = 0
    mock_res.std_out = b'[{"CounterSetName":"Web Service","Description":"Web Service Counters"}]\r\n'
    mock_res.std_err = b""
    mock_session.run_ps.return_value = mock_res

    with patch.object(executor, "_get_session", return_value=mock_session):
        sets = executor.discover_perfmon_sets()

    assert len(sets) == 1
    assert sets[0].name == "Web Service"
    assert sets[0].counters == ["*"]
