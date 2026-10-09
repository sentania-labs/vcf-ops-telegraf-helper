"""Durable record of an agent takeover, kept on the administrator workstation.

One JSON document per VM (keyed by vCenter id and MOR) plus a backup directory holding the
managed agent's configuration text. Secrets never enter the journal: no passwords, tokens or
key material, only identities, stage outcomes, file references and the operator's verbatim
confirmation. The record lets an interrupted takeover be reconciled with Ops and the endpoint
and resumed from the observed state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.vcf import AgentObjectInfo
from vcf_ops_telegraf_helper.storage.state import DEFAULT_STATE_DIR
from vcf_ops_telegraf_helper.utils import local_now_formatted

DEFAULT_JOURNAL_DIR = DEFAULT_STATE_DIR / "takeovers"


class TakeoverRecord(BaseModel):
    """What is known about one takeover, updated after every stage."""

    vm_name: Optional[str] = None
    vm_mor: str
    vc_id: str
    vm_resource_id: Optional[str] = None
    target_hostname: str
    ops_url: str
    collector_address: Optional[str] = None
    state: str = "captured"  # captured, backed_up, retired, cleaned, installed, verified, failed
    created_at: str = Field(default_factory=local_now_formatted)
    updated_at: str = Field(default_factory=local_now_formatted)
    confirmation_text: str = ""
    managed_services: List[str] = Field(default_factory=list)
    managed_telegraf_version: Optional[str] = None
    agent_object_before: Optional[AgentObjectInfo] = None
    agent_object_after: Optional[AgentObjectInfo] = None
    cutover_started_at: Optional[str] = None
    cutover_started_epoch: Optional[float] = None
    uninstall_task_id: Optional[str] = None
    uninstall_task_stage: Optional[str] = None
    backup_dir: Optional[str] = None
    backup_files: List[str] = Field(default_factory=list)
    stages: List[Dict[str, Any]] = Field(default_factory=list)
    results: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return journal_key(self.vc_id, self.vm_mor)


def journal_key(vc_id: str, vm_mor: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{vc_id}_{vm_mor}")


class TakeoverJournal:
    """Reads and writes takeover records and their configuration backups."""

    def __init__(self, directory: Optional[Path] = None):
        self.directory = Path(directory) if directory else DEFAULT_JOURNAL_DIR

    def _ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def record_path(self, vc_id: str, vm_mor: str) -> Path:
        return self.directory / f"{journal_key(vc_id, vm_mor)}.json"

    def backup_dir(self, vc_id: str, vm_mor: str) -> Path:
        return self.directory / journal_key(vc_id, vm_mor)

    def load(self, vc_id: str, vm_mor: str) -> Optional[TakeoverRecord]:
        path = self.record_path(vc_id, vm_mor)
        if not path.exists():
            return None
        try:
            return TakeoverRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, record: TakeoverRecord) -> Path:
        self._ensure()
        record.updated_at = local_now_formatted()
        path = self.record_path(record.vc_id, record.vm_mor)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record.model_dump(mode="json"), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path

    def write_backup(self, record: TakeoverRecord, files: Dict[str, str]) -> Path:
        """Store configuration text under the VM's backup directory. Keys are relative names."""
        self._ensure()
        target = self.backup_dir(record.vc_id, record.vm_mor)
        if target.exists():
            # A fresh takeover starts a fresh backup; stale fragments from an earlier attempt must not resurface on resume
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        written: List[str] = []
        for name, content in files.items():
            safe = Path(*[part for part in re.split(r"[\\/]+", name) if part not in ("", ".", "..")])
            dest = target / safe
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            try:
                os.chmod(dest, 0o600)
            except OSError:
                pass
            written.append(str(dest))
        record.backup_dir = str(target)
        record.backup_files = written
        return target

    def list_records(self) -> List[TakeoverRecord]:
        if not self.directory.exists():
            return []
        records = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                records.append(TakeoverRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return records
