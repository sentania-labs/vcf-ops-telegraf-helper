"""Endpoint executor interface and common structures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Union
from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    """Result of an executed shell command."""

    exit_code: int = Field(description="Process exit code")
    stdout: str = Field(default="", description="Captured standard output")
    stderr: str = Field(default="", description="Captured standard error")
    command: str = Field(default="", description="Original executed command line")

    @property
    def success(self) -> bool:
        """True if the command completed with exit code 0."""
        return self.exit_code == 0


class EndpointExecutor(ABC):
    """Abstract interface for executing commands and managing files on a target."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify reachability and authentication to the endpoint."""
        pass

    @abstractmethod
    def execute(self, command: str, timeout: int = 30) -> CommandResult:
        """Execute a shell command on the target."""
        pass

    @abstractmethod
    def upload(self, source_content: Union[str, bytes], destination_path: str, mode: int = 0o644) -> None:
        """Upload content to a destination file on the target."""
        pass

    @abstractmethod
    def download(self, source_path: str) -> str:
        """Download file content from the target."""
        pass

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if a file exists on the target."""
        pass

    def close(self) -> None:
        """Clean up any active network sessions or resources."""
        pass
