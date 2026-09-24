"""Local endpoint executor using local subprocess and filesystem calls."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Union

from vcf_ops_telegraf_helper.executors.base import CommandResult, EndpointExecutor


class LocalExecutor(EndpointExecutor):
    """Executes commands and manages files directly on the local system."""

    def test_connection(self) -> bool:
        return True

    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return CommandResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                command=command,
            )
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                exit_code=124,
                stdout=e.stdout or "",
                stderr=f"Command timed out after {timeout} seconds",
                command=command,
            )
        except Exception as e:
            return CommandResult(
                exit_code=1,
                stderr=str(e),
                command=command,
            )

    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source_content, str):
            dest.write_text(source_content, encoding="utf-8")
        else:
            dest.write_bytes(source_content)
        os.chmod(dest, mode)

    def download(self, source_path: str) -> str:
        return Path(source_path).read_text(encoding="utf-8")

    def file_exists(self, path: str) -> bool:
        return Path(path).exists()
