"""Rich terminal display helpers for VCF Operations Open Telegraf Helper."""

from __future__ import annotations

from typing import List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from vcf_ops_telegraf_helper.models.workflow import RunSummary, StageResult, StageStatus, WorkflowStage
from vcf_ops_telegraf_helper.workflow.progress import ProgressReporter


class RichTerminalProgressReporter(ProgressReporter):
    """Streams live workflow stages to a Rich terminal console."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()

    def on_stage_start(self, stage: WorkflowStage) -> None:
        self.console.print(f"[bold cyan]-->[/bold cyan] [white]{stage.value}...[/white]")

    def on_stage_complete(self, result: StageResult) -> None:
        if result.status == StageStatus.PASS:
            badge = "[bold green]PASS[/bold green]"
        elif result.status == StageStatus.FAIL:
            badge = "[bold red]FAIL[/bold red]"
        elif result.status == StageStatus.WARNING:
            badge = "[bold yellow]WARN[/bold yellow]"
        else:
            badge = "[dim]SKIP[/dim]"

        dur_text = f"({result.duration_ms} ms)" if result.duration_ms > 0 else ""
        self.console.print(
            f"    {badge} {result.message} [dim]{dur_text}[/dim]"
        )
        if result.details and result.status in (StageStatus.FAIL, StageStatus.WARNING):
            self.console.print(Panel(result.details, title="Diagnostics", border_style="red" if result.status == StageStatus.FAIL else "yellow"))

    def on_message(self, message: str) -> None:
        self.console.print(f"[dim]{message}[/dim]")


def display_banner(console: Console) -> None:
    """Print the application banner."""
    title = "[bold blue]VCF Operations Open Telegraf Helper[/bold blue]"
    sub = "[dim]Local administrator utility for supported open-source Telegraf onboarding[/dim]"
    console.print(Panel(f"{title}\n{sub}", border_style="blue", expand=False))


def display_preview(
    console: Console,
    target_host: str,
    collector_addr: str,
    system_toml: str,
    vcf_toml: str,
    commands: List[str],
) -> None:
    """Display pre-flight review: target details, generated TOML, and planned commands."""
    console.print("\n[bold]Configuration Review[/bold]")
    table = Table(show_header=False, box=None)
    table.add_row("[bold cyan]Target Host:[/bold cyan]", target_host)
    table.add_row("[bold cyan]Collector Destination:[/bold cyan]", collector_addr)
    table.add_row("[bold cyan]Managed Fragment 1:[/bold cyan]", "/etc/telegraf/telegraf.d/vcf-helper-system.conf")
    table.add_row("[bold cyan]Managed Fragment 2:[/bold cyan]", "/etc/telegraf/telegraf.d/cloudproxy-http.conf")
    console.print(table)

    console.print("\n[bold cyan]Generated System Input Fragment (vcf-helper-system.conf):[/bold cyan]")
    console.print(Syntax(system_toml, "toml", theme="monokai", line_numbers=True))

    console.print("\n[bold cyan]Generated VCF Cloud Proxy Output (cloudproxy-http.conf):[/bold cyan]")
    console.print(Syntax(vcf_toml, "toml", theme="monokai", line_numbers=True))

    if commands:
        console.print("\n[bold cyan]Planned Commands:[/bold cyan]")
        for cmd in commands:
            console.print(f"  [green]$[/green] {cmd}")


def display_summary(console: Console, summary: RunSummary) -> None:
    """Print the final run summary and verifications table."""
    status_style = "bold green" if summary.success else "bold red"
    status_label = "COMPLETED SUCCESSFULLY" if summary.success else "FAILED"

    console.print(f"\n[{status_style}]Workflow Run Summary: {status_label}[/{status_style}]")
    console.print(f"Target: [cyan]{summary.target_hostname}[/cyan] | Local Time: [dim]{summary.timestamp}[/dim]\n")

    # Stages table
    stage_table = Table(title="Stage Execution Details", show_header=True, header_style="bold magenta")
    stage_table.add_column("Stage", style="cyan")
    stage_table.add_column("Status", justify="center")
    stage_table.add_column("Message")
    stage_table.add_column("Duration", justify="right")

    for s in summary.stages:
        if s.status == StageStatus.PASS:
            status_text = "[bold green]PASS[/bold green]"
        elif s.status == StageStatus.FAIL:
            status_text = "[bold red]FAIL[/bold red]"
        elif s.status == StageStatus.WARNING:
            status_text = "[bold yellow]WARN[/bold yellow]"
        else:
            status_text = "[dim]SKIP[/dim]"
        stage_table.add_row(s.stage.value, status_text, s.message, f"{s.duration_ms} ms")

    console.print(stage_table)

    # Verification checklist table
    if summary.verifications:
        ver_table = Table(title="Operational Verification Checklist", show_header=True, header_style="bold green")
        ver_table.add_column("Verification Item", style="white")
        ver_table.add_column("Result", justify="center")

        for item, res in summary.verifications.items():
            if res == "PASS":
                res_fmt = "[bold green]PASS[/bold green]"
            elif res == "FAIL":
                res_fmt = "[bold red]FAIL[/bold red]"
            elif res == "UNKNOWN":
                res_fmt = "[bold yellow]UNKNOWN[/bold yellow]"
            else:
                res_fmt = f"[dim]{res}[/dim]"
            ver_table.add_row(item, res_fmt)

        console.print(ver_table)

    if summary.managed_files:
        console.print("\n[bold]Managed Files:[/bold]")
        for mf in summary.managed_files:
            console.print(f"  * [cyan]{mf}[/cyan]")
