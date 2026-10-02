"""SSH endpoint executor using Paramiko for remote Linux administration."""

from __future__ import annotations

import os
import posixpath
import shlex
from typing import Optional, Union
import paramiko

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.security.redaction import redact_secrets
from vcf_ops_telegraf_helper.models.discovery import (
    DiscoveredDatabase,
    DiscoveredService,
)


logger = get_logger("executors.ssh")


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
        self.key_filename = os.path.expanduser(key_filename) if key_filename else None
        self.timeout = timeout
        self.use_sudo = use_sudo and (username != "root")
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
        except Exception as exc:
            res = CommandResult(exit_code=1, stdout="", stderr=f"{type(exc).__name__}: {exc}", command="")
        if res.success and "linux" in res.stdout.lower():
            return True
        # The GUI shows a short message; the underlying SSH error goes to the log (never the password)
        if res.success:
            detail = f"connected, but 'uname -s' reported {res.stdout.strip()!r} instead of Linux"
        else:
            detail = (res.stderr or "").strip() or f"exit code {res.exit_code} with no output"
        # Errors can echo input back; never let the password (also the key passphrase) reach the log
        detail = redact_secrets(detail, [self.password] if self.password else None)
        # paramiko offers every one of these that is available, so name them all
        sources = [f"key file {self.key_filename}"] if self.key_filename else []
        if self.password:
            sources.append("password" if not self.key_filename else "password (also used as key passphrase)")
        sources.extend(["SSH agent", "default keys"])
        logger.warning(
            "SSH connection test failed for %s:%d as user %r (%s): %s",
            self.hostname,
            self.port,
            self.username,
            ", ".join(sources),
            detail[:2000],
        )
        return False

    def _is_privileged_path(self, path: str) -> bool:
        normalized = posixpath.normpath(path)
        system_roots = ("/etc", "/usr", "/var", "/opt", "/root")
        return any(
            normalized == root or normalized.startswith(f"{root}/")
            for root in system_roots
        )

    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        self._ensure_connected()
        if self._client is None:
            return CommandResult(exit_code=1, stderr="SSH client not connected", command=command)

        # Prepend sudo for privileged commands if running as non-root with sudo enabled
        effective_cmd = command
        if self.use_sudo and not command.strip().startswith("sudo"):
            stripped = command.strip()
            first_word = stripped.split()[0] if stripped else ""
            first_base = posixpath.basename(first_word)
            privileged_cmds = (
                "mkdir", "systemctl", "cp", "rm", "chmod", "chown",
                "test", "useradd", "adduser", "groupadd", "addgroup",
                "apt-get", "yum", "dnf", "bash", "curl", "journalctl",
            )
            trusted_system_bins = (
                "telegraf",
                "/usr/bin/telegraf",
                "/usr/local/bin/telegraf",
                "/bin/telegraf",
            )
            if (
                first_base in privileged_cmds
                or first_word in trusted_system_bins
                or stripped.startswith("cat /sys/class")
            ):
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
        data = source_content.encode("utf-8") if isinstance(source_content, str) else source_content

        if self.use_sudo and self._is_privileged_path(destination_path):
            self._ensure_connected()
            if self._client is None:
                raise RuntimeError("SSH client not connected")

            parent_dir = posixpath.dirname(destination_path)
            mkdir_res = self.execute(f"mkdir -p {shlex.quote(parent_dir)}")
            if not mkdir_res.success:
                raise IOError(f"Failed to create directory {parent_dir} via sudo: {mkdir_res.stderr.strip()}")

            cmd = f"sudo -n tee -- {shlex.quote(destination_path)} > /dev/null"
            stdin, stdout, stderr = self._client.exec_command(cmd, timeout=self.timeout)
            try:
                stdin.write(data)
                stdin.channel.shutdown_write()
            except (BrokenPipeError, OSError, IOError):
                pass

            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read().decode("utf-8", errors="replace").strip()
                raise IOError(f"Failed to write {destination_path} via sudo (exit {exit_code}): {err}")

            chmod_res = self.execute(f"chmod {oct(mode)[2:]} {shlex.quote(destination_path)}")
            if not chmod_res.success:
                raise IOError(f"Failed to set permissions on {destination_path} via sudo: {chmod_res.stderr.strip()}")
        else:
            sftp = self._get_sftp()
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
        if self.use_sudo and self._is_privileged_path(path):
            res = self.execute(f"test -e {shlex.quote(path)}", timeout=5)
            return res.success
        sftp = self._get_sftp()
        try:
            sftp.stat(path)
            return True
        except IOError:
            return False

    def discover_services(self) -> list[DiscoveredService]:
        """Discover running systemd services on Linux endpoint."""
        res = self.execute("systemctl list-units --type=service --state=running --no-legend --no-pager", timeout=15)
        if not res.success or not res.stdout.strip():
            return []

        services: list[DiscoveredService] = []
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4:
                unit = parts[0].replace(".service", "")
                desc = " ".join(parts[4:]) if len(parts) > 4 else ""
                services.append(DiscoveredService(
                    name=unit,
                    display_name=desc or unit,
                    status="Running",
                    start_type="Automatic",
                ))
        return services

    def discover_databases(
        self,
        db_type: str = "postgresql",
        auth_mode: str = "integrated",
        username: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 5432,
    ) -> list[DiscoveredDatabase]:
        """Discover database catalogs on Linux endpoint."""
        dbs: list[DiscoveredDatabase] = []
        if db_type.lower() in ("postgresql", "postgres"):
            cmd = "sudo -u postgres psql -t -A -c \"SELECT datname FROM pg_database WHERE datistemplate = false\""
            res = self.execute(cmd, timeout=10)
            if res.success and res.stdout.strip():
                for line in res.stdout.splitlines():
                    name = line.strip()
                    if name:
                        dbs.append(DiscoveredDatabase(
                            name=name,
                            state="ONLINE",
                            db_type="system" if name == "postgres" else "user",
                        ))
        elif db_type.lower() in ("mysql", "mariadb"):
            cmd = "mysql -N -e 'SHOW DATABASES;'"
            res = self.execute(cmd, timeout=10)
            if res.success and res.stdout.strip():
                for line in res.stdout.splitlines():
                    name = line.strip()
                    if name and name not in ("information_schema", "performance_schema"):
                        dbs.append(DiscoveredDatabase(
                            name=name,
                            state="ONLINE",
                            db_type="system" if name in ("mysql", "sys") else "user",
                        ))
        return dbs

    def get_free_disk_space_mb(self, path: Optional[str] = None) -> int:
        """Return free disk space in megabytes on target Linux filesystem."""
        check_path = path or "/"
        cmd = f"p={shlex.quote(check_path)}; if [ ! -e \"$p\" ]; then p=\"/\"; fi; df -m -P \"$p\" | awk 'NR>1 {{print $4}}'"
        res = self.execute(cmd, timeout=10)
        if res.success and res.stdout.strip().isdigit():
            return int(res.stdout.strip())
        return 1000

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

