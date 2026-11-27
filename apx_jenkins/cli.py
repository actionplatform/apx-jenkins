"""`action-platform jenkins …` — the jobs of a folder, and `build` / `queue` as aliases of `action-platform ci run` / `ci queue` on this plugin's runner."""

from __future__ import annotations

from typing import Any

import typer
from action_platform.cli.commands import ci as ci_cmd

from apx_jenkins.settings import SLUG, connect

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
) -> None:
    """One build, as `action-platform ci run`; its console is `action-platform ci log`."""
    ci_cmd.run(job, number, SLUG)


@app.command("queue")
def queue() -> None:
    """What waits in the build queue and why, as `action-platform ci queue`."""
    ci_cmd.queue(SLUG)
