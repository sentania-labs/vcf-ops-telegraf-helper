"""Main CLI entry points for VCF Operations Open Telegraf Helper."""

from __future__ import annotations

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
)
from vcf_ops_telegraf_helper.models.vcf import CollectorInfo, VCFEnvironment
from vcf_ops_telegraf_helper.models.workflow import DeploymentMode, WorkflowOptions
from vcf_ops_telegraf_helper.renderer.renderer import TelegrafRenderer
from vcf_ops_telegraf_helper.storage.state import StateStore
from vcf_ops_telegraf_helper.validation.validator import Validator
from vcf_ops_telegraf_helper.workflow.engine import ConfigureEndpointWorkflow


console = Console()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """VCF Operations Open Telegraf Helper: Local onboarding utility."""
    if ctx.invoked_subcommand is None:
        display_banner(console)
        click.echo(ctx.get_help())


@cli.command("wizard")
def wizard_cmd() -> None:
    """Launch the interactive terminal onboarding wizard."""
    run_wizard(console)


@cli.command("gui")
@click.option("--host", default="127.0.0.1", help="Host interface to bind")
@click.option("--port", default=8765, type=int, help="Port to listen on")
@click.option("--no-browser", is_flag=True, help="Do not automatically open web browser")
def gui_cmd(host: str, port: int, no_browser: bool) -> None:
    """Launch the local browser GUI styled with Lattice."""
    from vcf_ops_telegraf_helper.gui.server import start_gui
    display_banner(console)
    start_gui(host=host, port=port, open_browser=not no_browser)


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
    type=click.Choice(["ssh", "mock", "local", "package"]),
    default="ssh",
    help="Endpoint connection method",
)
@click.option("--ssh-user", default=None, help="SSH username")
@click.option("--ssh-pass", default=None, help="SSH password")
@click.option("--ssh-key", default=None, help="SSH private key path")
@click.option("--cpu/--no-cpu", default=True, help="Enable CPU monitoring")
@click.option("--mem/--no-mem", default=True, help="Enable memory monitoring")
@click.option("--disk/--no-disk", default=True, help="Enable disk monitoring")
@click.option("--net/--no-net", default=True, help="Enable network monitoring")
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
    cpu: bool,
    mem: bool,
    disk: bool,
    net: bool,
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
    target = EndpointTarget(
        hostname=target_host,
        os_family=OSFamily.LINUX,
        connection_method=conn_method,
        username=ssh_user,
        password=ssh_pass,
        key_filename=ssh_key,
    )

    monitoring = MonitoringConfig(
        cpu=CpuInputConfig(enabled=cpu),
        mem=MemInputConfig(enabled=mem),
        disk=DiskInputConfig(enabled=disk),
        net=NetInputConfig(enabled=net),
    )

    if conn_method == ConnectionMethod.MOCK:
        executor = MockExecutor(connected=True, telegraf_installed=True)
    elif conn_method == ConnectionMethod.LOCAL:
        executor = LocalExecutor()
    elif conn_method == ConnectionMethod.PACKAGE:
        executor = PackageExecutor(output_dir=output_dir)
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
@click.option("--collector", default="10.10.10.50", help="Collector address for output fragment")
@click.option("--target-host", default="target.local", help="Target hostname")
def render_cmd(
    cpu: bool,
    mem: bool,
    disk: bool,
    net: bool,
    collector: str,
    target_host: str,
) -> None:
    """Render and preview Telegraf TOML fragments directly to stdout."""
    cfg = MonitoringConfig(
        cpu=CpuInputConfig(enabled=cpu),
        mem=MemInputConfig(enabled=mem),
        disk=DiskInputConfig(enabled=disk),
        net=NetInputConfig(enabled=net),
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
