"""The plugin: access on Jenkins' role-strategy, and the tools and commands that read jobs, builds and the queue."""

from __future__ import annotations

from typing import Any, Optional

from action_platform.abc import Option, Plugin, Surface
from action_platform.core.service_access import AccessError, AccessProvider

from apx_jenkins import cli
from apx_jenkins.access import JenkinsProvider
from apx_jenkins.tools import register_tools


class JenkinsPlugin(Plugin):
    slug = "jenkins"
    name = "Jenkins"
    description = "Jenkins: access through Role-based Authorization Strategy, and read-only tools for jobs, builds and the queue"
    min_core = "0.33"
    needs = [
        "net: the host of the url option",
        "env: AP_JENKINS_URL, AP_JENKINS_USER, AP_JENKINS_TOKEN (tools on a machine; the plugin's options are the fallback)",
        "jenkins: the role-strategy plugin selected as the authorization strategy (access)",
    ]
    options = [
        Option(
            "url",
            "Jenkins URL",
            "url",
            help="The controller's address, e.g. https://jenkins.example.com",
        ),
        Option(
            "user",
            "User id",
            "text",
            help="The Jenkins user id of an administrator",
        ),
        Option(
            "token",
            "API token",
            "secret",
            help="That user's API token (user menu, Security, API Token)",
        ),
    ]

    def register(self, surface: Surface) -> None:
        cli.options = surface.options

        if surface.mcp is not None:
            register_tools(surface.mcp, surface.options)

        if surface.cli is not None:
            surface.cli.add_typer(cli.app, name="jenkins")

    def access(self, options: Any) -> Optional[AccessProvider]:
        url = options.get("url")

        if not url:
            return None

        values = {k: options.get(k) for k in ("user", "token")}
        missing = [k for k, v in values.items() if not v]

        if missing:
            raise AccessError(
                f"jenkins: set the plugin's {', '.join(missing)} option"
                f"{'s' if len(missing) > 1 else ''} to manage access"
            )

        return JenkinsProvider(str(url), str(values["user"]), str(values["token"]))
