"""`action-platform jenkins …` — the same reads as the tools, from a terminal."""

from __future__ import annotations

from typing import Any

import typer

from apx_jenkins.settings import connect

app = typer.Typer(help="Jenkins: jobs, builds, the queue.", no_args_is_help=True)
options: Any | None = None


@app.command("jobs")
def jobs(
    folder: str = typer.Argument("", help="Folder path; the root when empty"),
) -> None:
    """List the jobs of a folder with their last build."""
    for j in connect(options).jobs(folder):
        last = f"#{j.last_number} {j.last_result} {j.last_at}" if j.last_number else "-"
        typer.echo(f"{j.name}\t{j.kind}\t{j.status or '-'}\t{last}")


@app.command("build")
def build(
    job: str,
    number: int = typer.Argument(0, help="Build number; the last when 0"),
    lines: int = typer.Option(100, help="Console lines from the end"),
) -> None:
    """One build and the tail of its console."""
    b = connect(options).build(job, number, lines)
    typer.echo(f"{b.name}\t{b.result}\t{b.started_at}\t{b.duration_s}s")
    typer.echo(f"causes: {'; '.join(b.causes) or '-'}")
    typer.echo(f"revision: {b.revision or '-'} {b.branch or ''}".rstrip())

    if b.console_truncated:
        typer.echo(f"console: last {len(b.console)} of {b.console_lines} lines")

    for line in b.console:
        typer.echo(line)


@app.command("queue")
def queue() -> None:
    """What waits in the build queue and why."""
    for i in connect(options).queue():
        typer.echo(f"{i.job}\t{i.since}\t{i.why or '-'}")
