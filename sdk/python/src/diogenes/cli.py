"""
diogenes.cli
~~~~~~~~~~~~
The Diogenes CLI.

Commands:
    diogenes runs list           List recent agent runs
    diogenes runs show <id>      Show the full trace for a run
    diogenes init                Scaffold a diogenes.yaml in the current dir
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich import box

from .storage import TraceStore, Run, Step

console = Console()

DEFAULT_DB = ".diogenes/traces.db"


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _ns_to_dt(ns: int) -> str:
    """Convert nanoseconds-since-epoch to a human-readable local time."""
    dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _status_color(status: str) -> str:
    return {"OK": "green", "ERROR": "red"}.get(status, "yellow")


def _step_icon(step: Step) -> str:
    if step.kind == "llm_call":
        return "🤖"
    if step.kind == "tool_call":
        return "🔧"
    return "•"


def _step_label(step: Step) -> Text:
    t = Text()
    icon = _step_icon(step)

    if step.kind == "llm_call":
        model = step.model or "unknown-model"
        t.append(f"{icon} LLM  ", style="bold cyan")
        t.append(f"{model}", style="cyan")
        tokens = step.input_tokens + step.output_tokens
        if tokens:
            t.append(f"  {tokens} tok", style="dim")
    elif step.kind == "tool_call":
        t.append(f"{icon} tool ", style="bold yellow")
        t.append(f"{step.tool_name or step.name}", style="yellow")
    else:
        t.append(f"  {step.name}", style="dim")

    t.append(f"  {_ms(step.duration_ms)}", style="dim")
    color = _status_color(step.status)
    t.append(f"  [{step.status}]", style=color)
    return t


# ------------------------------------------------------------------ #
# CLI groups
# ------------------------------------------------------------------ #

@click.group()
@click.version_option("0.1.0", prog_name="diogenes")
def cli():
    """
    Diogenes — honest testing for agentic systems.

    Walk through your agent's runs with a lantern.
    """


@cli.group()
def runs():
    """Inspect agent runs captured in the local trace store."""


# ------------------------------------------------------------------ #
# diogenes runs list
# ------------------------------------------------------------------ #

@runs.command("list")
@click.option("--db", default=DEFAULT_DB, help="Path to trace database.", show_default=True)
@click.option("--limit", default=20, help="Number of runs to show.", show_default=True)
def runs_list(db: str, limit: int):
    """List recent agent runs, newest first."""
    try:
        store = TraceStore(db_path=db)
        run_list = store.list_runs(limit=limit)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if not run_list:
        console.print("[dim]No runs found. Run an instrumented agent first.[/dim]")
        return

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white",
        border_style="bright_black",
        expand=False,
    )
    table.add_column("Run ID", style="dim", min_width=12, max_width=12)
    table.add_column("Name", style="bold")
    table.add_column("Started", style="dim")
    table.add_column("Duration", justify="right")
    table.add_column("LLM", justify="right", style="cyan")
    table.add_column("Tools", justify="right", style="yellow")
    table.add_column("Tokens", justify="right")
    table.add_column("Status", justify="center")

    for r in run_list:
        status_style = _status_color(r.status)
        table.add_row(
            r.run_id[:12],
            r.name,
            _ns_to_dt(r.start_time) if r.start_time else "-",
            _ms(r.duration_ms),
            str(len(r.llm_calls)),
            str(len(r.tool_calls)),
            str(r.total_tokens) if r.total_tokens else "-",
            Text(r.status, style=status_style),
        )

    console.print()
    console.print(table)
    console.print(
        f"\n[dim]Showing {len(run_list)} run(s). "
        f"Use [bold]diogenes runs show <id>[/bold] for details.[/dim]"
    )


# ------------------------------------------------------------------ #
# diogenes runs show
# ------------------------------------------------------------------ #

@runs.command("show")
@click.argument("run_id")
@click.option("--db", default=DEFAULT_DB, help="Path to trace database.", show_default=True)
@click.option("--tools-only", is_flag=True, help="Show only tool call steps.")
def runs_show(run_id: str, db: str, tools_only: bool):
    """Show the full trace for a run. RUN_ID can be a prefix."""
    try:
        store = TraceStore(db_path=db)
        r = store.get_run(run_id)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if not r:
        console.print(f"[red]No run found matching ID:[/red] {run_id}")
        sys.exit(1)

    # -- Header panel --
    status_color = _status_color(r.status)
    header = Text()
    header.append(f"  {r.name}\n", style="bold white")
    header.append(f"  {r.run_id}\n", style="dim")
    header.append(f"  {_ns_to_dt(r.start_time)}  ", style="dim")
    header.append(f"⏱  {_ms(r.duration_ms)}  ", style="dim")
    header.append(f"[{r.status}]", style=status_color)

    console.print()
    console.print(Panel(header, title="[bold]Agent Run[/bold]", border_style="bright_blue"))

    # -- Summary stats --
    stats = Table(box=None, show_header=False, padding=(0, 2))
    stats.add_column(style="dim", justify="right")
    stats.add_column()

    stats.add_row("LLM calls",  f"[cyan]{len(r.llm_calls)}[/cyan]")
    stats.add_row("Tool calls", f"[yellow]{len(r.tool_calls)}[/yellow]")
    stats.add_row("Input tokens", f"{r.total_input_tokens:,}")
    stats.add_row("Output tokens", f"{r.total_output_tokens:,}")
    stats.add_row("Total tokens", f"[bold]{r.total_tokens:,}[/bold]")
    if r.tools_used:
        stats.add_row("Tools used", ", ".join(sorted(set(r.tools_used))))

    console.print(stats)
    console.print()

    # -- Step-by-step trace tree --
    steps = r.steps if not tools_only else r.tool_calls
    tree = Tree("[bold]Trace[/bold]", guide_style="bright_black")

    for i, step in enumerate(steps, 1):
        node = tree.add(Text.assemble(
            Text(f"[{i}] ", style="dim"),
            _step_label(step),
        ))

        # Drill into tool call details
        if step.kind == "tool_call":
            if step.tool_input:
                node.add(Text(f"in  {step.tool_input[:120]}", style="dim"))
            if step.tool_output:
                node.add(Text(f"out {step.tool_output[:120]}", style="dim"))

        # Drill into LLM call details
        if step.kind == "llm_call":
            tc_count = step.attributes.get("diogenes.llm.tool_calls_count", 0)
            if tc_count:
                node.add(Text(f"→ requested {tc_count} tool call(s)", style="dim cyan"))
            out = step.attributes.get("diogenes.llm.output_text", "")
            if out:
                preview = out[:100].replace("\n", " ")
                node.add(Text(f'"{preview}…"', style="dim"))

    console.print(tree)
    console.print()


# ------------------------------------------------------------------ #
# diogenes init
# ------------------------------------------------------------------ #

@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing diogenes.yaml.")
def init(force: bool):
    """Scaffold a diogenes.yaml in the current directory."""
    target = Path("diogenes.yaml")
    if target.exists() and not force:
        console.print("[yellow]diogenes.yaml already exists.[/yellow] Use --force to overwrite.")
        return

    config = """\
