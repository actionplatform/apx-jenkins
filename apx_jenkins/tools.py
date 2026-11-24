"""`jenkins_*` MCP tools: read-only looks at jobs, builds and the queue."""

from __future__ import annotations

from typing import Annotated, Any

from action_platform.mcp.annotations import READ_ONLY
from pydantic import Field

from apx_jenkins.models import Build, Job, QueueItem
from apx_jenkins.settings import connect


def register_tools(mcp: Any, options: Any) -> None:
    @mcp.tool(annotations=READ_ONLY)
    def jobs(
        folder: Annotated[
            str, Field(description="Folder path such as team/app; empty for the root")
        ] = "",
    ) -> list[Job]:
        """Jobs of a folder, or of the root, with the last build's number, result, time and duration."""
        return connect(options).jobs(folder)

    @mcp.tool(annotations=READ_ONLY)
    def build(
        job: Annotated[str, Field(description="Job path such as team/app/main")],
        number: Annotated[int, Field(description="Build number; 0 for the last")] = 0,
        lines: Annotated[
            int,
            Field(description="Console lines to return from the end", ge=0, le=2000),
        ] = 100,
    ) -> Build:
        """One build: result, duration, what started it, the revision and the tail of its console."""
        return connect(options).build(job, number, lines)

    @mcp.tool(annotations=READ_ONLY)
    def queue() -> list[QueueItem]:
        """What waits in the build queue and why."""
        return connect(options).queue()
