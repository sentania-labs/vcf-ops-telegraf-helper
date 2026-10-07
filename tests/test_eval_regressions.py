"""Regressions from the 0.7.3 endpoint evaluation."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from test_workflow import _create_test_workflow
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.base import CommandResult
from vcf_ops_telegraf_helper.models.workflow import WorkflowOptions, StageStatus
from vcf_ops_telegraf_helper.adapters.vcf91 import VCF91OpenTelegrafIntegration


def test_password_does_not_offer_ambient_keys():
    ex = SSHExecutor('host', username='operator', password='wrong')
    with patch('paramiko.SSHClient') as client:
        ex._ensure_connected()
    args = client.return_value.connect.call_args.kwargs
    assert args['look_for_keys'] is False
    assert args['allow_agent'] is False


def test_privileged_download_reads_with_sudo():
    ex = SSHExecutor('host', username='operator')
    with patch.object(ex, 'execute', return_value=CommandResult(exit_code=0, stdout='private key')) as run:
        assert ex.download('/etc/telegraf/telegraf.d/key.pem') == 'private key'
    assert run.call_args.args[0] == 'sudo -n cat -- /etc/telegraf/telegraf.d/key.pem'


def test_desktop_tls_options_do_not_escape_to_agent():
    wf = _create_test_workflow()
    wf.env.verify_ssl = False
    wf.env.ca_cert_path = r'C:\Users\operator\root.pem'
    wf.detect_telegraf()
    wf.configure_vcf_output()
    wf.artifacts.ca_cert_content = 'collector CA'
    wf.render_inputs()
    assert 'insecure_skip_verify = false' in wf.vcf_conf_content
    assert wf.env.ca_cert_path not in wf.vcf_conf_content
    assert '/etc/telegraf/telegraf.d/ca.pem' in wf.vcf_conf_content


def test_dry_run_does_not_claim_verification_pass():
    wf = _create_test_workflow(options=WorkflowOptions(dry_run=True))
    result = wf.run()
    assert result.stages[-1].status == StageStatus.SKIPPED


def test_tls_failure_names_tls():
    wf = _create_test_workflow()
    session = MagicMock()
    session.get.side_effect = requests.exceptions.SSLError('certificate verify failed')
    adapter = VCF91OpenTelegrafIntegration(wf.env, session=session)
    with pytest.raises(RuntimeError, match='TLS'):
        adapter.validate_connection()


def test_existing_inputs_survive_rerun():
    wf = _create_test_workflow()
    wf.detect_telegraf()
    path = wf.discovery.config_dir + '/vcf-helper-system.conf'
    deployed = '[[inputs.win_services]]\nservice_names = ["MSSQLSERVER", "SQLSERVERAGENT", "telegraf"]\n'
    wf.executor.uploaded_files[path] = deployed
    wf.render_inputs()
    assert wf.system_conf_content == deployed


def test_ingestion_rejects_stale_samples():
    wf = _create_test_workflow()
    wf.env.token = 'test-token'
    session = MagicMock()
    session.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {'resourceList': [{'identifier': 'agent'}]}),
        MagicMock(status_code=200, json=lambda: {'values': [{'stat-list': {'stat': [{'timestamps': [1000], 'data': [1]}]}}]}),
    ]
    adapter = VCF91OpenTelegrafIntegration(wf.env, session)
    assert adapter.verify_ingestion('node', since=2.0) == 'PENDING'


def test_report_includes_failure_detail():
    wf = _create_test_workflow(connected=False)
    summary = wf.run()
    summary.stages[0].details = 'could not read certificate'
    assert 'could not read certificate' in summary.to_markdown()


def test_purge_failure_is_not_success():
    from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow
    wf = _create_test_workflow()
    ex = MagicMock()
    ex.execute.return_value = CommandResult(exit_code=1, stderr='package database locked')
    uninstall = UninstallEndpointWorkflow(target=wf.target, executor=ex)
    assert uninstall.remove_package().status == StageStatus.FAIL


def test_windows_install_directory_flows_into_output():
    from vcf_ops_telegraf_helper.models.endpoint import OSFamily
    from vcf_ops_telegraf_helper.workflow.windows import WindowsTelegrafDetection
    wf = _create_test_workflow()
    wf.target.os_family = OSFamily.WINDOWS
    detected = WindowsTelegrafDetection(installed=True, binary_path=r'C:\Program Files\Telegraf\telegraf.exe', service_name='telegraf')
    with patch('vcf_ops_telegraf_helper.workflow.engine.detect_windows_telegraf', return_value=detected):
        assert wf.detect_telegraf().status == StageStatus.PASS
    wf.configure_vcf_output()
    wf.artifacts.ca_cert_content = 'CA'
    wf.render_inputs()
    assert wf.discovery.config_dir == r'C:\Program Files\Telegraf\telegraf.d'
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    output = tomllib.loads(wf.vcf_conf_content)
    assert output['outputs']['http'][0]['tls_ca'] == r'C:\Program Files\Telegraf\telegraf.d\ca.pem'


def test_ops_managed_agent_refused_before_preparation():
    from vcf_ops_telegraf_helper.models.endpoint import OSFamily
    from vcf_ops_telegraf_helper.workflow.windows import WindowsTelegrafDetection
    wf = _create_test_workflow()
    wf.target.os_family = OSFamily.WINDOWS
    detected = WindowsTelegrafDetection(installed=True, binary_path=r'C:\VMware\UCP\ucp-telegraf\telegraf.exe', service_name='ucp-telegraf')
    with patch('vcf_ops_telegraf_helper.workflow.engine.detect_windows_telegraf', return_value=detected):
        summary = wf.run()
    assert not summary.success
    assert 'managed by VCF Operations' in summary.stages[-1].message
    assert not wf.executor.uploaded_files


def test_preview_is_exactly_the_applied_fragments():
    wf = _create_test_workflow()
    shown = []
    wf.preview_callback = lambda: shown.append((wf.system_conf_content, wf.vcf_conf_content))
    assert wf.run().success
    config = wf.discovery.config_dir
    assert shown == [(wf.executor.uploaded_files[config + '/vcf-helper-system.conf'],
                      wf.executor.uploaded_files[config + '/cloudproxy-http.conf'])]


def test_native_trust_adapter_and_explicit_bundle(monkeypatch):
    from vcf_ops_telegraf_helper.security.tls import NativeTrustAdapter
    import truststore
    adapter = NativeTrustAdapter()
    request = requests.Request('GET', 'https://ops.example').prepare()
    _, attrs = adapter.build_connection_pool_key_attributes(request, True)
    assert isinstance(attrs['ssl_context'], truststore.SSLContext)
    _, attrs = adapter.build_connection_pool_key_attributes(request, '/custom/ca.pem')
    assert attrs['ca_certs'] == '/custom/ca.pem'
    assert 'ssl_context' not in attrs
    _, attrs = adapter.build_connection_pool_key_attributes(request, False)
    assert attrs['cert_reqs'] == 'CERT_NONE'


def test_deployed_database_password_not_in_report_diff():
    wf = _create_test_workflow()
    wf.detect_telegraf()
    wf.executor.uploaded_files[wf.discovery.config_dir + '/vcf-helper-system.conf'] = (
        '[[inputs.mysql]]\nservers = ["monitor:SYNTHETIC_PASSWORD@tcp(db:3306)/"]\n'
    )
    summary = wf.run()
    assert 'SYNTHETIC_PASSWORD' not in summary.to_json()
    assert 'SYNTHETIC_PASSWORD' not in summary.to_markdown()
    assert '[REDACTED]' in summary.to_markdown()
