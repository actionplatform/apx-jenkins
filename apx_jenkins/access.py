"""Jenkins under Role-based Authorization Strategy (the role-strategy plugin): a global role per account, and item roles that open one folder each. A folder is invisible without Overall/Read, so a folder grant asks for global `read` too (`companions`)."""

from __future__ import annotations

import re
from typing import Any, Optional

from action_platform.core.service_access import (
    AccessError,
    AccessProvider,
    Grant,
    Http,
    Op,
)

from apx_jenkins.client import connection

BASE = "role-strategy/strategy"
GLOBAL = "globalRoles"
ITEMS = "projectRoles"
GROUP = "group:"

GLOBAL_PERMISSIONS = {
    "read": ["hudson.model.Hudson.Read", "hudson.model.View.Read"],
    "admin": ["hudson.model.Hudson.Administer"],
}
READ = ["hudson.model.Item.Read", "hudson.model.Item.Discover"]
BUILD = READ + [
    "hudson.model.Item.Build",
    "hudson.model.Item.Cancel",
    "hudson.model.Item.Workspace",
]
CONFIGURE = BUILD + [
    "hudson.model.Item.Configure",
    "hudson.model.Item.Create",
    "hudson.model.Item.Delete",
    "hudson.model.Run.Delete",
    "hudson.model.Run.Update",
]
FOLDER_PERMISSIONS = {"read": READ, "build": BUILD, "configure": CONFIGURE}


def folder_role(folder: str, level: str) -> str:
    return f"folder:{folder}:{level}"


def folder_pattern(folder: str) -> str:
    return "^" + re.escape(folder) + "(/.*)?$"


class JenkinsProvider(AccessProvider):
    """Accounts are Jenkins user ids (`group:<name>` for a group). `global` is `read` or `admin`; `folder/<name>` is `read`, `build` or `configure` on that folder and every job in it; roles this plugin did not create stay addressable as `global-role/<name>` and `item-role/<name>`."""

    name = "jenkins"
    levels = {
        "global": ("read", "admin"),
        "folder": ("read", "build", "configure"),
        "global-role": ("assigned",),
        "item-role": ("assigned",),
    }

    def __init__(
        self, url: str, user: str, token: str, http: Optional[Http] = None
    ) -> None:
        self.http = http or connection(url, user, token)

    def read(self) -> list[Grant]:
        grants: list[Grant] = []

        for role, sids in self._roles(GLOBAL).items():
            for account in sids:
                if role in GLOBAL_PERMISSIONS:
                    grants.append(Grant("jenkins", account, "global", role))
                else:
                    grants.append(
                        Grant("jenkins", account, f"global-role/{role}", "assigned")
                    )

        for role, sids in self._roles(ITEMS).items():
            parsed = _folder(role)

            for account in sids:
                if parsed:
                    grants.append(
                        Grant("jenkins", account, f"folder/{parsed[0]}", parsed[1])
                    )
                else:
                    grants.append(
                        Grant("jenkins", account, f"item-role/{role}", "assigned")
                    )

        return self.strongest(grants)

    def apply(self, op: Op) -> None:
        g = op.grant
        kind, _, target = g.resource.partition("/")

        if kind == "global":
            kind_roles, role, old = GLOBAL, g.level, op.previous
            if op.action != "remove":
                self._ensure(GLOBAL, role, GLOBAL_PERMISSIONS[role])
        elif kind == "folder":
            kind_roles, role = ITEMS, folder_role(target, g.level)
            old = folder_role(target, op.previous) if op.previous else ""
            if op.action != "remove":
                self._ensure(
                    ITEMS, role, FOLDER_PERMISSIONS[g.level], folder_pattern(target)
                )
        elif kind in ("global-role", "item-role"):
            kind_roles, role, old = (
                (GLOBAL if kind == "global-role" else ITEMS),
                target,
                "",
            )
        else:
            raise AccessError(f"jenkins: cannot apply {g.resource}")

        if op.action == "remove":
            self._assign(kind_roles, role, g.account, False)
            return

        self._assign(kind_roles, role, g.account, True)

        if op.action == "change" and old and old != role:
            self._assign(kind_roles, old, g.account, False)

    def companions(self, resource: str) -> list[tuple[str, str]]:
        if self.kind(resource) in ("folder", "item-role"):
            return [("global", "read")]

        return []

    def guard(self, ops: list[Op], live: list[Grant]) -> dict[Op, str]:
        admins = {
            g.account for g in live if g.resource == "global" and g.level == "admin"
        }
        losing = {
            op.grant.account
            for op in ops
            if op.grant.resource == "global"
            and op.grant.account in admins
            and (op.action == "remove" or op.grant.level != "admin")
        }

        if admins and losing >= admins:
            return {
                op: f"jenkins: {op.grant.account} is the last global admin"
                for op in ops
                if op.grant.resource == "global" and op.grant.account in losing
            }

        return {}

    def _roles(self, kind: str) -> dict[str, list[str]]:
        res = self.http.send("GET", f"{BASE}/getAllRoles", query={"type": kind})

        if res.status == 404:
            raise AccessError(
                "jenkins: Role-based Authorization Strategy is not active — install the role-strategy plugin and select it under Security"
            )

        if res.status >= 400 or not isinstance(res.data, dict):
            raise AccessError(f"jenkins: getAllRoles answered {res.status}")

        return {
            role: [_account(s) for s in sids or []] for role, sids in res.data.items()
        }

    def _ensure(
        self, kind: str, role: str, permissions: list[str], pattern: str = ""
    ) -> None:
        res = self.http.send(
            "GET", f"{BASE}/getRole", query={"type": kind, "roleName": role}
        )

        if res.status == 200 and isinstance(res.data, dict) and res.data:
            return

        form = {
            "type": kind,
            "roleName": role,
            "permissionIds": ",".join(permissions),
            "overwrite": "false",
        }

        if pattern:
            form["pattern"] = pattern

        self.http.call("POST", f"{BASE}/addRole", form=form)

    def _assign(self, kind: str, role: str, account: str, on: bool) -> None:
        verb = "assign" if on else "unassign"

        if account.startswith(GROUP):
            name, field, sid = f"{verb}GroupRole", "group", account[len(GROUP) :]
        else:
            name, field, sid = f"{verb}UserRole", "user", account

        res = self.http.send(
            "POST", f"{BASE}/{name}", form={"type": kind, "roleName": role, field: sid}
        )

        if res.status == 404:
            self.http.call(
                "POST",
                f"{BASE}/{verb}Role",
                form={"type": kind, "roleName": role, "sid": sid},
            )
        elif res.status >= 400:
            raise AccessError(f"jenkins: {name} {role} answered {res.status}")


def _account(sid: Any) -> str:
    if isinstance(sid, dict):
        name = str(sid.get("sid", ""))

        return GROUP + name if str(sid.get("type", "")).upper() == "GROUP" else name

    return str(sid)


def _folder(role: str) -> Optional[tuple[str, str]]:
    parts = role.split(":")

    if len(parts) == 3 and parts[0] == "folder" and parts[2] in FOLDER_PERMISSIONS:
        return parts[1], parts[2]

    return None
