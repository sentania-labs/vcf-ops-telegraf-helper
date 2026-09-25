"""WinRM endpoint executor for remote Windows administration."""

from __future__ import annotations

import base64
from typing import Optional, Union

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor


class WinRMExecutor(EndpointExecutor):
    """Executes PowerShell commands and transfers files across WinRM."""

    def __init__(
        self,
        hostname: str,
        port: int = 5985,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_ssl: bool = False,
        transport: str = "ntlm",
        timeout: int = 20,
    ):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.transport = transport
        self.timeout = timeout
        self._session = None

    def _get_session(self):
        if self._session is not None:
            return self._session

        import winrm

        scheme = "https" if self.use_ssl else "http"
        endpoint = f"{scheme}://{self.hostname}:{self.port}/wsman"

        # pywinrm requires read_timeout_sec to strictly exceed operation_timeout_sec
        op_timeout = max(5, self.timeout)
        read_timeout = op_timeout + 10

        self._session = winrm.Session(
            target=endpoint,
            auth=(self.username or "Administrator", self.password or ""),
            transport=self.transport,
            server_cert_validation="ignore",
            operation_timeout_sec=op_timeout,
            read_timeout_sec=read_timeout,
        )
        return self._session

    def test_connection(self) -> bool:
        """Verify reachability and authentication to the Windows endpoint."""
        try:
            res = self.execute("$PSVersionTable.PSVersion.Major", timeout=10)
            return res.success and len(res.stdout.strip()) > 0
        except Exception:
            return False

    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        """Execute a PowerShell script block on the Windows target."""
        try:
            session = self._get_session()
            op_timeout = max(5, timeout)
            session.protocol.operation_timeout_sec = op_timeout
            session.protocol.read_timeout_sec = op_timeout + 15
            response = session.run_ps(command)
            return CommandResult(
                exit_code=response.status_code,
                stdout=response.std_out.decode("utf-8", errors="replace"),
                stderr=response.std_err.decode("utf-8", errors="replace"),
                command=command,
            )
        except Exception as exc:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                command=command,
            )

    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        """Upload content to a Windows destination path via base64 PowerShell stream."""
        raw_bytes = source_content.encode("utf-8") if isinstance(source_content, str) else source_content
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        # Normalize Windows backslashes and escape single quotes for PowerShell
        safe_path = destination_path.replace("/", "\\").replace("'", "''")

        # Prepare target directory and initialize destination file safely
        init_script = (
            f"$target = '{safe_path}'; "
            "$parent = [System.IO.Path]::GetDirectoryName($target); "
            "if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }; "
            "if (Test-Path $target) { Remove-Item -Path $target -Force }; "
            "[System.IO.File]::WriteAllBytes($target, [byte[]]@());"
        )
        res = self.execute(init_script, timeout=self.timeout)
        if not res.success:
            raise RuntimeError(f"Failed to initialize file {destination_path}: {res.stderr or res.stdout}")

        # Chunk the payload into safe 32KB pieces to respect WinRM envelope limits
        chunk_size = 32000
        if not b64_str:
            return

        chunks = [b64_str[i : i + chunk_size] for i in range(0, len(b64_str), chunk_size)]
        for chunk in chunks:
            append_script = (
                f"$bytes = [System.Convert]::FromBase64String('{chunk}'); "
                f"[System.IO.File]::AppendAllBytes('{safe_path}', $bytes);"
            )
            res = self.execute(append_script, timeout=max(20, self.timeout))
            if not res.success:
                raise RuntimeError(f"Failed to upload file to {destination_path}: {res.stderr or res.stdout}")

    def download(self, source_path: str) -> str:
        """Download file content from the target Windows machine."""
        safe_path = source_path.replace("/", "\\").replace("'", "''")
        script = f"[System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes('{safe_path}'))"
        res = self.execute(script, timeout=self.timeout)
        if not res.success:
            raise RuntimeError(f"Failed to download file from {source_path}: {res.stderr}")
        b64_clean = "".join(res.stdout.split())
        return base64.b64decode(b64_clean).decode("utf-8", errors="replace")

    def file_exists(self, path: str) -> bool:
        """Check if a file exists on the Windows target."""
        safe_path = path.replace("/", "\\").replace("'", "''")
        res = self.execute(f"Test-Path -Path '{safe_path}'", timeout=10)
        return res.success and "True" in res.stdout.strip()

    def close(self) -> None:
        """Clean up WinRM session resources."""
        self._session = None
