"""Tests for configuration ownership and blast radius isolation."""

from __future__ import annotations

from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import MonitoringConfig
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


def test_managed_fragment_isolation():
    """Verify that existing user-defined files in telegraf.d are never modified or deleted."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="node01.corp.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    monitoring = MonitoringConfig()

    # Pre-populate mock executor with existing customer configuration files
    executor = MockExecutor(connected=True, telegraf_installed=True)
    executor.uploaded_files["/etc/telegraf/telegraf.d/custom-nginx.conf"] = "[[inputs.nginx]]\n"
    executor.uploaded_files["/etc/telegraf/telegraf.d/custom-postgres.conf"] = "[[inputs.postgresql]]\n"
    executor.uploaded_files["/etc/telegraf/telegraf.conf"] = "[agent]\n  interval = '10s'\n"

    adapter = MockVCFOpsIntegration(env=env, connected=True)

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
    )
    summary = wf.run()

    assert summary.success

    # Check managed files
    assert "/etc/telegraf/telegraf.d/vcf-helper-system.conf" in executor.uploaded_files
    assert "/etc/telegraf/telegraf.d/cloudproxy-http.conf" in executor.uploaded_files

    # Confirm pre-existing customer files remain intact and untouched
    assert executor.uploaded_files["/etc/telegraf/telegraf.d/custom-nginx.conf"] == "[[inputs.nginx]]\n"
    assert executor.uploaded_files["/etc/telegraf/telegraf.d/custom-postgres.conf"] == "[[inputs.postgresql]]\n"
    assert executor.uploaded_files["/etc/telegraf/telegraf.conf"] == "[agent]\n  interval = '10s'\n"


def test_workflow_application_idempotency():
    """Verify that repeated workflow runs do not drift or create duplicate entries."""
    env = VCFEnvironment(
        name="test-env",
        url="https://vcf-ops.local",
        username="admin",
        collector=CollectorInfo(address="10.10.10.50"),
    )
    target = EndpointTarget(
        hostname="node01.corp.local",
        os_family=OSFamily.LINUX,
        connection_method=ConnectionMethod.MOCK,
    )
    monitoring = MonitoringConfig()
    executor = MockExecutor(connected=True, telegraf_installed=True)
    adapter = MockVCFOpsIntegration(env=env, connected=True)

    # First run
    wf1 = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
    )
    summary1 = wf1.run()
    assert summary1.success
    files_run1 = dict(executor.uploaded_files)

    # Second run against same executor
    wf2 = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
    )
    summary2 = wf2.run()
    assert summary2.success
    files_run2 = dict(executor.uploaded_files)

    # Content and files must be completely identical
    assert files_run1 == files_run2
