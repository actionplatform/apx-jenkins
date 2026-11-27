"""A Jenkins controller in memory. It answers only the routes and fields the real one sends — `_class` on every object, empty entries among a build's actions, `tree` required — and keeps role-strategy state, so a change shows up on the next read."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from action_platform.core.service_access import Response

from apx_jenkins.client import JenkinsError, JenkinsHttp

FOLDER = "com.cloudbees.hudson.plugins.folder.Folder"
PIPELINE = "org.jenkinsci.plugins.workflow.job.WorkflowJob"
RUN = "org.jenkinsci.plugins.workflow.job.WorkflowRun"
BASE = "https://jenkins.example.com"
HIDDEN = {"console", "stages", "tests", "files", "inputs", "nodes"}
RANGE = re.compile(r"\{(\d+),(\d+)\}$")


class FakeJenkins(JenkinsHttp):
    def __init__(self, active: bool = True, legacy: bool = False) -> None:
        super().__init__("jenkins", BASE, {})
        self.active = active
        self.legacy = legacy
        self.roles: dict[str, dict[str, dict]] = {"globalRoles": {}, "projectRoles": {}}
        self.calls: list[tuple[str, str, Any]] = []
        self.items: dict[str, dict] = {}
        self.queued: list[dict] = []
        self.waiting: dict[int, dict] = {}
        self.computers: list[dict] = []
        self.read = 0
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
        params: dict[str, str] | None = None,
        changes: list[tuple[str, str, str]] = (),
    ) -> None:
        actions: list[dict] = [
            {
                "_class": "hudson.model.CauseAction",
                "causes": [self.cause(c) for c in causes],
            },
            {
                "_class": "hudson.model.ParametersAction",
                "parameters": [
                    {
                        "_class": "hudson.model.StringParameterValue",
                        "name": k,
                        "value": v,
                    }
                    for k, v in (params or {}).items()
                ],
            },
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
            "changeSets": [
                {
                    "_class": "hudson.plugins.git.GitChangeSetList",
                    "kind": "git",
                    "items": [
                        {
                            "_class": "hudson.plugins.git.GitChangeSet",
                            "commitId": commit,
                            "msg": msg,
                            "author": {"fullName": author},
                        }
                        for commit, author, msg in changes
                    ],
                }
            ]
            if changes
            else [],
            "artifacts": [],
            "console": console,
            "stages": [],
            "tests": None,
            "files": {},
            "inputs": [],
            "nodes": {},
        }

    @staticmethod
    def cause(description: str) -> dict:
        if description.startswith("Started by user "):
            user = description.removeprefix("Started by user ")

            return {
                "_class": "hudson.model.Cause$UserIdCause",
                "shortDescription": description,
                "userId": user,
                "userName": user,
            }

        return {"_class": "hudson.model.Cause", "shortDescription": description}

    def build_of(self, job: str, number: int) -> dict:
        return self.items[job]["builds"][number]

    def stage(
        self,
        job: str,
        number: int,
        id: str,
        name: str,
        status: str,
        log: str = "",
        started: int = 1795502401000,
        duration: int = 1200,
    ) -> None:
        build = self.build_of(job, number)
        build["stages"].append(
            {
                "_links": {
                    "self": {
                        "href": f"/{self.path(job)}/{number}/execution/node/{id}/wfapi/describe"
                    }
                },
                "id": id,
                "name": name,
                "execNode": "",
                "status": status,
                "startTimeMillis": started,
                "durationMillis": duration,
                "pauseDurationMillis": 0,
            }
        )
        build["nodes"][id] = {"status": status, "log": log}

    def report(
        self, job: str, number: int, passed: int, skipped: int, failures: list[dict]
    ) -> None:
        self.build_of(job, number)["tests"] = {
            "_class": "hudson.tasks.junit.TestResult",
            "duration": 1.5,
            "empty": False,
            "failCount": len(failures),
            "passCount": passed,
            "skipCount": skipped,
            "suites": [
                {
                    "_class": "hudson.tasks.junit.SuiteResult",
                    "name": "suite",
                    "cases": [
                        {
                            "_class": "hudson.tasks.junit.CaseResult",
                            "className": "app.tests.Ok",
                            "name": "test_passes",
                            "status": "PASSED",
                            "errorDetails": None,
                            "errorStackTrace": None,
                        },
                        *(
                            {
                                "_class": "hudson.tasks.junit.CaseResult",
                                "status": "FAILED",
                                **f,
                            }
                            for f in failures
                        ),
                    ],
                }
            ],
        }

    def archive(self, job: str, number: int, path: str, content: bytes) -> None:
        build = self.build_of(job, number)
        build["artifacts"].append(
            {
                "displayPath": path.rsplit("/", 1)[-1],
                "fileName": path.rsplit("/", 1)[-1],
                "relativePath": path,
            }
        )
        build["files"][path] = content

    def ask(self, job: str, number: int, id: str, message: str) -> None:
        base = f"/{self.path(job)}/{number}/input/{id}"
        self.build_of(job, number)["inputs"].append(
            {
                "id": id,
                "proceedText": "Proceed",
                "message": message,
                "inputs": [],
                "proceedUrl": f"{base}/proceedEmpty",
                "abortUrl": f"{base}/abort",
                "redirectApprovalUrl": f"/{self.path(job)}/{number}/input/",
            }
        )

    def leave(self, item: int, job: str, number: int) -> None:
        self.waiting[item] = {
            "_class": "hudson.model.Queue$LeftItem",
            "id": item,
            "cancelled": False,
            "executable": {
                "_class": RUN,
                "number": number,
                "url": f"{BASE}/{self.path(job)}/{number}/",
            },
        }

    def cancel(self, item: int) -> None:
        self.waiting[item] = {
            "_class": "hudson.model.Queue$LeftItem",
            "id": item,
            "cancelled": True,
            "executable": None,
        }

    def text_of(self, build: dict) -> bytes:
        return "".join(f"{line}\n" for line in build["console"]).encode()

    def read_bytes(self, path: str, query: dict, limit: int):
        start = int(query["start"])
        self.calls.append(("GET", path.strip("/"), {"start": start, "limit": limit}))
        name, number = self.locate(
            path.strip("/").removesuffix("/logText/progressiveText")
        )
        build = self.items.get(name or "", {}).get("builds", {}).get(number or -1)

        if build is None:
            raise JenkinsError("jenkins: GET answered 404")

        data = self.text_of(build)
        headers = {
            "content-type": "text/plain;charset=UTF-8",
            "x-text-size": str(len(data)),
        }

        if build["building"]:
            headers["x-more-data"] = "true"

        sent = data[start : start + limit + 1]
        self.read += len(sent)

        return sent, headers, "utf-8"

    def stream(self, path: str, chunk: int = 65536):
        self.calls.append(("GET", path.strip("/"), None))
        route, _, file = path.strip("/").partition("/artifact/")
        name, number = self.locate(route)
        build = self.items.get(name or "", {}).get("builds", {}).get(number or -1)
        content = (build or {}).get("files", {}).get(urllib.parse.unquote(file))

        if content is None:
            raise JenkinsError("jenkins: GET answered 404")

        for i in range(0, len(content), chunk):
            yield content[i : i + chunk]

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

        if route == "crumbIssuer/api/json":
            return Response(
                200,
                {
                    "_class": "hudson.security.csrf.DefaultCrumbIssuer",
                    "crumb": "c1",
                    "crumbRequestField": "Jenkins-Crumb",
                },
            )

        if method == "POST":
            return self.queue_build(route, q)

        found = re.fullmatch(r"queue/item/(\d+)/api/json", route)

        if found and "tree" in q:
            item = self.waiting.get(int(found.group(1)))

            if item is None:
                return Response(404, "<html>Not Found</html>")

            return Response(200, item)

        if route == "computer/api/json" and "tree" in q:
            return Response(
                200, {"_class": "hudson.model.ComputerSet", "computer": self.computers}
            )

        for suffix in (
            "/wfapi/describe",
            "/wfapi/pendingInputActions",
            "/testReport/api/json",
        ):
            if route.endswith(suffix):
                return self.run_detail(route.removesuffix(suffix), suffix)

        found = re.fullmatch(r"(.+)/execution/node/([^/]+)/wfapi/log", route)

        if found:
            return self.node_log(found.group(1), urllib.parse.unquote(found.group(2)))

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

            return Response(200, {k: v for k, v in build.items() if k not in HIDDEN})

        if name and name not in self.items:
            return Response(404, "<html>Not Found</html>")

        if not name:
            return Response(
                200, {"_class": "hudson.model.Hudson", "jobs": self.children("")}
            )

        item = self.items[name]

        if item["_class"] == FOLDER:
            return Response(200, {"_class": FOLDER, "jobs": self.children(name)})

        out = self.describe(name)

        if q["tree"].startswith("builds["):
            builds = item["builds"]
            limit = RANGE.search(q["tree"])
            newest = sorted(builds, reverse=True)[
                : int(limit.group(2)) if limit else None
            ]
            out["builds"] = [
                {k: v for k, v in builds[n].items() if k not in HIDDEN} for n in newest
            ]

        return Response(200, out)

    def build_route(self, route: str) -> dict | None:
        name, number = self.locate(route)

        return self.items.get(name or "", {}).get("builds", {}).get(number or -1)

    def run_detail(self, route: str, suffix: str) -> Response:
        build = self.build_route(route)

        if build is None:
            return Response(404, "<html>Not Found</html>")

        if suffix == "/testReport/api/json":
            if build["tests"] is None:
                return Response(404, "<html>Not Found</html>")

            return Response(200, build["tests"])

        if suffix == "/wfapi/pendingInputActions":
            return Response(200, build["inputs"])

        return Response(
            200,
            {
                "_links": {"self": {"href": f"/{route}/wfapi/describe"}},
                "id": str(build["number"]),
                "name": f"#{build['number']}",
                "status": "IN_PROGRESS" if build["building"] else build["result"],
                "startTimeMillis": build["timestamp"],
                "durationMillis": build["duration"],
                "stages": build["stages"],
            },
        )

    def node_log(self, route: str, node: str) -> Response:
        build = self.build_route(route)
        found = (build or {}).get("nodes", {}).get(node)

        if found is None:
            return Response(404, "<html>Not Found</html>")

        return Response(
            200,
            {
                "nodeId": node,
                "nodeStatus": found["status"],
                "length": len(found["log"]),
                "hasMore": False,
                "text": found["log"],
                "consoleUrl": f"/{route}/execution/node/{node}/log",
            },
        )

    def queue_build(self, route: str, q: dict) -> Response:
        job, _, verb = route.rpartition("/")
        name, number = self.locate(job)

        if verb not in ("build", "buildWithParameters") or number is not None:
            return Response(404, "<html>Not Found</html>")

        if name not in self.items:
            return Response(404, "<html>Not Found</html>")

        item = 100 + len(self.waiting)
        self.waiting[item] = {
            "_class": "hudson.model.Queue$WaitingItem",
            "id": item,
            "why": "In the quiet period.",
            "params": dict(q),
        }

        return Response(201, "", {"location": f"{BASE}/queue/item/{item}/"})

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
