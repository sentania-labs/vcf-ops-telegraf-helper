"""Main CLI entry points for VCF Operations Open Telegraf Helper."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Optional
import click
from rich.console import Console
from rich.syntax import Syntax

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
from vcf_ops_telegraf_helper.executors.package import PackageExecutor
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
from vcf_ops_telegraf_helper.models.workflow import DeploymentMode, WorkflowOptions
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
@click.pass_context
def cli(ctx: click.Context) -> None:
    """VCF Operations Open Telegraf Helper: Local onboarding utility."""
    if ctx.invoked_subcommand is None:
        # If double-clicked in Windows Explorer, launch native GUI by default.
        if _is_windows_double_click() and not os.environ.get("VCF_HELPER_NO_GUI"):
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
    from vcf_ops_telegraf_helper.gui.app import run_gui
    sys.exit(run_gui(theme=theme))


@cli.command("run")
@click.option("--vcf-url", required=True, help="VCF Operations URL, e.g. https://vcf-ops.local")
@click.option("--vcf-user", default="admin", help="VCF Operations username")
@click.option("--vcf-pass", default=None, help="VCF Operations password")
@click.option("--vcf-token", default=None, help="VCF Operations API token")
@click.option("--mock-vcf", is_flag=True, help="Use simulated VCF Operations adapter for offline testing")
@click.option("--collector", required=True, help="Cloud Proxy or Collector IP/FQDN")
@click.option("--collector-group", default=None, help="Collector group name for mTLS client certificate bundle")
@click.option("--verify-ssl/--no-verify-ssl", default=True, help="Verify TLS certificates")
@click.option("--target-host", required=True, help="Target hostname or IP address")
@click.option(
    "--connection",
    type=click.Choice(["ssh", "winrm", "mock", "local", "package"]),
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
@click.option("--nginx", default=None, help="NGINX status URL (e.g. http://localhost/status)")
@click.option("--apache", default=None, help="Apache status URL (e.g. http://localhost/server-status?auto)")
@click.option("--mysql", default=None, help="MySQL connection string (e.g. tcp(127.0.0.1:3306)/)")
@click.option("--postgres", default=None, help="PostgreSQL connection string")
@click.option("--mssql", default=None, help="MSSQL connection string")
@click.option("--docker", default=None, help="Docker daemon endpoint (e.g. unix:///var/run/docker.sock)")
@click.option("--ping", default=None, help="Ping target IP or hostname")
@click.option("--preview", is_flag=True, help="Show preview before execution")
@click.option("--dry-run", is_flag=True, help="Simulate execution without modifying target")
@click.option(
    "--mode",
    type=click.Choice(["push", "script", "config_only"]),
    default="push",
    help="Deployment mode",
)
@click.option("--output-dir", default="./vcf-telegraf-bundle", help="Output directory for script/bundle")
@click.option("--export-md", default=None, help="Export summary to Markdown file")
@click.option("--export-json", default=None, help="Export summary to JSON file")
def run_cmd(
    vcf_url: str,
    vcf_user: str,
    vcf_pass: Optional[str],
    vcf_token: Optional[str],
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
    nginx: Optional[str],
    apache: Optional[str],
    mysql: Optional[str],
    postgres: Optional[str],
    mssql: Optional[str],
    docker: Optional[str],
    ping: Optional[str],
    preview: bool,
    dry_run: bool,
    mode: str,
    output_dir: str,
    export_md: Optional[str],
    export_json: Optional[str],
) -> None:
    """Execute the guided workflow via command-line options."""
    display_banner(console)

    vcf_env = VCFEnvironment(
        name="cli",
        url=vcf_url,
        username=vcf_user,
        password=vcf_pass,
        token=vcf_token,
        collector=CollectorInfo(address=collector, name=collector_group),
        verify_ssl=verify_ssl,
    )

    conn_method = ConnectionMethod(connection)
    if target_os:
        is_win = target_os.lower() == "windows"
    else:
        is_win = conn_method == ConnectionMethod.WINRM
    actual_port = port or (5985 if is_win else 22)
    target = EndpointTarget(
        hostname=target_host,
        os_family=OSFamily.WINDOWS if is_win else OSFamily.LINUX,
        connection_method=conn_method,
        port=actual_port,
        username=ssh_user or ("Administrator" if is_win else None),
        password=ssh_pass,
        key_filename=ssh_key,
        winrm_use_ssl=winrm_ssl,
        install_telegraf=install_telegraf,
        telegraf_version=telegraf_version,
        registered_hostname=hostname,
    )

    # Opt-in plugin resolution:
    # If the user explicitly passed any core plugin flags, enable only the ones opted into.
    # If the user passed no core plugin flags at all, apply the recommended baseline for the target OS.
    # Workload plugins (nginx, mssql, etc.) supplement the baseline rather than disabling it.
    explicit_flags = [cpu, mem, disk, net, system, swap, diskio, processes, win_perf]
    has_explicit_core_plugin = any(f is not None for f in explicit_flags) or bool(win_services)

    if not has_explicit_core_plugin:
        # Auto-apply OS baseline
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
    else:
        all_specified = [f for f in explicit_flags if f is not None]
        only_negatives = len(all_specified) > 0 and all(f is False for f in all_specified) and not bool(win_services)
        if only_negatives:
            # Baseline minus negated plugins
            effective_cpu = (cpu if cpu is not None else True) if not is_win else False
            effective_mem = (mem if mem is not None else True) if not is_win else False
            effective_disk = (disk if disk is not None else True) if not is_win else False
            effective_net = (net if net is not None else True) if not is_win else False
            effective_system = (system if system is not None else True) if not is_win else False
            effective_swap = (swap if swap is not None else True) if not is_win else False
            effective_diskio = False
            effective_proc = False
            effective_win_perf = (win_perf if win_perf is not None else True) if is_win else False
            effective_win_svc = is_win
        else:
            # Pure opt-in
            effective_cpu = bool(cpu)
            effective_mem = bool(mem)
            effective_disk = bool(disk)
            effective_net = bool(net)
            effective_system = bool(system)
            effective_swap = bool(swap)
            effective_diskio = bool(diskio)
            effective_proc = bool(processes)
            effective_win_perf = bool(win_perf)
            effective_win_svc = bool(win_services)

    svc_list = [s.strip() for s in win_services.split(",") if s.strip()] if win_services else ["*"]
    monitoring = MonitoringConfig(
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
        docker=DockerInputConfig(enabled=bool(docker), endpoint=docker or "unix:///var/run/docker.sock"),
        ping=PingInputConfig(enabled=bool(ping), urls=[ping] if ping else ["10.10.10.1"]),
    )

    if conn_method == ConnectionMethod.MOCK:
        executor = MockExecutor(connected=True, telegraf_installed=True)
    elif conn_method == ConnectionMethod.LOCAL:
        executor = LocalExecutor()
    elif conn_method == ConnectionMethod.PACKAGE:
        executor = PackageExecutor(output_dir=output_dir)
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
            port=target.port,
            username=ssh_user,
            password=ssh_pass,
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
        )
        display_preview(
            console,
            target_host,
            collector,
            system_toml,
            vcf_toml,
            [
                "mkdir -p /etc/telegraf/telegraf.d",
                "upload vcf-helper-system.conf -> /etc/telegraf/telegraf.d/vcf-helper-system.conf",
                "upload cloudproxy-http.conf -> /etc/telegraf/telegraf.d/cloudproxy-http.conf",
                "/usr/bin/telegraf --test",
                "systemctl restart telegraf",
            ],
        )

    reporter = RichTerminalProgressReporter(console)
    wf_options = WorkflowOptions(
        mode=DeploymentMode(mode),
        dry_run=dry_run,
        output_dir=output_dir,
        restart_service=True,
        install_telegraf=install_telegraf,
        telegraf_version=telegraf_version,
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


@cli.command("render")
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
        is_win = bool(win_perf or win_services)

    explicit_flags = [cpu, mem, disk, net, system, swap, diskio, processes, win_perf]
    has_explicit_core_plugin = any(f is not None for f in explicit_flags) or bool(win_services)

    if not has_explicit_core_plugin:
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
    else:
        all_specified = [f for f in explicit_flags if f is not None]
        only_negatives = len(all_specified) > 0 and all(f is False for f in all_specified) and not bool(win_services)
        if only_negatives:
            # Baseline minus negated plugins
            effective_cpu = (cpu if cpu is not None else True) if not is_win else False
            effective_mem = (mem if mem is not None else True) if not is_win else False
            effective_disk = (disk if disk is not None else True) if not is_win else False
            effective_net = (net if net is not None else True) if not is_win else False
            effective_system = (system if system is not None else True) if not is_win else False
            effective_swap = (swap if swap is not None else True) if not is_win else False
            effective_diskio = False
            effective_proc = False
            effective_win_perf = (win_perf if win_perf is not None else True) if is_win else False
            effective_win_svc = is_win
        else:
            effective_cpu = bool(cpu)
            effective_mem = bool(mem)
            effective_disk = bool(disk)
            effective_net = bool(net)
            effective_system = bool(system)
            effective_swap = bool(swap)
            effective_diskio = bool(diskio)
            effective_proc = bool(processes)
            effective_win_perf = bool(win_perf)
            effective_win_svc = bool(win_services)

    svc_list = [s.strip() for s in win_services.split(",") if s.strip()] if win_services else ["*"]
    cfg = MonitoringConfig(
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
        docker=DockerInputConfig(enabled=bool(docker), endpoint=docker or "unix:///var/run/docker.sock"),
        ping=PingInputConfig(enabled=bool(ping), urls=[ping] if ping else ["10.10.10.1"]),
    )

    system_toml = TelegrafRenderer.render_system_inputs(cfg)
    vcf_toml = TelegrafRenderer.render_vcf_output(collector_address=collector, hostname=hostname or target_host)

    console.print("[bold cyan]# vcf-helper-system.conf[/bold cyan]")
    console.print(Syntax(system_toml, "toml"))
    console.print("\n[bold cyan]# cloudproxy-http.conf[/bold cyan]")
    console.print(Syntax(vcf_toml, "toml"))


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

    actual_port = port or (5985 if os_fam == OSFamily.WINDOWS else 22)
    ep_target = EndpointTarget(
        hostname=target_host,
        os_family=os_fam,
        connection_method=conn_method,
        port=actual_port,
        username=user or ("Administrator" if os_fam == OSFamily.WINDOWS else "root"),
        password=password,
        key_filename=key_path,
    )

    if conn_method == ConnectionMethod.SSH:
        executor = SSHExecutor(
            hostname=target_host,
            port=actual_port,
            username=user,
            password=password,
            key_filename=key_path,
        )
    elif conn_method == ConnectionMethod.WINRM:
        executor = WinRMExecutor(
            hostname=target_host,
            port=actual_port,
            username=user or "Administrator",
            password=password or "",
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

