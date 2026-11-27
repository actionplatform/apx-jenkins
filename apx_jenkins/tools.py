"""`jenkins_*` MCP tools: the jobs of a folder, and `jenkins_build` / `jenkins_queue` as aliases of the core's `ci_run` / `ci_queue` on this plugin's runner."""

from __future__ import annotations

from typing import Annotated, Any

from action_platform.mcp.annotations import READ_ONLY
from action_platform.plugins import ci
from pydantic import Field

from apx_jenkins.models import Job
from apx_jenkins.settings import SLUG, connect


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
    ) -> dict[str, Any]:
        """Alias of ci_run on Jenkins: one build's status, timing, parameters, causes and changes; its console is ci_log."""
        return ci.run(ci.runner(SLUG, options), job, number)

    @mcp.tool(annotations=READ_ONLY)
    def queue() -> list[dict[str, Any]]:
        """Alias of ci_queue on Jenkins: what waits in the build queue, since when and why."""
        return ci.queue(ci.runner(SLUG, options))
