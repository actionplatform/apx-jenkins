"""A Jenkins controller in memory. It answers only the routes and fields the real one sends — `_class` on every object, empty entries among a build's actions, `tree` required — and keeps role-strategy state, so a change shows up on the next read."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from action_platform.core.service_access import Response

from apx_jenkins.client import JenkinsError, JenkinsHttp

FOLDER = "com.cloudbees.hudson.plugins.folder.Folder"
PIPELINE = "org.jenkinsci.plugins.workflow.job.WorkflowJob"
RUN = "org.jenkinsci.plugins.workflow.job.WorkflowRun"
BASE = "https://jenkins.example.com"


class FakeJenkins(JenkinsHttp):
    def __init__(self, active: bool = True, legacy: bool = False) -> None:
        super().__init__("jenkins", BASE, {})
        self.active = active
        self.legacy = legacy
        self.roles: dict[str, dict[str, dict]] = {"globalRoles": {}, "projectRoles": {}}
        self.calls: list[tuple[str, str, Any]] = []
        self.items: dict[str, dict] = {}
        self.queued: list[dict] = []
        self.status = 0
        self.last_query = ""

    def role(
        self,
        kind: str,
        name: str,
        permissions: list[str],
        sids: list[str],
        pattern: str = ".*",
    ) -> None:
        self.roles[kind][name] = {
            "permissions": permissions,
            "pattern": pattern,
            "sids": list(sids),
        }

    def folder(self, name: str) -> None:
        self.items[name] = {"_class": FOLDER}

    def job(self, name: str, color: str = "notbuilt") -> None:
        self.items[name] = {"_class": PIPELINE, "color": color, "builds": {}}

    def run(
        self,
        job: str,
        number: int,
        result: str | None,
        causes: list[str],
        console: list[str],
        timestamp: int = 1795502400777,
        duration: int = 99179,
        sha: str = "96a8ee275617d5b6f63a7aa834a6c23634c7ee6b",
        branch: str = "refs/remotes/origin/main",
    ) -> None:
        actions: list[dict] = [
            {
                "_class": "hudson.model.CauseAction",
                "causes": [{"_class": "x", "shortDescription": c} for c in causes],
            },
            {"_class": "hudson.model.ParametersAction"},
            {},
            {
                "_class": "hudson.plugins.git.util.BuildData",
                "lastBuiltRevision": {"SHA1": sha, "branch": [{"name": branch}]},
            },
            {},
            {"_class": "org.jenkinsci.plugins.workflow.cps.EnvActionImpl"},
        ]
        self.items[job]["builds"][number] = {
            "_class": RUN,
            "actions": actions,
            "building": result is None,
            "duration": 0 if result is None else duration,
            "fullDisplayName": f"{job.replace('/', ' » ')} #{number}",
            "number": number,
            "result": result,
            "timestamp": timestamp,
            "url": f"{BASE}/{self.path(job)}/{number}/",
            "console": console,
        }

    def path(self, name: str) -> str:
        return "/".join(f"job/{p}" for p in name.split("/"))

    def writes(self) -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]

    def send(self, method, path, body=None, query=None, form=None) -> Response:
        url = urllib.parse.urlsplit(self.url(path, query))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        q.update({k: str(v) for k, v in (form or {}).items()})
        route = url.path.strip("/")
        sent = json.loads(json.dumps(body)) if body is not None else None
        self.last_query = url.query
        self.calls.append((method, route, sent if form is None else dict(form)))

        if self.status:
            return Response(self.status, "<html>refused</html>")

        return self.handle(method, route, q)

    def lines(self, path: str, limit: int) -> tuple[list[str], int]:
        self.calls.append(("GET", path.strip("/"), {"limit": limit}))
        route = path.strip("/").removesuffix("/consoleText")
        name, number = self.locate(route)
        build = self.items.get(name or "", {}).get("builds", {}).get(number or -1)

        if build is None:
            raise JenkinsError("jenkins: GET answered 404")

        text = build["console"]

        return text[-limit:], len(text)

    def locate(self, route: str) -> tuple[str | None, int | None]:
        parts = route.split("/")
        names = (
            [urllib.parse.unquote(n) for n in parts[1::2]] if parts[0] == "job" else []
        )
        trailing = parts[2 * len(names) :]
        number = None

        if trailing and trailing[0] == "lastBuild":
            number = -1
        elif trailing and trailing[0].isdigit():
            number = int(trailing[0])

        return "/".join(names), number

    def handle(self, method: str, route: str, q: dict) -> Response:
        if route.startswith("role-strategy/"):
            return self.roles_handle(route, q)

        if route == "queue/api/json":
            return Response(
                200,
                {
                    "_class": "hudson.model.Queue",
                    "items": [
                        dict(i, _class="hudson.model.Queue$BuildableItem")
                        for i in self.queued
                    ],
                },
            )

        if not route.endswith("api/json") or "tree" not in q:
            return Response(404, "<html>Not Found</html>")

        route = route.removesuffix("/api/json").removesuffix("api/json")
        name, number = self.locate(route)

        if number is not None:
            item = self.items.get(name or "")
            builds = (item or {}).get("builds", {})
            key = max(builds) if number == -1 and builds else number
            build = builds.get(key)

            if build is None:
                return Response(404, "<html>Not Found</html>")

            return Response(200, {k: v for k, v in build.items() if k != "console"})

        if name and name not in self.items:
            return Response(404, "<html>Not Found</html>")

        if not name:
            return Response(
                200, {"_class": "hudson.model.Hudson", "jobs": self.children("")}
            )

        item = self.items[name]

        if item["_class"] == FOLDER:
            return Response(200, {"_class": FOLDER, "jobs": self.children(name)})

        return Response(200, self.describe(name))

    def children(self, folder: str) -> list[dict]:
        prefix = f"{folder}/" if folder else ""

        return [
            self.describe(n)
            for n in self.items
            if n.startswith(prefix) and "/" not in n[len(prefix) :]
        ]

    def describe(self, name: str) -> dict:
        item = self.items[name]
        out: dict[str, Any] = {
            "_class": item["_class"],
            "name": name.rsplit("/", 1)[-1],
            "fullName": name,
            "url": f"{BASE}/{self.path(name)}/",
        }

        if item["_class"] == FOLDER:
            return out

        out["color"] = item["color"]
        builds = item["builds"]

        if builds:
            b = builds[max(builds)]
            out["lastBuild"] = {
                k: b[k]
                for k in (
                    "_class",
                    "building",
                    "duration",
                    "number",
                    "result",
                    "timestamp",
                )
            }

        return out

    def roles_handle(self, route: str, q: dict) -> Response:
        if not self.active:
            return Response(404, "Not Found")

        verb = route.rsplit("/", 1)[-1]
        roles = self.roles[q["type"]]

        if verb == "getAllRoles":
            if self.legacy:
                return Response(200, {n: list(r["sids"]) for n, r in roles.items()})

            return Response(
                200,
                {
                    n: [{"type": "USER", "sid": s} for s in r["sids"]]
                    for n, r in roles.items()
                },
            )

        if verb == "getRole":
            r = roles.get(q["roleName"])

            if r is None:
                return Response(200, {})

            return Response(
                200,
                {
                    "permissionIds": {p: True for p in r["permissions"]},
                    "pattern": r["pattern"],
                    "sids": r["sids"],
                },
            )

        if verb == "addRole":
            roles.setdefault(
                q["roleName"],
                {
                    "permissions": q["permissionIds"].split(","),
                    "pattern": q.get("pattern", ".*"),
                    "sids": [],
                },
            )
            return Response(200, None)

        if verb in ("assignRole", "unassignRole") and self.legacy:
            return self.assign(roles, verb, q["roleName"], q["sid"])

        if verb in ("assignUserRole", "unassignUserRole") and not self.legacy:
            return self.assign(roles, verb, q["roleName"], q["user"])

        if verb in ("assignGroupRole", "unassignGroupRole") and not self.legacy:
            return self.assign(roles, verb, q["roleName"], q["group"])

        return Response(404, "Not Found")

    def assign(self, roles: dict, verb: str, role: str, sid: str) -> Response:
        if role not in roles:
            return Response(400, "no such role")

        sids = roles[role]["sids"]

        if verb.startswith("assign") and sid not in sids:
            sids.append(sid)
        elif verb.startswith("unassign") and sid in sids:
            sids.remove(sid)

        return Response(200, None)