version: "1.0"

# Local trace database — commit .diogenes/traces.db to share traces with
# your team, or add it to .gitignore to keep them local.
collector:
  storage: .diogenes/traces.db

# Optional: forward traces to a remote OTel collector or Diogenes Cloud.
# remote:
#   otlp_endpoint: https://ingest.diogenes.dev/otlp

# Scenarios — define your test cases here.
scenarios: []
#  - id: example
#    description: "Agent handles a simple request end-to-end"
#    trace: .diogenes/traces/example.trace
#    replay:
#      mode: full        # full | partial | live
#      mock_llm: true
#      mock_tools: []
#    assertions:
#      - tool_called: my_tool
#      - tool_not_called: dangerous_tool
#      - step_count: { max: 10 }
#      - tokens: { max: 5000 }
"""
    target.write_text(config)

    diogenes_dir = Path(".diogenes")
    diogenes_dir.mkdir(exist_ok=True)

    gitignore = Path(".diogenes/.gitignore")
    if not gitignore.exists():
        gitignore.write_text("# Uncomment to exclude traces from version control\n# traces.db\n")

    console.print("[green]✓[/green] Created [bold]diogenes.yaml[/bold]")
    console.print("[green]✓[/green] Created [bold].diogenes/[/bold] directory")
    console.print("\n[dim]Next steps:[/dim]")
    console.print("  1. Instrument your agent with [bold]import diogenes[/bold]")
    console.print("  2. Run your agent")
    console.print("  3. [bold]diogenes runs list[/bold]")


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

def main():
    cli()


if __name__ == "__main__":
    main()