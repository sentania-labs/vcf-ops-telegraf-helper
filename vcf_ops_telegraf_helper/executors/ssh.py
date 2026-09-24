"""SSH endpoint executor using Paramiko for remote Linux administration."""

from __future__ import annotations

import os
from typing import Optional, Union
import paramiko

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor


class SSHExecutor(EndpointExecutor):
    """Executes commands and transfers files across SSH and SFTP, with sudo support."""

    def __init__(
        self,
        hostname: str,
        port: int = 22,
        username: Optional[str] = None,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: int = 10,
        use_sudo: bool = True,
    ):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.timeout = timeout
        self.use_sudo = use_sudo and (username not in (None, "root"))
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def _ensure_connected(self) -> None:
        if self._client is not None:
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.hostname,
            port=self.port,
            username=self.username,
            password=self.password,
            key_filename=self.key_filename,
            timeout=self.timeout,
            look_for_keys=True,
            allow_agent=True,
        )
        self._client = client

    def _get_sftp(self) -> paramiko.SFTPClient:
        self._ensure_connected()
        if self._sftp is None and self._client is not None:
            self._sftp = self._client.open_sftp()
        return self._sftp

    def test_connection(self) -> bool:
        try:
            self._ensure_connected()
            res = self.execute("uname -s", timeout=5)
            return res.success and "linux" in res.stdout.lower()
        except Exception:
            return False

    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        self._ensure_connected()
        if self._client is None:
            return CommandResult(exit_code=1, stderr="SSH client not connected", command=command)

        # Prepend sudo for privileged commands if running as non-root with sudo enabled
        effective_cmd = command
        if self.use_sudo and not command.strip().startswith("sudo"):
            privileged_prefixes = ("mkdir", "systemctl", "cp", "rm", "chmod", "chown", "cat /sys/class")
            if any(command.strip().startswith(p) for p in privileged_prefixes):
                effective_cmd = f"sudo -n {command}"

        stdin, stdout, stderr = self._client.exec_command(effective_cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr.read().decode("utf-8", errors="replace")

        return CommandResult(
            exit_code=exit_code,
            stdout=out_text,
            stderr=err_text,
            command=effective_cmd,
        )

    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        sftp = self._get_sftp()
        data = source_content.encode("utf-8") if isinstance(source_content, str) else source_content

        if self.use_sudo and destination_path.startswith(("/etc", "/usr", "/var")):
            # Write to temporary file in /tmp, then move with sudo
            tmp_remote = f"/tmp/.vcf_upload_{os.getpid()}_{hash(destination_path) % 10000}"
            with sftp.open(tmp_remote, "wb") as remote_file:
                remote_file.write(data)

            # Ensure destination directory and move
            parent_dir = str(os.path.dirname(destination_path))
            self.execute(f"mkdir -p {parent_dir}")
            self.execute(f"cp {tmp_remote} {destination_path}")
            self.execute(f"chmod {oct(mode)[2:]} {destination_path}")
            self.execute(f"rm -f {tmp_remote}")
        else:
            # Ensure parent directory exists on remote target
            parts = destination_path.strip("/").split("/")
            cur = ""
            for part in parts[:-1]:
                cur += "/" + part
                try:
                    sftp.stat(cur)
                except IOError:
                    sftp.mkdir(cur)

            with sftp.open(destination_path, "wb") as remote_file:
                remote_file.write(data)

            sftp.chmod(destination_path, mode)

    def download(self, source_path: str) -> str:
        sftp = self._get_sftp()
        with sftp.open(source_path, "rb") as remote_file:
            content = remote_file.read()
            return content.decode("utf-8", errors="replace")

    def file_exists(self, path: str) -> bool:
        sftp = self._get_sftp()
        try:
            sftp.stat(path)
            return True
        except IOError:
            return False

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
