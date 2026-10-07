"""WinRM endpoint executor for remote Windows administration."""

from __future__ import annotations

import base64
import json
from typing import Optional, Union

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor
from vcf_ops_telegraf_helper.logger import get_logger
from vcf_ops_telegraf_helper.security.redaction import redact_secrets
from vcf_ops_telegraf_helper.models.discovery import (
    DiscoveredDatabase,
    DiscoveredPerfmonSet,
    DiscoveredService,
)


logger = get_logger("executors.winrm")


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
        self.connection_error = ""
        self.auth_method = f"password ({transport})"
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
        except Exception as exc:
            res = CommandResult(exit_code=1, stdout="", stderr=f"{type(exc).__name__}: {exc}", command="")
        if res.success and res.stdout.strip():
            return True
        # The GUI shows a short message; the underlying WinRM error goes to the log (never the password)
        detail = (res.stderr or "").strip() or f"exit code {res.exit_code} with no output"
        # Errors can echo input back; never let the password reach the persistent log
        detail = redact_secrets(detail, [self.password] if self.password else None)
        self.connection_error = detail[:2000]
        logger.warning(
            "WinRM connection test failed for %s://%s:%d/wsman as user %r (transport %s): %s",
            "https" if self.use_ssl else "http",
            self.hostname,
            self.port,
            self.username or "Administrator",
            self.transport,
            detail[:2000],
        )
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
                stderr=f"{type(exc).__name__}: {exc}",
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

        # Chunk base64 string so that the total PowerShell command line stays comfortably
        # below the Windows WinRM limit (8,192 characters).
        # A chunk of 1,500 base64 chars produces ~3,500 bytes of UTF-16LE script,
        # which EncodedCommand expands to ~4,700 command-line characters.
        chunk_size = 1500
        if not b64_str:
            return

        chunks = [b64_str[i : i + chunk_size] for i in range(0, len(b64_str), chunk_size)]
        for chunk in chunks:
            append_script = (
                f"$bytes = [System.Convert]::FromBase64String('{chunk}'); "
                f"$stream = [System.IO.File]::Open('{safe_path}', [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write); "
                "try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Close() };"
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

    def discover_services(self) -> list[DiscoveredService]:
        """Discover installed or running services on Windows endpoint."""
        script = (
            "Get-Service | Select-Object -Property Name, DisplayName, Status, StartType "
            "| ConvertTo-Json -Compress"
        )
        res = self.execute(script, timeout=15)
        if not res.success or not res.stdout.strip():
            return []

        services: list[DiscoveredService] = []
        try:
            raw = json.loads(res.stdout)
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                name = item.get("Name")
                if not name:
                    continue
                disp = item.get("DisplayName")
                status = "Running" if item.get("Status") in (4, "Running") else "Stopped"
                start = str(item.get("StartType", "Automatic"))
                services.append(DiscoveredService(
                    name=name,
                    display_name=disp,
                    status=status,
                    start_type=start,
                ))
        except Exception:
            pass
        return services

    def discover_perfmon_sets(self) -> list[DiscoveredPerfmonSet]:
        """Discover Windows Perfmon counter sets on Windows endpoint."""
        script = (
            "Get-Counter -ListSet * | Select-Object -Property CounterSetName, Description "
            "| ConvertTo-Json -Compress"
        )
        res = self.execute(script, timeout=20)
        if not res.success or not res.stdout.strip():
            return []

        perf_sets: list[DiscoveredPerfmonSet] = []
        try:
            raw = json.loads(res.stdout)
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                name = item.get("CounterSetName")
                if not name:
                    continue
                desc = item.get("Description")
                perf_sets.append(DiscoveredPerfmonSet(
                    name=name,
                    description=desc,
                    counters=["*"],
                ))
        except Exception:
            pass
        return perf_sets

    def discover_databases(
        self,
        db_type: str = "mssql",
        auth_mode: str = "integrated",
        username: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 1433,
    ) -> list[DiscoveredDatabase]:
        """Discover online database catalogs from SQL Server instance."""
        if db_type.lower() != "mssql":
            return []

        auth_clause = "Integrated Security=SSPI;" if auth_mode == "integrated" else f"User Id={username or ''};Password={password or ''};"
        conn_str = f"Server=127.0.0.1,{port};{auth_clause}TrustServerCertificate=True;Connect Timeout=5;"
        safe_conn_str = conn_str.replace("'", "''")
        script = (
            f"$connStr = '{safe_conn_str}'; "
            "$conn = New-Object System.Data.SqlClient.SqlConnection($connStr); "
            "try { "
            "$conn.Open(); "
            "$cmd = $conn.CreateCommand(); "
            "$cmd.CommandText = 'SELECT name, state_desc FROM sys.databases'; "
            "$r = $cmd.ExecuteReader(); "
            "$dbs = @(); "
            "while ($r.Read()) { $dbs += @{ name=$r[0]; state=$r[1] } }; "
            "$conn.Close(); "
            "$dbs | ConvertTo-Json -Compress; "
            "} catch { Write-Error $_.Exception.Message }"
        )
        res = self.execute(script, timeout=15)
        if not res.success or not res.stdout.strip():
            return []

        dbs: list[DiscoveredDatabase] = []
        try:
            raw = json.loads(res.stdout)
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                name = item.get("name")
                if not name:
                    continue
                state = item.get("state", "ONLINE")
                db_type_label = "system" if name in ("master", "tempdb", "model", "msdb") else "user"
                dbs.append(DiscoveredDatabase(
                    name=name,
                    state=state,
                    db_type=db_type_label,
                ))
        except Exception:
            pass
        return dbs

    def get_free_disk_space_mb(self, path: Optional[str] = None) -> int:
        """Return free disk space in megabytes on target Windows volume."""
        drive = (path or "C:").strip().rstrip("\\/").rstrip(":")[:1] or "C"
        if not drive.isalpha():
            drive = "C"
        script = f"[int64]((Get-PSDrive -Name '{drive}' -PSProvider FileSystem -ErrorAction Stop).Free / 1MB)"
        res = self.execute(script, timeout=10)
        if res.success and res.stdout.strip().isdigit():
            return int(res.stdout.strip())
        return 1000

    def close(self) -> None:
        """Clean up WinRM session resources."""
        self._session = None

