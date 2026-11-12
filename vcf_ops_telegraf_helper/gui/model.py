"""What the window knows: its settings, the machines, and the sign-ins held for this sitting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VirtualMachineResource
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.workflow.checks import MachineRef
from vcf_ops_telegraf_helper.workflow.collect import BASIC_KEYS, USUAL_SET, ordered
from vcf_ops_telegraf_helper.workflow.plain import STATUS_NONE, Problem
from vcf_ops_telegraf_helper.workflow.plan import DEFAULT_TELEGRAF_VERSION, TELEGRAF_VERSIONS

SETTINGS_KEY = "settings"
AUTO = "auto"


class Settings(BaseModel):
    """The app's own settings. Each is shown at work in the place it applies."""

    defaults: List[str] = Field(default_factory=lambda: list(USUAL_SET))
    collector: str = AUTO
    version: str = DEFAULT_TELEGRAF_VERSION
    existing: str = "keep"  # keep | update
    theme: str = "dark"
    updates: bool = True
    check_ops: bool = True
    cert_file: Optional[str] = None
    check_collector: bool = True
    win_mode: str = "auto"
    ssh_port: int = 22

    @property
    def usual(self) -> bool:
        """True while the default set is the one the app ships with."""
        return ordered(self.defaults) == ordered(USUAL_SET)

    @classmethod
    def load(cls, store: StateStore) -> "Settings":
        raw = store.get_preference(SETTINGS_KEY)
        if not raw:
            return cls()
        try:
            settings = cls.model_validate(json.loads(raw))
        except Exception:
            return cls()
        settings.defaults = [k for k in ordered(settings.defaults) if k in BASIC_KEYS]
        if settings.version not in TELEGRAF_VERSIONS:
            settings.version = DEFAULT_TELEGRAF_VERSION
        return settings

    def save(self, store: StateStore) -> None:
        store.save_preference(SETTINGS_KEY, json.dumps(self.model_dump()))


def collector_key(collector: CollectorInfo) -> str:
    return f"{'group' if collector.is_collector_group else 'one'}:{collector.address}"


@dataclass
class SignIn:
    """A sign-in for machines. Held in memory until the app closes; never written to disk."""

    user: str = ""
    password: str = ""
    key_file: str = ""
    use_key: bool = False

    @property
    def complete(self) -> bool:
        return bool(self.user.strip() and (self.key_file if self.use_key else self.password))

    def copy(self) -> "SignIn":
        return SignIn(self.user, self.password, self.key_file, self.use_key)


@dataclass
class Machine:
    """A machine from the inventory with its plain status."""

    vm: VirtualMachineResource
    status: str = STATUS_NONE
    problem: Optional[Problem] = None
    last: str = ""

    @property
    def id(self) -> str:
        return self.vm.resource_id

    @property
    def name(self) -> str:
        return self.vm.name

    @property
    def address(self) -> str:
        return self.vm.ip_address or self.vm.hostname or self.vm.name

    @property
    def os(self) -> str:
        return "windows" if (self.vm.os_family or "").lower() == "windows" else "linux"

    @property
    def os_name(self) -> str:
        return self.vm.os_name or ("Windows" if self.os == "windows" else "Linux")

    @property
    def off(self) -> bool:
        return not self.vm.is_powered_on

    @property
    def infra(self) -> bool:
        return self.vm.is_platform_vm

    @property
    def selectable(self) -> bool:
        return not (self.off or self.infra)

    def ref(self) -> MachineRef:
        return MachineRef(
            machine_id=self.id, name=self.name, address=self.address, os=self.os, vm_mor=self.vm.vm_mor,
            vc_id=self.vm.vc_id, collector_address=self.vm.collector_address, collector_group=self.vm.collector_group,
        )


@dataclass
class Session:
    """Sign-ins given during this sitting, one per kind of machine plus any given for one machine."""

    family: Dict[str, SignIn] = field(default_factory=dict)
    machine: Dict[str, SignIn] = field(default_factory=dict)

    def for_machine(self, machine: Machine) -> Optional[SignIn]:
        return self.machine.get(machine.id) or self.family.get(machine.os)

    def clear(self) -> None:
        self.family.clear()
        self.machine.clear()
