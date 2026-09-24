"""Tests for workflow engine orchestration and safety gates."""

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
from vcf_ops_telegraf_helper.models.workflow import (
    StageStatus,
    WorkflowOptions,
    WorkflowStage,
)
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


def _create_test_workflow(
    connected: bool = True,
    telegraf_installed: bool = True,
    vcf_connected: bool = True,
    options: WorkflowOptions | None = None,
) -> ConfigureEndpointWorkflow:
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
    executor = MockExecutor(connected=connected, telegraf_installed=telegraf_installed)
    adapter = MockVCFOpsIntegration(env=env, connected=vcf_connected)

    return ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
        options=options or WorkflowOptions(),
    )


def test_workflow_full_success_sequence():
    """Verify all 8 stages execute in order and succeed under normal conditions."""
    wf = _create_test_workflow()
    summary = wf.run()

    assert summary.success
    assert len(summary.stages) == 8

    # Verify stage order
    expected_stages = [
        WorkflowStage.CONNECT,
        WorkflowStage.DETECT,
        WorkflowStage.PREPARE_VCF,
        WorkflowStage.RENDER_INPUTS,
        WorkflowStage.VALIDATE,
        WorkflowStage.APPLY,
        WorkflowStage.RESTART,
        WorkflowStage.VERIFY,
    ]
    for actual, expected in zip(summary.stages, expected_stages):
        assert actual.stage == expected
        assert actual.status in (StageStatus.PASS, StageStatus.SKIPPED)

    # Verification checklist
    assert summary.verifications["Telegraf installed"] == "PASS"
    assert summary.verifications["Config valid"] == "PASS"
    assert summary.verifications["Service running"] == "PASS"
    assert summary.verifications["Collector reachable"] == "PASS"


def test_workflow_connection_failure_aborts_early():
    """Verify connection failure at stage 1 halts the pipeline before applying any changes."""
    wf = _create_test_workflow(connected=False)
    summary = wf.run()

    assert not summary.success
    assert len(summary.stages) == 1
    assert summary.stages[0].stage == WorkflowStage.CONNECT
    assert summary.stages[0].status == StageStatus.FAIL

    # Confirm apply was never executed
    assert len(summary.managed_files) == 0


def test_workflow_dry_run_skips_mutation():
    """Verify dry-run mode executes validation but skips apply and restart."""
    opts = WorkflowOptions(dry_run=True)
    wf = _create_test_workflow(options=opts)
    summary = wf.run()

    assert summary.success
    apply_stage = next(s for s in summary.stages if s.stage == WorkflowStage.APPLY)
    restart_stage = next(s for s in summary.stages if s.stage == WorkflowStage.RESTART)

    assert apply_stage.status == StageStatus.SKIPPED
    assert restart_stage.status == StageStatus.SKIPPED
    assert len(summary.managed_files) == 0


def test_workflow_honest_unknown_telemetry():
    """Verify that when telemetry cannot be confirmed immediately, UNKNOWN is reported honestly."""
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
    # Mock VCF Ops reports UNKNOWN for telemetry ingestion
    adapter = MockVCFOpsIntegration(env=env, connected=True, ingestion_status="UNKNOWN")

    wf = ConfigureEndpointWorkflow(
        environment=env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
    )
    summary = wf.run()
    assert summary.verifications["VCF Ops ingestion"] == "UNKNOWN"
