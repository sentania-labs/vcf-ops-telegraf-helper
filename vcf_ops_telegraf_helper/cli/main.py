"""Main CLI entry points for VCF Operations Open Telegraf Helper."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Optional
import click
from rich.console import Console
from rich.syntax import Syntax

from vcf_ops_telegraf_helper import __version__
from vcf_ops_telegraf_helper.adapters.factory import get_adapter
from vcf_ops_telegraf_helper.adapters.mock import MockVCFOpsIntegration
from vcf_ops_telegraf_helper.cli.display import (
    RichTerminalProgressReporter,
    display_banner,
    display_preview,
    display_summary,
)
from vcf_ops_telegraf_helper.cli.wizard import run_wizard
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
    ApacheInputConfig,
    CpuInputConfig,
    DiskInputConfig,
    DiskIoInputConfig,
    DockerInputConfig,
    MemInputConfig,
    MonitoringConfig,
    MssqlInputConfig,
    MysqlInputConfig,
    NetInputConfig,
    NginxInputConfig,
    PingInputConfig,
    PostgresqlInputConfig,
    ProcessesInputConfig,
    SwapInputConfig,
    SystemInputConfig,
    WinPerfCountersInputConfig,
    WinServicesInputConfig,
)
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import WorkflowOptions
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.validation.validator import Validator
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


console = Console()


def _is_windows_double_click() -> bool:
    """Check if process was launched directly from Windows Explorer (double click)."""
    if sys.platform != "win32" or not sys.stdout.isatty():
        return False
    try:
        import ctypes

        process_list = (ctypes.c_uint * 4)()
        count = ctypes.windll.kernel32.GetConsoleProcessList(process_list, 4)
        return count <= 2
    except Exception:
        return False


@click.group(invoke_without_command=True)
@click.version_option(__version__, "--version", "-v", prog_name="vcf-ops-telegraf-helper")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """VCF Operations Open Telegraf Helper: Local onboarding utility."""
    if ctx.invoked_subcommand is None:
        # If double-clicked in Windows Explorer, launch native GUI by default.
        if _is_windows_double_click() and not os.environ.get("VCF_HELPER_NO_GUI"):
            if sys.platform == "win32":
                try:
                    import ctypes

                    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
                    if hwnd:
                        ctypes.windll.user32.ShowWindow(hwnd, 0)
                    ctypes.windll.kernel32.FreeConsole()
                except Exception:
                    pass
            try:
                from vcf_ops_telegraf_helper.gui.app import run_gui

                sys.exit(run_gui())
            except Exception as exc:
                display_banner(console)
                console.print(f"[bold red]Failed to launch GUI:[/bold red] {exc}")
                click.echo(ctx.get_help())
                try:
                    input("\nPress Enter to exit...")
                except (EOFError, KeyboardInterrupt):
                    pass
                sys.exit(1)

        display_banner(console)
        click.echo(ctx.get_help())


@cli.command("wizard")
def wizard_cmd() -> None:
    """Launch the interactive terminal onboarding wizard."""
    run_wizard(console)


@cli.command("gui")
@click.option("--theme", type=click.Choice(["dark", "light"]), default="dark", help="Initial Lattice theme")
def gui_cmd(theme: str) -> None:
    """Launch the native desktop GUI styled with Lattice."""
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
            ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass
    from vcf_ops_telegraf_helper.gui.app import run_gui
    sys.exit(run_gui(theme=theme))


def resolve_monitoring_config(
    is_win: bool,
    no_baseline: bool,
    cpu: Optional[bool] = None,
    mem: Optional[bool] = None,
    disk: Optional[bool] = None,
    net: Optional[bool] = None,
    system: Optional[bool] = None,
    swap: Optional[bool] = None,
    diskio: Optional[bool] = None,
    processes: Optional[bool] = None,
    win_perf: Optional[bool] = None,
    win_services: Optional[str] = None,
    no_win_services: bool = False,
    nginx: Optional[str] = None,
    apache: Optional[str] = None,
    mysql: Optional[str] = None,
    postgres: Optional[str] = None,
    mssql: Optional[str] = None,
    docker: Optional[str] = None,
    ping: Optional[str] = None,
) -> MonitoringConfig:
    """Resolve additive plugin configuration against target OS baseline."""
    if no_baseline:
        effective_cpu = False
        effective_mem = False
        effective_disk = False
        effective_net = False
        effective_system = False
        effective_swap = False
        effective_diskio = False
        effective_proc = False
        effective_win_perf = False
        effective_win_svc = False
    else:
        effective_cpu = not is_win
        effective_mem = not is_win
        effective_disk = not is_win
        effective_net = not is_win
        effective_system = not is_win
        effective_swap = not is_win
        effective_diskio = False
        effective_proc = False
        effective_win_perf = is_win
        effective_win_svc = is_win

    if cpu is not None:
        effective_cpu = cpu
    if mem is not None:
        effective_mem = mem
    if disk is not None:
        effective_disk = disk
    if net is not None:
        effective_net = net
    if system is not None:
        effective_system = system
    if swap is not None:
        effective_swap = swap
    if diskio is not None:
        effective_diskio = diskio
    if processes is not None:
        effective_proc = processes
    if win_perf is not None:
        effective_win_perf = win_perf
    if win_services is not None:
        effective_win_svc = True
    if no_win_services:
        effective_win_svc = False

    svc_list = [s.strip() for s in win_services.split(",") if s.strip()] if win_services else ["telegraf"]

    return MonitoringConfig(
        cpu=CpuInputConfig(enabled=effective_cpu),
        mem=MemInputConfig(enabled=effective_mem),
        disk=DiskInputConfig(enabled=effective_disk),
        net=NetInputConfig(enabled=effective_net),
        system=SystemInputConfig(enabled=effective_system),
        swap=SwapInputConfig(enabled=effective_swap),
        diskio=DiskIoInputConfig(enabled=effective_diskio),
        processes=ProcessesInputConfig(enabled=effective_proc),
        win_perf_counters=WinPerfCountersInputConfig(enabled=effective_win_perf),
        win_services=WinServicesInputConfig(enabled=effective_win_svc, service_names=svc_list),
        nginx=NginxInputConfig(enabled=bool(nginx), urls=[nginx] if nginx else ["http://localhost/status"]),
        apache=ApacheInputConfig(enabled=bool(apache), urls=[apache] if apache else ["http://localhost/server-status?auto"]),
        mysql=MysqlInputConfig(enabled=bool(mysql), servers=[mysql] if mysql else ["tcp(127.0.0.1:3306)/"]),
        postgresql=PostgresqlInputConfig(enabled=bool(postgres), address=postgres or "host=localhost user=postgres sslmode=disable"),
        mssql=MssqlInputConfig(enabled=bool(mssql), servers=[mssql] if mssql else ["Server=127.0.0.1;Port=1433;User Id=sa;Password=;app name=telegraf;log=1;"]),
        docker=DockerInputConfig(enabled=bool(docker), endpoint=docker or ("npipe:////./pipe/docker_engine" if is_win else "unix:///var/run/docker.sock")),
        ping=PingInputConfig(enabled=bool(ping), urls=[ping] if ping else ["10.10.10.1"]),
    )


@cli.command("run")
@click.option("--vcf-url", required=True, help="VCF Operations URL, e.g. https://vcf-ops.local")
@click.option("--vcf-user", default="admin", help="VCF Operations username")
@click.option("--vcf-pass", default=None, help="VCF Operations password")
@click.option("--vcf-token", default=None, help="VCF Operations API token")
@click.option(
    "--vcf-auth-source",
    default="local",
    show_default=True,
    help="VCF Operations login source for --vcf-user (a directory or SSO source name)",
)
@click.option("--mock-vcf", is_flag=True, help="Use simulated VCF Operations adapter for offline testing")
@click.option("--collector", required=True, help="Cloud Proxy or Collector IP/FQDN")
@click.option("--collector-group", default=None, help="Collector group name for mTLS client certificate bundle")
@click.option("--verify-ssl/--no-verify-ssl", default=True, help="Verify TLS certificates")
@click.option("--target-host", required=True, help="Target hostname or IP address")
@click.option(
    "--connection",
    type=click.Choice(["ssh", "winrm", "mock", "local"]),
    default="ssh",
    help="Endpoint connection method",
)
@click.option("--port", type=int, default=None, help="Remote connection port")
@click.option("--ssh-user", default=None, help="SSH/WinRM username")
@click.option("--ssh-pass", default=None, help="SSH/WinRM password")
@click.option("--ssh-key", default=None, help="SSH private key path")
@click.option("--winrm-ssl", is_flag=True, default=False, help="Use HTTPS/SSL for WinRM transport")
@click.option("--install-telegraf", is_flag=True, default=False, help="Automatically install Telegraf agent if missing")
@click.option("--telegraf-version", default="1.40.1", help="Telegraf agent release version to install (default: 1.40.1)")
@click.option("--os", "target_os", type=click.Choice(["linux", "windows"], case_sensitive=False), default=None, help="Target operating system (defaults to windows for WinRM, linux otherwise)")
@click.option("--hostname", default=None, help="Explicit hostname for VCF Operations registration (overrides discovery)")
@click.option("--no-baseline", is_flag=True, default=False, help="Start from an empty canvas without default OS baseline metrics")
@click.option("--cpu/--no-cpu", default=None, help="Enable or disable CPU monitoring")
@click.option("--mem/--no-mem", default=None, help="Enable or disable memory monitoring")
@click.option("--disk/--no-disk", default=None, help="Enable or disable disk monitoring")
@click.option("--net/--no-net", default=None, help="Enable or disable network monitoring")
@click.option("--system/--no-system", default=None, help="Enable or disable system load and uptime monitoring")
@click.option("--swap/--no-swap", default=None, help="Enable or disable swap monitoring")
@click.option("--diskio/--no-diskio", default=None, help="Enable or disable disk I/O monitoring")
@click.option("--processes/--no-processes", default=None, help="Enable or disable process count monitoring")
@click.option("--win-perf/--no-win-perf", default=None, help="Enable or disable Windows performance counters")
@click.option("--win-services", default=None, help="Comma-separated Windows services to monitor")
@click.option("--no-win-services", is_flag=True, default=False, help="Disable Windows services monitoring")
@click.option("--nginx", default=None, help="NGINX status URL (e.g. http://localhost/status)")
@click.option("--apache", default=None, help="Apache status URL (e.g. http://localhost/server-status?auto)")
@click.option("--mysql", default=None, help="MySQL connection string (e.g. tcp(127.0.0.1:3306)/)")
@click.option("--postgres", default=None, help="PostgreSQL connection string")
@click.option("--mssql", default=None, help="MSSQL connection string")
@click.option("--docker", default=None, help="Docker daemon endpoint (e.g. unix:///var/run/docker.sock)")
@click.option("--ping", default=None, help="Ping target IP or hostname")
@click.option("--preview", is_flag=True, help="Show preview before execution")
@click.option("--dry-run", is_flag=True, help="Simulate execution without modifying target")
@click.option("--export-md", default=None, help="Export summary to Markdown file")
@click.option("--export-json", default=None, help="Export summary to JSON file")
@click.option(
    "--ca-cert",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to custom enterprise CA certificate bundle for VCF Operations TLS validation",
)
@click.option("--vm-name", default=None, help="Target virtual machine name in vCenter / VCF Operations")
@click.option("--vm-id", default=None, help="Target vCenter virtual machine MOR (e.g. vm-1042)")
@click.option("--vc-id", default=None, help="vCenter instance UUID for --vm-id (resolved from inventory if omitted)")
@click.option("--force-new-cert", is_flag=True, default=False, help="Force minting a new client certificate even if existing cert is valid")
def run_cmd(
    vcf_url: str,
    vcf_user: str,
    vcf_pass: Optional[str],
    vcf_token: Optional[str],
    vcf_auth_source: str,
    mock_vcf: bool,
    collector: str,
    collector_group: Optional[str],
    verify_ssl: bool,
    target_host: str,
    connection: str,
    port: Optional[int],
    ssh_user: Optional[str],
    ssh_pass: Optional[str],
    ssh_key: Optional[str],
    winrm_ssl: bool,
    install_telegraf: bool,
    telegraf_version: str,
    target_os: Optional[str],
    hostname: Optional[str],
    no_baseline: bool,
    cpu: Optional[bool],
    mem: Optional[bool],
    disk: Optional[bool],
    net: Optional[bool],
    system: Optional[bool],
    swap: Optional[bool],
    diskio: Optional[bool],
    processes: Optional[bool],
    win_perf: Optional[bool],
    win_services: Optional[str],
    no_win_services: bool,
    nginx: Optional[str],
    apache: Optional[str],
    mysql: Optional[str],
    postgres: Optional[str],
    mssql: Optional[str],
    docker: Optional[str],
    ping: Optional[str],
    preview: bool,
    dry_run: bool,
    export_md: Optional[str],
    export_json: Optional[str],
    ca_cert: Optional[Path] = None,
    vm_name: Optional[str] = None,
    vm_id: Optional[str] = None,
    vc_id: Optional[str] = None,
    force_new_cert: bool = False,
) -> None:
    """Execute the guided workflow via command-line options."""
    display_banner(console)

    if vc_id and not vm_id:
        raise click.UsageError("--vc-id requires --vm-id")

    conn_method = ConnectionMethod(connection)
    if target_os:
        is_win = target_os.lower() == "windows"
    else:
        is_win = conn_method == ConnectionMethod.WINRM

    vcf_pass = vcf_pass or os.environ.get("VCF_PASS")
    vcf_token = vcf_token or os.environ.get("VCF_TOKEN")
    if is_win or conn_method == ConnectionMethod.WINRM:
        ssh_pass = ssh_pass or os.environ.get("WINRM_PASS") or os.environ.get("SSH_PASS")
    else:
        ssh_pass = ssh_pass or os.environ.get("SSH_PASS") or os.environ.get("WINRM_PASS")

    if not mock_vcf and not vcf_token and not vcf_pass:
        if sys.stdin.isatty():
            vcf_pass = click.prompt(f"Password for VCF user {vcf_user}", hide_input=True)

    vcf_env = VCFEnvironment(
        name="cli",
        url=vcf_url,
        username=vcf_user,
        password=vcf_pass,
        token=vcf_token,
        auth_source=vcf_auth_source,
        collector=CollectorInfo(address=collector, name=collector_group),
        verify_ssl=verify_ssl,
        ca_cert_path=str(ca_cert) if ca_cert else None,
    )

    if conn_method in (ConnectionMethod.SSH, ConnectionMethod.WINRM) and not ssh_pass and not ssh_key:
        if sys.stdin.isatty():
            target_user = ssh_user or ("Administrator" if is_win else "root")
            ssh_pass = click.prompt(f"Password for {target_user}@{target_host}", hide_input=True)

    actual_port = port or (5985 if is_win else 22)
    default_user = "Administrator" if is_win else "root"
    target = EndpointTarget(
        hostname=target_host,
        os_family=OSFamily.WINDOWS if is_win else OSFamily.LINUX,
        connection_method=conn_method,
        port=actual_port,
        username=ssh_user or default_user,
        password=ssh_pass,
        key_filename=ssh_key,
        winrm_use_ssl=winrm_ssl,
        install_telegraf=install_telegraf,
        telegraf_version=telegraf_version,
        registered_hostname=hostname or vm_name,
        vm_mor=vm_id,
        vc_id=vc_id,
    )

    monitoring = resolve_monitoring_config(
        is_win=is_win,
        no_baseline=no_baseline,
        cpu=cpu,
        mem=mem,
        disk=disk,
        net=net,
        system=system,
        swap=swap,
        diskio=diskio,
        processes=processes,
        win_perf=win_perf,
        win_services=win_services,
        no_win_services=no_win_services,
        nginx=nginx,
        apache=apache,
        mysql=mysql,
        postgres=postgres,
        mssql=mssql,
        docker=docker,
        ping=ping,
    )

    if conn_method == ConnectionMethod.MOCK:
        executor = MockExecutor(connected=True, telegraf_installed=True)
    elif conn_method == ConnectionMethod.LOCAL:
        executor = LocalExecutor()
    elif conn_method == ConnectionMethod.WINRM:
        executor = WinRMExecutor(
            hostname=target_host,
            port=target.port,
            username=target.username or "Administrator",
            password=target.password or "",
            use_ssl=winrm_ssl,
        )
    else:
        executor = SSHExecutor(
            hostname=target_host,
            port=target.port,
            username=target.username,
            password=target.password,
            key_filename=ssh_key,
        )

    if mock_vcf:
        adapter = MockVCFOpsIntegration(vcf_env)
    else:
        adapter = get_adapter(vcf_env)

    if preview or dry_run:
        system_toml = TelegrafRenderer.render_system_inputs(monitoring)
        vcf_toml = TelegrafRenderer.render_vcf_output(
            collector_address=collector,
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
                "/usr/bin/telegraf --test",
                "systemctl restart telegraf",
            ]
        display_preview(
            console,
            target_host,
            collector,
            system_toml,
            vcf_toml,
            planned_cmds,
        )

    reporter = RichTerminalProgressReporter(console)
    wf_options = WorkflowOptions(
        dry_run=dry_run,
        restart_service=True,
        install_telegraf=install_telegraf,
        telegraf_version=telegraf_version,
        force_new_cert=force_new_cert,
    )

    workflow = ConfigureEndpointWorkflow(
        environment=vcf_env,
        target=target,
        monitoring=monitoring,
        executor=executor,
        adapter=adapter,
        options=wf_options,
        reporter=reporter,
    )

    summary = workflow.run()
    display_summary(console, summary)

    if export_md:
        Path(export_md).write_text(summary.to_markdown(), encoding="utf-8")
        console.print(f"[bold green]✓[/bold green] Markdown report written to [cyan]{export_md}[/cyan]")

    if export_json:
        Path(export_json).write_text(summary.to_json(), encoding="utf-8")
        console.print(f"[bold green]✓[/bold green] JSON report written to [cyan]{export_json}[/cyan]")

    if not summary.success:
        sys.exit(1)


@cli.command("vms")
@click.option("--vcf-url", envvar="VCF_URL", required=True, help="VCF Operations URL, e.g. https://vcf-ops.local")
@click.option("--vcf-user", envvar="VCF_USER", default="admin", help="VCF Operations API username")
@click.option("--vcf-pass", envvar="VCF_PASS", default=None, help="VCF Operations API password")
@click.option("--vcf-token", envvar="VCF_TOKEN", default=None, help="VCF Operations API token")
@click.option(
    "--vcf-auth-source",
    envvar="VCF_AUTH_SOURCE",
    default="local",
    show_default=True,
    help="VCF Operations login source for --vcf-user (a directory or SSO source name)",
)
@click.option("--mock-vcf", is_flag=True, help="Use simulated VCF Operations adapter for offline testing")
@click.option(
    "--ca-cert",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Custom enterprise CA certificate bundle path",
)
@click.option("--verify-ssl/--no-verify-ssl", default=True, help="Verify TLS certificates")
@click.option(
    "--os",
    "os_filter",
    type=click.Choice(["all", "windows", "linux"], case_sensitive=False),
    default="all",
    help="Filter by guest OS family",
)
@click.option("--cg", "cg_filter", default=None, help="Filter by the collector group of the existing agent")
@click.option(
    "--status",
    "status_filter",
    type=click.Choice(["all", "not-installed", "reporting", "no-data"], case_sensitive=False),
    default="all",
    help="Filter by agent status reported by VCF Operations",
)
@click.option("--include-powered-off", is_flag=True, default=False, help="Include powered-off VMs (hidden by default)")
@click.option("--filter", "query_filter", default=None, help="Search filter for VM name, IP, hostname, or MOR")
def vms_cmd(
    vcf_url: str,
    vcf_user: str,
    vcf_pass: Optional[str],
    vcf_token: Optional[str],
    vcf_auth_source: str,
    mock_vcf: bool,
    ca_cert: Optional[Path],
    verify_ssl: bool,
    os_filter: str,
    cg_filter: Optional[str],
    status_filter: str,
    include_powered_off: bool,
    query_filter: Optional[str],
) -> None:
    """Query and display virtual machine inventory from VCF Operations."""
    from rich.table import Table

    display_banner(console)
    vcf_pass = vcf_pass or os.environ.get("VCF_PASS")
    vcf_token = vcf_token or os.environ.get("VCF_TOKEN")
    if not mock_vcf and not vcf_token and not vcf_pass:
        if sys.stdin.isatty():
            vcf_pass = click.prompt(f"Password for VCF user {vcf_user}", hide_input=True)

    vcf_env = VCFEnvironment(
        name="cli-vms",
        url=vcf_url,
        username=vcf_user,
        password=vcf_pass,
        token=vcf_token,
        auth_source=vcf_auth_source,
        collector=CollectorInfo(address="127.0.0.1"),
        verify_ssl=verify_ssl,
        ca_cert_path=str(ca_cert) if ca_cert else None,
    )

    if mock_vcf:
        adapter = MockVCFOpsIntegration(vcf_env)
    else:
        adapter = get_adapter(vcf_env)

    try:
        console.print(f"[bold cyan]-->[/bold cyan] Querying VCF Operations inventory from {vcf_url}...")
        vms = adapter.list_virtual_machines(strict=True)
    except Exception as exc:
        console.print(f"[bold red]Failed to retrieve inventory:[/bold red] {exc}")
        sys.exit(1)
    if adapter.inventory_warning:
        console.print(f"[bold yellow]Warning:[/bold yellow] {adapter.inventory_warning}")

    status_values = {"not-installed": "Not installed", "reporting": "Reporting", "no-data": "No data"}
    filtered = []
    q = (query_filter or "").strip().lower()
    for vm in vms:
        if not include_powered_off and not vm.is_powered_on:
            continue
        haystack = (vm.name, vm.ip_address or "", vm.hostname or "", vm.vm_mor or "")
        if q and not any(q in field.lower() for field in haystack):
            continue
        if os_filter.lower() == "windows" and vm.os_family.lower() != "windows":
            continue
        if os_filter.lower() == "linux" and vm.os_family.lower() != "linux":
            continue
        if cg_filter and (vm.collector_group or "").lower() != cg_filter.strip().lower():
            continue
        if status_filter.lower() != "all" and vm.telegraf_status != status_values[status_filter.lower()]:
            continue
        filtered.append(vm)

    table = Table(
        title=f"VCF Operations Virtual Machine Inventory ({len(filtered)} / {len(vms)} VMs)",
        show_header=True,
        header_style="bold magenta",
    )
    # Identity columns never wrap so names, IPs, and MORs stay copyable on narrow terminals
    table.add_column("VM Name", style="cyan", no_wrap=True)
    table.add_column("IP Address", style="white", no_wrap=True)
    table.add_column("OS Family", justify="center")
    table.add_column("Power", justify="center")
    table.add_column("VM MOR", justify="center", no_wrap=True)
    table.add_column("Agent Status", justify="center", overflow="fold")
    table.add_column("Agent Collector Group", overflow="fold")

    for vm in filtered:
        st_color = {"Reporting": "green", "No data": "yellow"}.get(vm.telegraf_status, "dim")
        status = vm.telegraf_status
        if vm.agent_registrations > 1:
            status = f"{status} ({vm.agent_registrations} registrations)"
        table.add_row(
            vm.name,
            vm.ip_address or "N/A",
            vm.os_family.capitalize(),
            vm.power_state or "Unknown",
            vm.vm_mor or "N/A",
            f"[{st_color}]{status}[/{st_color}]",
            vm.collector_group or "-",
        )

    console.print(table)


@cli.command("render")
@click.option("--no-baseline", is_flag=True, default=False, help="Start from an empty canvas without default OS baseline metrics")
@click.option("--pretty", is_flag=True, default=False, help="Render with syntax highlighting (default: raw unpadded TOML for file redirection)")
@click.option("--cpu/--no-cpu", default=None, help="Enable or disable CPU monitoring")
@click.option("--mem/--no-mem", default=None, help="Enable or disable memory monitoring")
@click.option("--disk/--no-disk", default=None, help="Enable or disable disk monitoring")
@click.option("--net/--no-net", default=None, help="Enable or disable network monitoring")
@click.option("--system/--no-system", default=None, help="Enable or disable system load and uptime monitoring")
@click.option("--swap/--no-swap", default=None, help="Enable or disable swap monitoring")
@click.option("--diskio/--no-diskio", default=None, help="Enable or disable disk I/O monitoring")
@click.option("--processes/--no-processes", default=None, help="Enable or disable process count monitoring")
@click.option("--win-perf/--no-win-perf", default=None, help="Enable or disable Windows performance counters")
@click.option("--win-services", default=None, help="Comma-separated Windows services to monitor")
@click.option("--no-win-services", is_flag=True, default=False, help="Disable Windows services monitoring")
@click.option("--nginx", default=None, help="NGINX status URL (e.g. http://localhost/status)")
@click.option("--apache", default=None, help="Apache status URL (e.g. http://localhost/server-status?auto)")
@click.option("--mysql", default=None, help="MySQL connection string (e.g. tcp(127.0.0.1:3306)/)")
@click.option("--postgres", default=None, help="PostgreSQL connection string")
@click.option("--mssql", default=None, help="MSSQL connection string")
@click.option("--docker", default=None, help="Docker daemon endpoint (e.g. unix:///var/run/docker.sock)")
@click.option("--ping", default=None, help="Ping target IP or hostname")
@click.option("--os", "target_os", type=click.Choice(["linux", "windows"], case_sensitive=False), default=None, help="Target operating system")
@click.option("--hostname", default=None, help="Explicit hostname for VCF Operations registration")
@click.option("--collector", default="10.10.10.50", help="Collector address for output fragment")
@click.option("--target-host", default="target.local", help="Target hostname")
def render_cmd(
    no_baseline: bool,
    pretty: bool,
    cpu: Optional[bool],
    mem: Optional[bool],
    disk: Optional[bool],
    net: Optional[bool],
    system: Optional[bool],
    swap: Optional[bool],
    diskio: Optional[bool],
    processes: Optional[bool],
    win_perf: Optional[bool],
    win_services: Optional[str],
    no_win_services: bool,
    nginx: Optional[str],
    apache: Optional[str],
    mysql: Optional[str],
    postgres: Optional[str],
    mssql: Optional[str],
    docker: Optional[str],
    ping: Optional[str],
    target_os: Optional[str],
    hostname: Optional[str],
    collector: str,
    target_host: str,
) -> None:
    """Render and preview Telegraf TOML fragments directly to stdout."""
    if target_os:
        is_win = target_os.lower() == "windows"
    else:
        is_win = bool(win_perf or win_services or no_win_services or (win_perf is False))

    cfg = resolve_monitoring_config(
        is_win=is_win,
        no_baseline=no_baseline,
        cpu=cpu,
        mem=mem,
        disk=disk,
        net=net,
        system=system,
        swap=swap,
        diskio=diskio,
        processes=processes,
        win_perf=win_perf,
        win_services=win_services,
        no_win_services=no_win_services,
        nginx=nginx,
        apache=apache,
        mysql=mysql,
        postgres=postgres,
        mssql=mssql,
        docker=docker,
        ping=ping,
    )

    system_toml = TelegrafRenderer.render_system_inputs(cfg)
    vcf_toml = TelegrafRenderer.render_vcf_output(
        collector_address=collector,
        hostname=hostname or target_host,
        is_windows=is_win,
    )

    if pretty:
        console.print("[bold cyan]# vcf-helper-system.conf[/bold cyan]")
        console.print(Syntax(system_toml, "toml"))
        console.print("\n[bold cyan]# cloudproxy-http.conf[/bold cyan]")
        console.print(Syntax(vcf_toml, "toml"))
    else:
        sys.stdout.write(f"# vcf-helper-system.conf\n{system_toml}\n# cloudproxy-http.conf\n{vcf_toml}\n")
        sys.stdout.flush()


@cli.command("validate")
@click.option("--file", "conf_file", type=click.Path(exists=True), help="Path to TOML file to validate")
def validate_cmd(conf_file: Optional[str]) -> None:
    """Validate a Telegraf TOML configuration fragment."""
    if conf_file:
        content = Path(conf_file).read_text(encoding="utf-8")
        res = Validator.validate_toml_syntax(content, label=conf_file)
        if res.is_valid:
            console.print(f"[bold green]✓[/bold green] {res.message}")
        else:
            console.print(f"[bold red]✕[/bold red] {res.message}")
            if res.details:
                console.print(f"[dim]{res.details}[/dim]")
            sys.exit(1)
    else:
        # Default self-check of standard renderer outputs
        cfg = MonitoringConfig()
        toml_str = TelegrafRenderer.render_system_inputs(cfg)
        res = Validator.validate_toml_syntax(toml_str, "Default OS Monitoring Fragment")
        if res.is_valid:
            console.print(f"[bold green]✓[/bold green] {res.message}")
        else:
            console.print(f"[bold red]✕[/bold red] {res.message}")
            sys.exit(1)


@cli.group("env")
def env_group() -> None:
    """Manage saved VCF Operations environments."""
    pass


@env_group.command("list")
def env_list() -> None:
    """List saved VCF Operations environments."""
    store = StateStore()
    envs = store.list_environments()
    if not envs:
        console.print("[dim]No saved environments found.[/dim]")
        return

    for env in envs:
        console.print(f"* [bold cyan]{env.name}[/bold cyan]: {env.url} (Collector: {env.collector.address})")


@env_group.command("add")
@click.argument("name")
@click.argument("url")
@click.argument("collector")
@click.option("--user", default="admin", help="Username")
def env_add(name: str, url: str, collector: str, user: str) -> None:
    """Add a saved VCF Operations environment definition."""
    store = StateStore()
    vcf_env = VCFEnvironment(
        name=name,
        url=url,
        username=user,
        collector=CollectorInfo(address=collector),
    )
    store.save_environment(vcf_env)
    console.print(f"[bold green]✓[/bold green] Saved environment '{name}' ({url})")


@cli.command("uninstall")
@click.option("--target", "-t", default=None, help="Target hostname or IP address")
@click.option("--os", "os_type", type=click.Choice(["linux", "windows"], case_sensitive=False), default="linux", help="Target OS family")
@click.option("--method", type=click.Choice(["ssh", "winrm", "local"], case_sensitive=False), default="ssh", help="Connection method")
@click.option("--user", "-u", default=None, help="Remote username")
@click.option("--key-path", "-k", default=None, help="SSH private key path")
@click.option("--password", "-p", default=None, help="Remote password")
@click.option("--port", type=int, default=None, help="Remote connection port")
@click.option("--purge-repo/--no-purge-repo", default=True, help="Remove InfluxData repository configuration")
@click.option("--yes", "-y", is_flag=True, default=False, help="Confirm uninstall without prompting")
def uninstall_cmd(
    target: Optional[str],
    os_type: str,
    method: str,
    user: Optional[str],
    key_path: Optional[str],
    password: Optional[str],
    port: Optional[int],
    purge_repo: bool,
    yes: bool,
) -> None:
    """Safely stop, disable, and purge Telegraf agent from an endpoint."""
    display_banner(console)
    target_host = target or click.prompt("Target hostname or IP address")
    os_fam = OSFamily.WINDOWS if os_type.lower() == "windows" else OSFamily.LINUX
    conn_method = ConnectionMethod(method.lower())

    if not yes:
        confirm = click.confirm(
            f"Are you sure you want to stop, disable, and completely uninstall Telegraf from {target_host}?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Uninstallation cancelled by user.[/yellow]")
            return

    if os_fam == OSFamily.WINDOWS or conn_method == ConnectionMethod.WINRM:
        password = password or os.environ.get("WINRM_PASS") or os.environ.get("SSH_PASS")
    else:
        password = password or os.environ.get("SSH_PASS") or os.environ.get("WINRM_PASS")
    target_user = user or ("Administrator" if os_fam == OSFamily.WINDOWS else "root")
    if conn_method in (ConnectionMethod.SSH, ConnectionMethod.WINRM) and not password and not key_path:
        if sys.stdin.isatty():
            password = click.prompt(f"Password for {target_user}@{target_host}", hide_input=True)

    actual_port = port or (5985 if os_fam == OSFamily.WINDOWS else 22)
    ep_target = EndpointTarget(
        hostname=target_host,
        os_family=os_fam,
        connection_method=conn_method,
        port=actual_port,
        username=target_user,
        password=password,
        key_filename=key_path,
    )

    if conn_method == ConnectionMethod.SSH:
        executor = SSHExecutor(
            hostname=target_host,
            port=actual_port,
            username=ep_target.username,
            password=ep_target.password,
            key_filename=key_path,
        )
    elif conn_method == ConnectionMethod.WINRM:
        executor = WinRMExecutor(
            hostname=target_host,
            port=actual_port,
            username=ep_target.username or "Administrator",
            password=ep_target.password or "",
        )
    else:
        executor = LocalExecutor()

    from vcf_ops_telegraf_helper.models.workflow import UninstallOptions
    from vcf_ops_telegraf_helper.workflow.uninstall import UninstallEndpointWorkflow

    reporter = RichTerminalProgressReporter(console)
    workflow = UninstallEndpointWorkflow(
        target=ep_target,
        executor=executor,
        reporter=reporter,
        options=UninstallOptions(purge_packages=True, purge_repositories=purge_repo),
    )

    console.print(f"\n[bold]Initiating uninstallation on {target_host}...[/bold]\n")
    summary = workflow.run()

    if summary.success:
        console.print(f"\n[bold green]✓ Telegraf uninstalled successfully from {target_host}[/bold green]")
        for item, status in summary.verifications.items():
            console.print(f"  * {item}: [green]{status}[/green]")
    else:
        console.print(f"\n[bold red]✗ Uninstallation failed or left artifacts on {target_host}[/bold red]")
        for item, status in summary.verifications.items():
            color = "green" if status == "PASS" else "red"
            console.print(f"  * {item}: [{color}]{status}[/{color}]")
        sys.exit(1)


if __name__ == "__main__":
    cli()

