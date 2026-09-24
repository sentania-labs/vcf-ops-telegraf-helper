"""Tests for local state persistence and secret protection."""

from __future__ import annotations

from pathlib import Path
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.storage.state import StateStore


def test_state_store_strips_passwords_and_tokens(tmp_path: Path):
    """Verify that StateStore strips plain passwords and tokens before writing to disk."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)

    env = VCFEnvironment(
        name="prod-vcf",
        url="https://vcf-prod.corp.local",
        username="admin",
        password="super_secret_password_do_not_persist",
        token="temporary_auth_token_12345",
        collector=CollectorInfo(address="10.20.30.40"),
    )

    store.save_environment(env)

    # Read the raw file directly from disk
    raw_content = state_file.read_text(encoding="utf-8")
    assert "super_secret_password_do_not_persist" not in raw_content
    assert "temporary_auth_token_12345" not in raw_content
    assert "prod-vcf" in raw_content

    # Load back through store and check
    loaded = store.get_environment("prod-vcf")
    assert loaded is not None
    assert loaded.password is None
    assert loaded.token is None
    assert loaded.url == "https://vcf-prod.corp.local"


def test_state_store_recent_endpoints(tmp_path: Path):
    """Verify recording endpoints maintains order and uniqueness."""
    state_file = tmp_path / "state.json"
    store = StateStore(state_file=state_file)

    store.record_endpoint("host1.local")
    store.record_endpoint("host2.local")
    store.record_endpoint("host1.local")

    state = store.load()
    assert state.recent_endpoints == ["host1.local", "host2.local"]
