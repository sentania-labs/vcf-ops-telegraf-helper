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
    DockerInputConfig,
    MemInputConfig,
    MonitoringConfig,
    MssqlInputConfig,
    MysqlInputConfig,
    NetInputConfig,
    NginxInputConfig,
    PingInputConfig,
    PostgresqlInputConfig,
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
@click.option("--mock-vcf", is_flag=True, help="Use simulated VCF Operations adapter for offline testing")
@click.option("--collector", required=True, help="Cloud Proxy or Collector IP/FQDN")
@click.option("--verify-ssl/--no-verify-ssl", default=True, help="Verify TLS certificates")
@click.option("--target-host", required=True, help="Target hostname or IP address")
@click.option(
    "--connection",
    type=click.Choice(["ssh", "winrm", "mock", "local", "package"]),
    default="ssh",
    help="Endpoint connection method",
)
@click.option("--ssh-user", default=None, help="SSH/WinRM username")
@click.option("--ssh-pass", default=None, help="SSH/WinRM password")
@click.option("--ssh-key", default=None, help="SSH private key path")
@click.option("--winrm-ssl", is_flag=True, default=False, help="Use HTTPS/SSL for WinRM transport")
@click.option("--install-telegraf", is_flag=True, default=False, help="Automatically install Telegraf agent if missing")
@click.option("--cpu/--no-cpu", default=True, help="Enable CPU monitoring")
@click.option("--mem/--no-mem", default=True, help="Enable memory monitoring")
@click.option("--disk/--no-disk", default=True, help="Enable disk monitoring")
@click.option("--net/--no-net", default=True, help="Enable network monitoring")
@click.option("--win-perf", is_flag=True, default=False, help="Enable Windows performance counters")
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
    mock_vcf: bool,
    collector: str,
    verify_ssl: bool,
    target_host: str,
    connection: str,
    ssh_user: Optional[str],
    ssh_pass: Optional[str],
    ssh_key: Optional[str],
    winrm_ssl: bool,
    install_telegraf: bool,
    cpu: bool,
    mem: bool,
    disk: bool,
    net: bool,
    win_perf: bool,
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
        collector=CollectorInfo(address=collector),
        verify_ssl=verify_ssl,
    )

    conn_method = ConnectionMethod(connection)
    is_win = conn_method == ConnectionMethod.WINRM
    target = EndpointTarget(
        hostname=target_host,
        os_family=OSFamily.WINDOWS if is_win else OSFamily.LINUX,
        connection_method=conn_method,
        username=ssh_user or ("Administrator" if is_win else None),
        password=ssh_pass,
        key_filename=ssh_key,
        winrm_use_ssl=winrm_ssl,
        install_telegraf=install_telegraf,
    )

    svc_list = [s.strip() for s in win_services.split(",") if s.strip()] if win_services else ["*"]
    monitoring = MonitoringConfig(
        cpu=CpuInputConfig(enabled=cpu),
        mem=MemInputConfig(enabled=mem),
        disk=DiskInputConfig(enabled=disk),
        net=NetInputConfig(enabled=net),
        win_perf_counters=WinPerfCountersInputConfig(enabled=win_perf),
        win_services=WinServicesInputConfig(enabled=bool(win_services), service_names=svc_list),
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
            hostname=target_host,
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
@click.option("--cpu/--no-cpu", default=True, help="Enable CPU monitoring")
@click.option("--mem/--no-mem", default=True, help="Enable memory monitoring")
@click.option("--disk/--no-disk", default=True, help="Enable disk monitoring")
@click.option("--net/--no-net", default=True, help="Enable network monitoring")
@click.option("--win-perf", is_flag=True, default=False, help="Enable Windows performance counters")
@click.option("--win-services", default=None, help="Comma-separated Windows services to monitor")
@click.option("--nginx", default=None, help="NGINX status URL (e.g. http://localhost/status)")
@click.option("--apache", default=None, help="Apache status URL (e.g. http://localhost/server-status?auto)")
@click.option("--mysql", default=None, help="MySQL connection string (e.g. tcp(127.0.0.1:3306)/)")
@click.option("--postgres", default=None, help="PostgreSQL connection string")
@click.option("--mssql", default=None, help="MSSQL connection string")
@click.option("--docker", default=None, help="Docker daemon endpoint (e.g. unix:///var/run/docker.sock)")
@click.option("--ping", default=None, help="Ping target IP or hostname")
@click.option("--collector", default="10.10.10.50", help="Collector address for output fragment")
@click.option("--target-host", default="target.local", help="Target hostname")
def render_cmd(
    cpu: bool,
    mem: bool,
    disk: bool,
    net: bool,
    win_perf: bool,
    win_services: Optional[str],
    nginx: Optional[str],
    apache: Optional[str],
    mysql: Optional[str],
    postgres: Optional[str],
    mssql: Optional[str],
    docker: Optional[str],
    ping: Optional[str],
    collector: str,
    target_host: str,
) -> None:
    """Render and preview Telegraf TOML fragments directly to stdout."""
    svc_list = [s.strip() for s in win_services.split(",") if s.strip()] if win_services else ["*"]
    cfg = MonitoringConfig(
        cpu=CpuInputConfig(enabled=cpu),
        mem=MemInputConfig(enabled=mem),
        disk=DiskInputConfig(enabled=disk),
        net=NetInputConfig(enabled=net),
        win_perf_counters=WinPerfCountersInputConfig(enabled=win_perf),
        win_services=WinServicesInputConfig(enabled=bool(win_services), service_names=svc_list),
        nginx=NginxInputConfig(enabled=bool(nginx), urls=[nginx] if nginx else ["http://localhost/status"]),
        apache=ApacheInputConfig(enabled=bool(apache), urls=[apache] if apache else ["http://localhost/server-status?auto"]),
        mysql=MysqlInputConfig(enabled=bool(mysql), servers=[mysql] if mysql else ["tcp(127.0.0.1:3306)/"]),
        postgresql=PostgresqlInputConfig(enabled=bool(postgres), address=postgres or "host=localhost user=postgres sslmode=disable"),
        mssql=MssqlInputConfig(enabled=bool(mssql), servers=[mssql] if mssql else ["Server=127.0.0.1;Port=1433;User Id=sa;Password=;app name=telegraf;log=1;"]),
        docker=DockerInputConfig(enabled=bool(docker), endpoint=docker or "unix:///var/run/docker.sock"),
        ping=PingInputConfig(enabled=bool(ping), urls=[ping] if ping else ["10.10.10.1"]),
    )

    system_toml = TelegrafRenderer.render_system_inputs(cfg)
    vcf_toml = TelegrafRenderer.render_vcf_output(collector_address=collector, hostname=target_host)

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


if __name__ == "__main__":
    cli()

