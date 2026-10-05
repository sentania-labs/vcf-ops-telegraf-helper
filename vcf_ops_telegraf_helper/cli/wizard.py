"""Interactive terminal wizard for guided onboarding."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Optional
from rich.console import Console
from rich.prompt import Confirm, Prompt

from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.cli.display import (
    RichTerminalProgressReporter,
    display_banner,
    display_preview,
    display_summary,
)
from vcf_ops_telegraf_helper.executors.local import LocalExecutor
from vcf_ops_telegraf_helper.executors.mock import MockExecutor
from vcf_ops_telegraf_helper.executors.ssh import SSHExecutor
from vcf_ops_telegraf_helper.executors.winrm import WinRMExecutor
from vcf_ops_telegraf_helper.models.endpoint import (
    ConnectionMethod,
    EndpointTarget,
    OSFamily,
)
from vcf_ops_telegraf_helper.models.monitoring import (
    CpuInputConfig,
    DiskInputConfig,
    MemInputConfig,
    MonitoringConfig,
    NetInputConfig,
    SwapInputConfig,
    SystemInputConfig,
    WinPerfCountersInputConfig,
    WinServicesInputConfig,
)
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import WorkflowOptions
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


def run_wizard(console: Optional[Console] = None) -> None:
    """Run the step-by-step interactive configuration wizard."""
    con = console or Console()
    store = StateStore()

    display_banner(con)

    # -------------------------------------------------------------------------
    # Step 1: VCF Operations
    # -------------------------------------------------------------------------
    con.print("\n[bold blue]Step 1: VCF Operations Environment[/bold blue]")
    saved_envs = store.list_environments()
    default_url = saved_envs[0].url if saved_envs else "https://vcf-ops.local"
    default_collector = saved_envs[0].collector.address if saved_envs else "10.10.10.50"

    vcf_url = Prompt.ask("VCF Operations URL", default=default_url, console=con)
    vcf_user = Prompt.ask("VCF Operations Username", default="admin", console=con)
    vcf_auth_source = Prompt.ask("VCF Operations Login Source", default="local", console=con)
    env_vcf_pass = os.environ.get("VCF_PASS")
    if env_vcf_pass:
        con.print("  [dim]Using VCF password from VCF_PASS environment variable[/dim]")
        vcf_pass = env_vcf_pass
    else:
        vcf_pass = Prompt.ask("VCF Operations Password", password=True, console=con)
    collector_ip = Prompt.ask("Cloud Proxy / Collector IP or FQDN", default=default_collector, console=con)
    verify_ssl = Confirm.ask("Verify TLS/SSL certificates?", default=True, console=con)

    vcf_env = VCFEnvironment(
        name="current",
        url=vcf_url,
        username=vcf_user,
        password=vcf_pass,
        auth_source=vcf_auth_source,
        collector=CollectorInfo(address=collector_ip),
        verify_ssl=verify_ssl,
    )

    con.print("[dim]Validating VCF Operations connection...[/dim]")
    adapter = get_adapter(vcf_env)
    if adapter.validate_connection():
        version = adapter.detect_version()
        con.print(f"[bold green]✓[/bold green] VCF Operations reachable (version: [cyan]{version}[/cyan])")
    else:
        con.print("[bold yellow]![/bold yellow] Could not reach VCF Operations API. Proceeding with offline/simulated parameters.")

    # -------------------------------------------------------------------------
    # Step 2: Target Endpoint
    # -------------------------------------------------------------------------
    con.print("\n[bold blue]Step 2: Target Endpoint[/bold blue]")
    target_host = Prompt.ask("Target Hostname or IP", default="localhost", console=con)
    conn_choice = Prompt.ask(
        "Connection method",
        choices=["ssh", "winrm", "mock", "local"],
        default="mock" if target_host == "localhost" else "ssh",
        console=con,
    )

    ssh_user: Optional[str] = None
    ssh_pass: Optional[str] = None
    ssh_key: Optional[str] = None
    winrm_ssl: bool = False
    auto_install: bool = False
    conn_method = ConnectionMethod(conn_choice)
    is_win = conn_method == ConnectionMethod.WINRM

    if conn_method == ConnectionMethod.SSH:
        ssh_user = Prompt.ask("SSH Username", default="root", console=con)
        env_ssh_pass = os.environ.get("SSH_PASS") or os.environ.get("WINRM_PASS")
        if env_ssh_pass:
            ssh_pass = env_ssh_pass
            con.print("  [dim]Using SSH password from environment variable[/dim]")
        else:
            use_key = Confirm.ask("Use SSH private key authentication?", default=True, console=con)
            if use_key:
                default_key = str(Path.home() / ".ssh" / "id_rsa")
                ssh_key = Prompt.ask("SSH Key Path", default=default_key, console=con)
            else:
                ssh_pass = Prompt.ask("SSH Password", password=True, console=con)
    elif conn_method == ConnectionMethod.WINRM:
        ssh_user = Prompt.ask("WinRM Username", default="Administrator", console=con)
        env_winrm_pass = os.environ.get("WINRM_PASS") or os.environ.get("SSH_PASS")
        if env_winrm_pass:
            ssh_pass = env_winrm_pass
            con.print("  [dim]Using WinRM password from environment variable[/dim]")
        else:
            ssh_pass = Prompt.ask("WinRM Password", password=True, console=con)
        winrm_ssl = Confirm.ask("Use HTTPS for WinRM (port 5986)?", default=False, console=con)

    telegraf_ver = "1.40.1"
    if conn_method in (ConnectionMethod.SSH, ConnectionMethod.WINRM):
        auto_install = Confirm.ask(
            "Install open-source Telegraf agent if missing (InfluxData official distribution)?",
            default=True,
            console=con,
        )
        if auto_install:
            telegraf_ver = Prompt.ask("Telegraf release version to install", default="1.40.1", console=con).strip() or "1.40.1"

    target = EndpointTarget(
        hostname=target_host,
        os_family=OSFamily.WINDOWS if is_win else OSFamily.LINUX,
        connection_method=conn_method,
        username=ssh_user,
        password=ssh_pass,
        key_filename=ssh_key,
        winrm_use_ssl=winrm_ssl,
        install_telegraf=auto_install,
        telegraf_version=telegraf_ver,
    )

    # Instantiate chosen executor
    if conn_method == ConnectionMethod.MOCK:
        executor = MockExecutor(connected=True, telegraf_installed=True)
    elif conn_method == ConnectionMethod.LOCAL:
        executor = LocalExecutor()
    elif conn_method == ConnectionMethod.WINRM:
        executor = WinRMExecutor(
            hostname=target_host,
            port=target.port,
            username=target.username,
            password=target.password,
            use_ssl=winrm_ssl,
        )
    else:
        executor = SSHExecutor(
            hostname=target_host,
            username=ssh_user,
            password=ssh_pass,
            key_filename=ssh_key,
        )

    # -------------------------------------------------------------------------
    # Step 3: Monitoring Selection
    # -------------------------------------------------------------------------
    con.print("\n[bold blue]Step 3: Monitoring Selection[/bold blue]")
    is_win = target.os_family == OSFamily.WINDOWS
    apply_baseline = Confirm.ask("Apply recommended OS baseline metrics?", default=True, console=con)

    if is_win:
        if apply_baseline:
            enable_win_perf = True
            enable_win_svc = True
            svc_names = ["telegraf"]
        else:
            enable_win_perf = Confirm.ask("Enable Windows Performance Counters (Broadcom template)?", default=True, console=con)
            enable_win_svc = Confirm.ask("Enable Windows Services monitoring?", default=True, console=con)
            svc_input = Prompt.ask("Service names to monitor (comma-separated)", default="telegraf", console=con)
            svc_names = [s.strip() for s in svc_input.split(",") if s.strip()] or ["telegraf"]

        monitoring = MonitoringConfig(
            cpu=CpuInputConfig(enabled=False),
            mem=MemInputConfig(enabled=False),
            disk=DiskInputConfig(enabled=False),
            net=NetInputConfig(enabled=False),
            system=SystemInputConfig(enabled=False),
            swap=SwapInputConfig(enabled=False),
            win_perf_counters=WinPerfCountersInputConfig(enabled=enable_win_perf),
            win_services=WinServicesInputConfig(enabled=enable_win_svc, service_names=svc_names),
        )
    else:
        if apply_baseline:
            enable_cpu = True
            enable_mem = True
            enable_disk = True
            enable_net = True
            enable_sys = True
            enable_swap = True
        else:
            enable_cpu = Confirm.ask("Enable CPU monitoring?", default=True, console=con)
            enable_mem = Confirm.ask("Enable Memory monitoring?", default=True, console=con)
            enable_disk = Confirm.ask("Enable Disk monitoring?", default=True, console=con)
            enable_net = Confirm.ask("Enable Network monitoring?", default=True, console=con)
            enable_sys = Confirm.ask("Enable System load and uptime monitoring?", default=True, console=con)
            enable_swap = Confirm.ask("Enable Swap monitoring?", default=True, console=con)

        monitoring = MonitoringConfig(
            cpu=CpuInputConfig(enabled=enable_cpu),
            mem=MemInputConfig(enabled=enable_mem),
            disk=DiskInputConfig(enabled=enable_disk),
            net=NetInputConfig(enabled=enable_net),
            system=SystemInputConfig(enabled=enable_sys),
            swap=SwapInputConfig(enabled=enable_swap),
            win_perf_counters=WinPerfCountersInputConfig(enabled=False),
            win_services=WinServicesInputConfig(enabled=False),
        )

    # -------------------------------------------------------------------------
    # Step 4: Review
    # -------------------------------------------------------------------------
    con.print("\n[bold blue]Step 4: Configuration Review & Preview[/bold blue]")
    system_toml = TelegrafRenderer.render_system_inputs(monitoring)
    vcf_toml = TelegrafRenderer.render_vcf_output(
        collector_address=collector_ip,
        hostname=target.registered_hostname or target_host,
        ip=target_host,
        verify_ssl=verify_ssl,
        is_windows=is_win,
    )

    if is_win:
        planned_cmds = [
            "mkdir C:\\telegraf\\telegraf.d",
            "upload vcf-helper-system.conf -> C:\\telegraf\\telegraf.d\\vcf-helper-system.conf",
            "upload cloudproxy-http.conf -> C:\\telegraf\\telegraf.d\\cloudproxy-http.conf",
            "& 'C:\\telegraf\\telegraf.exe' --test --config 'C:\\telegraf\\telegraf.conf' --config-directory 'C:\\telegraf\\telegraf.d'",
            "Restart-Service telegraf -Force",
        ]
    else:
        planned_cmds = [
            "mkdir -p /etc/telegraf/telegraf.d",
            "upload vcf-helper-system.conf -> /etc/telegraf/telegraf.d/vcf-helper-system.conf",
            "upload cloudproxy-http.conf -> /etc/telegraf/telegraf.d/cloudproxy-http.conf",
            "/usr/bin/telegraf --test --config /etc/telegraf/telegraf.conf --config-directory /etc/telegraf/telegraf.d",
            "systemctl restart telegraf",
        ]
    display_preview(con, target_host, collector_ip, system_toml, vcf_toml, planned_cmds)

    proceed = Confirm.ask("\nProceed with execution?", default=True, console=con)
    if not proceed:
        con.print("[yellow]Execution aborted by user.[/yellow]")
        return

    # -------------------------------------------------------------------------
    # Step 5: Execute
    # -------------------------------------------------------------------------
    con.print("\n[bold blue]Step 5: Guided Execution[/bold blue]")
    reporter = RichTerminalProgressReporter(con)
    options = WorkflowOptions(
        restart_service=True,
        install_telegraf=auto_install,
        telegraf_version=telegraf_ver,
    )

    workflow = ConfigureEndpointWorkflow(
        environment=vcf_env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
        options=options,
        reporter=reporter,
    )

    summary = workflow.run()

    # -------------------------------------------------------------------------
    # Step 6: Summary & Export
    # -------------------------------------------------------------------------
    con.print("\n[bold blue]Step 6: Run Summary[/bold blue]")
    display_summary(con, summary)

    # Record recent target in local state
    store.record_endpoint(target_host)
    store.save_environment(vcf_env)

    # Export options
    export_choice = Confirm.ask("\nExport run summary report?", default=True, console=con)
    if export_choice:
        md_file = Prompt.ask("Markdown summary filename", default=f"summary-{target_host}.md", console=con)
        Path(md_file).write_text(summary.to_markdown(), encoding="utf-8")
        con.print(f"[bold green]✓[/bold green] Markdown report exported to [cyan]{md_file}[/cyan]")

        json_choice = Confirm.ask("Export JSON report as well?", default=False, console=con)
        if json_choice:
            json_file = Prompt.ask("JSON summary filename", default=f"summary-{target_host}.json", console=con)
            Path(json_file).write_text(summary.to_json(), encoding="utf-8")
            con.print(f"[bold green]✓[/bold green] JSON report exported to [cyan]{json_file}[/cyan]")
