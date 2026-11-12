"""History of everything the app has done, kept on this computer only.

One JSON file next to state.json. Every run (set up, change, takeover, test, practice run,
removal) is one entry carrying a line per machine: its outcome in a sentence, the reason and
what to do on a failure, and the command that does the same thing from the command line.
Passwords, tokens and key material never enter the file; the command carries placeholders.

The file also keeps what the app last learned about each machine (what it collects, where its
data goes, how it was reached) so the machine's page can show it without signing in again.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.storage.state import DEFAULT_STATE_DIR

DEFAULT_HISTORY_FILE = DEFAULT_STATE_DIR / "history.json"

KIND_SETUP = "setup"
KIND_CHANGE = "change"
KIND_TAKEOVER = "takeover"
KIND_TEST = "test"
KIND_REMOVE = "remove"

KIND_TITLES = {
    KIND_SETUP: "Set up monitoring",
    KIND_CHANGE: "Change what is collected",
    KIND_TAKEOVER: "Take over a machine",
    KIND_TEST: "Test connection",
    KIND_REMOVE: "Remove monitoring",
}


class ActivityRow(BaseModel):
    """What happened to one machine in one run."""

    machine_id: str = Field(description="VCF Operations resource id of the machine")
    name: str
    os: str = "linux"
    ok: bool = True
    pending: bool = Field(default=False, description="Set up, but VCF Operations has not shown data yet")
    skipped: bool = Field(default=False, description="The run was stopped before this machine was changed")
    left: bool = Field(default=False, description="Left out of the run; nothing was done to it")
    test: bool = False
    note: bool = Field(default=False, description="The line carries a caution the person should read")
    msg: str = ""
    todo: str = ""
    cmd: str = Field(default="", description="The command that does the same thing from the command line")
    collecting: Optional[List[str]] = Field(default=None, description="Catalogue keys the machine collects after this run")
    collector: str = ""
    telegraf: str = ""

    @property
    def counted(self) -> bool:
        """True when something was actually attempted on the machine."""
        return not (self.left or self.skipped)


class ActivityEntry(BaseModel):
    """One thing the app did, to one or several machines."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    at: float = Field(default_factory=time.time)
    title: str
    kind: str
    practice: bool = False
    rows: List[ActivityRow] = Field(default_factory=list)
    detail: str = ""
    log: str = ""
    report: str = Field(default="", description="The full report, as Markdown, for Save a report")
    choices: Dict[str, Any] = Field(default_factory=dict, description="The choices of the run, without secrets")

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def worked(self) -> int:
        return sum(1 for r in self.rows if r.ok and r.counted)

    @property
    def not_done(self) -> int:
        return sum(1 for r in self.rows if not r.counted)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if not r.ok)

    @property
    def changed_something(self) -> bool:
        """False for a test and for a practice run: neither changes a machine."""
        return self.kind != KIND_TEST and not self.practice

    def row_for(self, machine_id: str) -> Optional[ActivityRow]:
        return next((r for r in self.rows if r.machine_id == machine_id), None)

    def when(self, now: Optional[float] = None) -> str:
        return format_when(self.at, now)


def format_when(at: float, now: Optional[float] = None) -> str:
    """'Today, 4:12 PM', 'Yesterday, 4:12 PM' or 'Nov 3, 10:20 AM', in this computer's local time."""
    moment = datetime.fromtimestamp(at)
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    clock = format_clock(at)
    days = (today - moment.date()).days
    if days == 0:
        return f"Today, {clock}"
    if days == 1:
        return f"Yesterday, {clock}"
    stamp = f"{moment.strftime('%b')} {moment.day}"
    if moment.year != today.year:
        stamp += f", {moment.year}"
    return f"{stamp}, {clock}"


def format_clock(at: Optional[float] = None) -> str:
    moment = datetime.fromtimestamp(at if at is not None else time.time())
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


class _HistoryFile(BaseModel):
    entries: List[ActivityEntry] = Field(default_factory=list)
    facts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class HistoryStore:
    """Reads and writes the history file. Newest entry first."""

    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = Path(history_file) if history_file else DEFAULT_HISTORY_FILE

    def _load(self) -> _HistoryFile:
        if not self.history_file.exists():
            return _HistoryFile()
        try:
            return _HistoryFile.model_validate(json.loads(self.history_file.read_text(encoding="utf-8")))
        except Exception:
            # A damaged file must not stop the app; keep it aside so nothing is silently lost
            try:
                os.replace(self.history_file, self.history_file.with_suffix(".damaged.json"))
            except OSError:
                pass
            return _HistoryFile()

    def _save(self, data: _HistoryFile) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.history_file.parent, 0o700)
        except OSError:
            pass
        tmp = self.history_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data.model_dump(mode="json"), indent=2), encoding="utf-8")
        os.replace(tmp, self.history_file)
        try:
            os.chmod(self.history_file, 0o600)
        except OSError:
            pass

    def entries(self) -> List[ActivityEntry]:
        return sorted(self._load().entries, key=lambda e: e.at, reverse=True)

    def get(self, entry_id: str) -> Optional[ActivityEntry]:
        return next((e for e in self._load().entries if e.id == entry_id), None)

    def add(self, entry: ActivityEntry) -> ActivityEntry:
        """Save an entry; an entry with the same id is replaced (a repeated test updates its line)."""
        data = self._load()
        data.entries = [e for e in data.entries if e.id != entry.id]
        data.entries.append(entry)
        self._save(data)
        return entry

    def for_machine(self, machine_id: str) -> List[ActivityEntry]:
        return [e for e in self.entries() if e.row_for(machine_id) is not None]

    def last_change(self, machine_id: str) -> Optional[ActivityEntry]:
        """The newest entry that tried to change this machine (not a test, not a practice run)."""
        for entry in self.for_machine(machine_id):
            row = entry.row_for(machine_id)
            if entry.changed_something and row is not None and row.counted:
                return entry
        return None

    def clear(self) -> None:
        data = self._load()
        data.entries = []
        self._save(data)

    def facts(self, machine_id: str) -> Dict[str, Any]:
        return dict(self._load().facts.get(machine_id, {}))

    def remember(self, machine_id: str, **facts: Any) -> None:
        """Merge what was just learned about a machine. A value of None forgets that fact."""
        data = self._load()
        current = dict(data.facts.get(machine_id, {}))
        for key, value in facts.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        data.facts[machine_id] = current
        self._save(data)
