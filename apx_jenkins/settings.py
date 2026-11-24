"""Where a setting comes from, in order: the process environment (`AP_JENKINS_<KEY>`), then the plugin's options file on this machine."""

from __future__ import annotations

import os
from typing import Any, Optional

from action_platform.plugins.options import FileOptions

from apx_jenkins.client import Jenkins, JenkinsError, connection

SLUG = "jenkins"
KEYS = ("url", "user", "token")


def setting(key: str, options: Any = None) -> Optional[str]:
    found = os.environ.get(f"AP_{SLUG}_{key}".upper())

    if not found:
        found = (options or FileOptions(SLUG)).get(key)

    return str(found) if found else None


def connect(options: Any = None) -> Jenkins:
    values = {k: setting(k, options) for k in KEYS}
    missing = [k for k, v in values.items() if not v]

    if missing:
        names = ", ".join(f"AP_JENKINS_{k.upper()}" for k in missing)

        raise JenkinsError(
            f"jenkins: set {names} or the plugin's {', '.join(missing)} options"
        )

    return Jenkins(
        connection(str(values["url"]), str(values["user"]), str(values["token"]))
    )
