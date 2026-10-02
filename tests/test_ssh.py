"""Tests for SSH executor and remote Linux administration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor


def test_ssh_executor_privileged_path_logic():
    """Verify detection of root/privileged filesystem paths and rejection of unprivileged paths."""
    executor = SSHExecutor(hostname="linux.local", username="scott")
    assert executor._is_privileged_path("/etc/telegraf/telegraf.conf") is True
    assert executor._is_privileged_path("/usr/bin/telegraf") is True
    assert executor._is_privileged_path("/var/log/telegraf") is True
    assert executor._is_privileged_path("/opt/telegraf/telegraf.conf") is True
    assert executor._is_privileged_path("/root/.ssh/authorized_keys") is True
    assert executor._is_privileged_path("/etc") is True

    # Non-system paths or lookalikes
    assert executor._is_privileged_path("/etc_backup/conf") is False
    assert executor._is_privileged_path("/opt_custom/app") is False
    assert executor._is_privileged_path("/home/scott/telegraf.conf") is False
    assert executor._is_privileged_path("/tmp/scratch.conf") is False


def test_ssh_executor_posix_path_handling_under_windows_semantics():
    """Verify remote POSIX paths are normalized with posixpath even on Windows hosts."""
    import ntpath

    executor = SSHExecutor(hostname="linux.local", username="scott")
    with patch("os.path", ntpath):
        assert executor._is_privileged_path("/etc/telegraf/telegraf.conf") is True
        assert executor._is_privileged_path("/opt/telegraf/telegraf.conf") is True
        assert executor._is_privileged_path("/home/scott/telegraf.conf") is False


def test_ssh_executor_init_sudo_logic():
    """Verify use_sudo is enabled for non-root and ambient (None or empty) users, and disabled for root."""
    exec_ambient_none = SSHExecutor(hostname="linux.local", username=None)
    assert exec_ambient_none.use_sudo is True

    exec_ambient_empty = SSHExecutor(hostname="linux.local", username="")
    assert exec_ambient_empty.use_sudo is True

    exec_user = SSHExecutor(hostname="linux.local", username="scott")
    assert exec_user.use_sudo is True

    exec_root = SSHExecutor(hostname="linux.local", username="root")
    assert exec_root.use_sudo is False

    exec_explicit_no_sudo = SSHExecutor(hostname="linux.local", username="scott", use_sudo=False)
    assert exec_explicit_no_sudo.use_sudo is False


def test_ssh_executor_upload_sudo_tee():
    """Verify upload uses direct sudo tee stream with -- argument delimiter without /tmp staging."""
    executor = SSHExecutor(hostname="linux.local", username="scott")

    mock_client = MagicMock()
    mock_stdin = MagicMock()
    mock_stdout = MagicMock()
    mock_stderr = MagicMock()

    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)
    executor._client = mock_client

    with patch.object(executor, "execute") as mock_exec:
        mock_exec.return_value = MagicMock(success=True)
        executor.upload("test content", "/etc/telegraf/telegraf.d/test.conf", mode=0o644)

        # Verify mkdir -p was executed
        assert any("mkdir -p /etc/telegraf/telegraf.d" in str(call) for call in mock_exec.call_args_list)

        # Verify sudo tee stream executed with -- delimiter and stdin was closed cleanly
        mock_client.exec_command.assert_called_with("sudo -n tee -- /etc/telegraf/telegraf.d/test.conf > /dev/null", timeout=10)
        mock_stdin.write.assert_called_with(b"test content")
        mock_stdin.channel.shutdown_write.assert_called_once()

        # Verify chmod
        assert any("chmod 644 /etc/telegraf/telegraf.d/test.conf" in str(call) for call in mock_exec.call_args_list)


def test_ssh_executor_upload_sudo_tee_broken_pipe_handles_error():
    """Verify upload catches BrokenPipeError if sudo rejects authentication immediately and reports stderr."""
    executor = SSHExecutor(hostname="linux.local", username="scott")

    mock_client = MagicMock()
    mock_stdin = MagicMock()
    mock_stdout = MagicMock()
    mock_stderr = MagicMock()

    mock_stdin.write.side_effect = BrokenPipeError("Broken pipe")
    mock_stdout.channel.recv_exit_status.return_value = 1
    mock_stderr.read.return_value = b"sudo: a password is required\n"
    mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)
    executor._client = mock_client

    with patch.object(executor, "execute") as mock_exec:
        mock_exec.return_value = MagicMock(success=True)
        with pytest.raises(IOError, match="Failed to write /etc/telegraf/test.conf via sudo \\(exit 1\\): sudo: a password is required"):
            executor.upload("content", "/etc/telegraf/test.conf")


def test_ssh_executor_upload_mkdir_failure_raises():
    """Verify upload raises IOError if mkdir -p fails."""
    executor = SSHExecutor(hostname="linux.local", username="scott")
    mock_client = MagicMock()
    executor._client = mock_client

    with patch.object(executor, "execute") as mock_exec:
        mock_exec.return_value = MagicMock(success=False, stderr="Read-only file system")
        with pytest.raises(IOError, match="Failed to create directory /etc/telegraf via sudo: Read-only file system"):
            executor.upload("content", "/etc/telegraf/test.conf")


def test_ssh_executor_upload_chmod_failure_raises():
    """Verify upload raises IOError if chmod fails after successful tee."""
    executor = SSHExecutor(hostname="linux.local", username="scott")
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())
    executor._client = mock_client

    def mock_exec_handler(cmd, **kwargs):
        if cmd.startswith("mkdir"):
            return MagicMock(success=True)
        if cmd.startswith("chmod"):
            return MagicMock(success=False, stderr="Operation not permitted")
        return MagicMock(success=True)

    with patch.object(executor, "execute", side_effect=mock_exec_handler):
        with pytest.raises(IOError, match="Failed to set permissions on /etc/telegraf/test.conf via sudo: Operation not permitted"):
            executor.upload("content", "/etc/telegraf/test.conf")


def test_ssh_executor_upload_sftp_for_unprivileged_destination():
    """Verify upload uses SFTP directly when destination path is unprivileged."""
    executor = SSHExecutor(hostname="linux.local", username="scott", use_sudo=True)

    mock_sftp = MagicMock()
    mock_file = MagicMock()
    mock_sftp.open.return_value.__enter__.return_value = mock_file

    with patch.object(executor, "_get_sftp", return_value=mock_sftp):
        executor.upload("custom content", "/home/scott/custom.conf")
        mock_file.write.assert_called_with(b"custom content")
        mock_sftp.chmod.assert_called_with("/home/scott/custom.conf", 0o644)


def test_ssh_executor_file_exists_sudo_elevates():
    """Verify file_exists executes sudo -n test -e via _client.exec_command for privileged paths."""
    executor = SSHExecutor(hostname="linux.local", username="scott")

    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stdout.read.return_value = b""
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    executor._client = mock_client

    assert executor.file_exists("/etc/telegraf/telegraf.conf") is True
    mock_client.exec_command.assert_called_with("sudo -n test -e /etc/telegraf/telegraf.conf", timeout=5)


def test_ssh_executor_execute_prepends_sudo():
    """Verify execute prepends sudo -n to privileged commands and leaves unprivileged commands untouched."""
    executor = SSHExecutor(hostname="linux.local", username="scott")

    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stdout.read.return_value = b"active\n"
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    executor._client = mock_client

    # Privileged command
    res = executor.execute("systemctl is-active telegraf")
    assert res.success is True
    mock_client.exec_command.assert_called_with("sudo -n systemctl is-active telegraf", timeout=30)

    # Privileged binary with full path
    res = executor.execute("/usr/bin/telegraf --test --config /etc/telegraf/telegraf.conf")
    assert res.success is True
    mock_client.exec_command.assert_called_with("sudo -n /usr/bin/telegraf --test --config /etc/telegraf/telegraf.conf", timeout=30)

    # User/group provisioning command
    res = executor.execute("groupadd -r telegraf")
    assert res.success is True
    mock_client.exec_command.assert_called_with("sudo -n groupadd -r telegraf", timeout=30)

    # test -e command
    res = executor.execute("test -e /etc/telegraf")
    assert res.success is True
    mock_client.exec_command.assert_called_with("sudo -n test -e /etc/telegraf", timeout=30)

    # User-local binary is NOT elevated to root
    res = executor.execute("/home/scott/bin/telegraf --version")
    assert res.success is True
    mock_client.exec_command.assert_called_with("/home/scott/bin/telegraf --version", timeout=30)

    # Unprivileged command
    res = executor.execute("uname -s")
    assert res.success is True
    mock_client.exec_command.assert_called_with("uname -s", timeout=30)


def test_ssh_executor_key_filename_tilde_expansion():
    """Verify tilde in key_filename is expanded to full home path."""
    import os
    executor = SSHExecutor(hostname="linux.local", key_filename="~/.ssh/custom_key")
    expected = os.path.expanduser("~/.ssh/custom_key")
    assert executor.key_filename == expected


def test_ssh_executor_get_free_disk_space_mb_fallback():
    """Verify get_free_disk_space_mb executes df -m -P with root fallback."""
    executor = SSHExecutor(hostname="linux.local", username="scott")
    mock_client = MagicMock()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stdout.read.return_value = b"Filesystem 1048576 524288 524288 50% /\n"
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    executor._client = mock_client

    with patch.object(executor, "execute", return_value=CommandResult(exit_code=0, stdout="1500\n", command="cmd")):
        free_mb = executor.get_free_disk_space_mb("/etc/telegraf")

    assert free_mb == 1500


def test_ssh_test_connection_logs_real_error_without_password(caplog):
    """A failed SSH connection test logs the underlying error for the operator, never the password."""
    import logging
    from unittest.mock import patch as _patch

    from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor

    executor = SSHExecutor(hostname="linux01.corp.local", username="root", password="S3cret-pw")
    with _patch.object(executor, "_ensure_connected", side_effect=OSError("Authentication failed.")):
        with caplog.at_level(logging.WARNING, logger="vcf_ops_telegraf_helper"):
            assert executor.test_connection() is False

    logged = caplog.text
    assert "Authentication failed." in logged
    assert "linux01.corp.local:22" in logged
    # Guards against the log format ever including the executor's password field
    assert "S3cret-pw" not in logged


def test_ssh_test_connection_logs_non_linux_target(caplog):
    """Reaching a host that is not Linux is logged with what it reported."""
    import logging

    executor = SSHExecutor(hostname="bsd01.corp.local", username="root", password="pw")
    with patch.object(executor, "_ensure_connected"), patch.object(
        executor, "execute", return_value=CommandResult(exit_code=0, stdout="FreeBSD\n", command="uname -s")
    ):
        with caplog.at_level(logging.WARNING, logger="vcf_ops_telegraf_helper"):
            assert executor.test_connection() is False
    assert "reported 'FreeBSD' instead of Linux" in caplog.text


def test_ssh_test_connection_redacts_password_and_lists_auth_sources(caplog):
    """The log masks an echoed password and names every auth source paramiko was given."""
    import logging

    executor = SSHExecutor(hostname="linux01.corp.local", username="root", password="S3cret-pw", key_filename="/tmp/id_test")
    with patch.object(executor, "_ensure_connected", side_effect=OSError("bad passphrase S3cret-pw for key")):
        with caplog.at_level(logging.WARNING, logger="vcf_ops_telegraf_helper"):
            assert executor.test_connection() is False
    logged = caplog.text
    assert "S3cret-pw" not in logged
    assert "key file /tmp/id_test, password (also used as key passphrase), SSH agent, default keys" in logged

