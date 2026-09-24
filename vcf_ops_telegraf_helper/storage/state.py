"""Lightweight local state storage for VCF Operations Open Telegraf Helper.

Persists non-sensitive helper settings, recent target hostnames, and known
VCF Operations environments in ~/.config/vcf-ops-telegraf-helper/state.json.
Secrets such as passwords and session tokens are stripped before saving to disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from vcf_ops_telegraf_helper.models.vcf import VCFEnvironment


DEFAULT_STATE_DIR = Path.home() / ".config" / "vcf-ops-telegraf-helper"
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "state.json"


class LocalAppState(BaseModel):
    """Schema for local application preferences and saved environments."""

    environments: Dict[str, VCFEnvironment] = Field(default_factory=dict)
    recent_endpoints: List[str] = Field(default_factory=list)
    preferences: Dict[str, str] = Field(default_factory=dict)


class StateStore:
    """Manages reading and writing local helper application state."""

    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self.state_file = state_file

    def _ensure_dir(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_file.parent, 0o700)
        except OSError:
            pass

    def load(self) -> LocalAppState:
        """Load state from disk, or return empty default state if absent."""
        if not self.state_file.exists():
            return LocalAppState()

        try:
            content = self.state_file.read_text(encoding="utf-8")
            data = json.loads(content)
            return LocalAppState.model_validate(data)
        except Exception:
            # Fall back safely if corrupted
            return LocalAppState()

    def save(self, state: LocalAppState) -> None:
        """Persist state to disk safely, stripping any plaintext passwords."""
        self._ensure_dir()

        # Sanitize environments to guarantee passwords are never saved to disk
        sanitized_state = state.model_copy(deep=True)
        for env in sanitized_state.environments.values():
            env.password = None
            env.token = None

        content = json.dumps(sanitized_state.model_dump(), indent=2)
        self.state_file.write_text(content, encoding="utf-8")
        try:
            os.chmod(self.state_file, 0o600)
        except OSError:
            pass

    def save_environment(self, env: VCFEnvironment) -> None:
        """Add or update a saved VCF Operations environment."""
        state = self.load()
        state.environments[env.name] = env
        self.save(state)

    def get_environment(self, name: str) -> Optional[VCFEnvironment]:
        """Retrieve a saved environment by name."""
        state = self.load()
        return state.environments.get(name)

    def list_environments(self) -> List[VCFEnvironment]:
        """List all saved environments."""
        state = self.load()
        return list(state.environments.values())

    def record_endpoint(self, hostname: str) -> None:
        """Record an endpoint in recent targets, preserving up to 10 entries."""
        state = self.load()
        if hostname in state.recent_endpoints:
            state.recent_endpoints.remove(hostname)
        state.recent_endpoints.insert(0, hostname)
        state.recent_endpoints = state.recent_endpoints[:10]
        self.save(state)
