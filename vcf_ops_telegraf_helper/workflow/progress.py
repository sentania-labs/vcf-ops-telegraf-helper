"""Workflow progress reporting protocols."""

from __future__ import annotations

from typing import Protocol
from vcf_ops_telegraf_helper.models.workflow import StageResult, WorkflowStage


class ProgressReporter(Protocol):
    """Protocol for streaming progress events from the workflow engine."""

    def on_stage_start(self, stage: WorkflowStage) -> None:
        """Invoked immediately before a workflow stage begins."""
        ...

    def on_stage_complete(self, result: StageResult) -> None:
        """Invoked immediately after a workflow stage completes."""
        ...

    def on_message(self, message: str) -> None:
        """Invoked when diagnostic or informational output is emitted."""
        ...


class SilentProgressReporter:
    """Null reporter that discards all progress events (useful for tests)."""

    def on_stage_start(self, stage: WorkflowStage) -> None:
        pass

    def on_stage_complete(self, result: StageResult) -> None:
        pass

    def on_message(self, message: str) -> None:
        pass
