"""The Jenkins CIRunner: runs, stages, logs, tests, artifacts, the queue and the executors of a controller, read through `client.JenkinsHttp`; registered in the `action_platform.ci_runner` group so the platform deploys and runs CI through it.

`job` is the path Jenkins shows in the URL without the `job/` segments: `team/app` or `team/app/main`.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from action_platform.abc.ci_runner import (
    LOG_WINDOW,
    Artifact,
    CIRunner,
    Executor,
    LogChunk,
    PendingInput,
    QueueItem,
    Stage,
    TestFailure,
    TestReport,
)
from action_platform.core.context import Cause, Change, Run, RunRef
from action_platform.core.exception import (
    ActionPlatformError,
    DeployError,
    ProviderError,
)
from action_platform.core.service_access import Response

from apx_jenkins.client import JenkinsHttp, connection, job_path, window

BUILD = (
    "number,result,building,timestamp,duration,url,displayName,"
    "actions[lastBuiltRevision[SHA1,branch[name]],"
    "causes[shortDescription,userId],parameters[name,value]],"
    "changeSets[items[commitId,msg,author[fullName]]],"
    "changeSet[items[commitId,msg,author[fullName]]]"
)
QUEUE = "items[why,inQueueSince,task[name,fullName,url]]"
COMPUTERS = (
    "computer[displayName,executors[idle,currentExecutable[number,url]],"
    "oneOffExecutors[idle,currentExecutable[number,url]]]"
)
QUEUE_ITEM = re.compile(r"/queue/item/(\d+)")
JOB_SEGMENT = re.compile(r"/job/([^/]+)")
RESULTS = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "UNSTABLE": "unstable",
    "ABORTED": "aborted",
    "NOT_BUILT": "aborted",
}
FAILED_CASES = ("FAILED", "REGRESSION")
RUNNING_NODES = ("IN_PROGRESS", "PAUSED_PENDING_INPUT")


def _when(millis: Any) -> Optional[datetime]:
    return (
        datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc) if millis else None
    )


def _job_of(url: str) -> Optional[str]:
    names = JOB_SEGMENT.findall(urllib.parse.urlsplit(url or "").path)

    return "/".join(urllib.parse.unquote(n) for n in names) or None


def _branch(name: Optional[str]) -> Optional[str]:
    if not name:
        return None

    if name.startswith("refs/remotes/"):
        return name.split("/", 3)[-1]

    if name.startswith("refs/heads/"):
        return name.removeprefix("refs/heads/")

    return name.split("/", 1)[1] if "/" in name else name


class JenkinsRunner(CIRunner):
    """
    Args:
        base_url (str): the controller's root.
        token (str): the user's API token.
        username (str): the user the token belongs to.
        http (JenkinsHttp): the connection to use instead of one built from the three.
    """

    name = "jenkins"

    def __init__(
        self,
        base_url: str = "",
        token: Optional[str] = None,
        username: Optional[str] = None,
        http: Optional[JenkinsHttp] = None,
    ) -> None:
        if http is None and not base_url:
            raise ProviderError("jenkins needs a base_url")

        self.http = http or connection(base_url, username or "", token or "")
        self._builds: dict[str, int] = {}

    def _send(self, method: str, path: str, **kwargs: Any) -> Response:
        try:
            return self.http.send(method, path, **kwargs)
        except ActionPlatformError as e:
            raise ProviderError(str(e)) from e

    def _json(self, path: str, what: str, tree: Optional[str] = None) -> Any:
        res = self._send("GET", path, query={"tree": tree} if tree else None)

        if res.status in (401, 403):
            raise ProviderError(
                f"jenkins: the credentials were refused for {what} ({res.status})"
            )

        if res.status >= 400:
            raise ProviderError(f"jenkins: {what} answered {res.status}")

        return res.data

    def _build(self, job: str, number: int) -> str:
        return f"{job_path(job)}/{int(number)}"

    def test(self) -> None:
        self._json("/api/json", "the controller", "mode")

    def runs(self, job: str, limit: int = 50) -> list[Run]:
        data = self._json(
            f"{job_path(job)}/api/json",
            f"job {job!r}",
            f"builds[{BUILD}]{{0,{int(limit)}}}",
        )

        return [self._run(b) for b in (data or {}).get("builds") or []]

    def run(self, job: str, number: int) -> Run:
        data = self._json(
            f"{self._build(job, number)}/api/json",
            f"build {number} of {job!r}",
            BUILD,
        )

        return self._run(data or {})

    @staticmethod
    def _run(build: dict) -> Run:
        sha = branch = None
        params: dict[str, str] = {}
        causes: list[Cause] = []

        for action in build.get("actions") or []:
            revision = action.get("lastBuiltRevision") or {}

            if revision:
                sha = revision.get("SHA1") or sha
                names = revision.get("branch") or []
                branch = (names[0].get("name") if names else None) or branch

            for cause in action.get("causes") or []:
                if cause.get("shortDescription"):
                    causes.append(Cause(cause["shortDescription"], cause.get("userId")))

            for param in action.get("parameters") or []:
                if param.get("name") and param.get("value") is not None:
                    params[str(param["name"])] = str(param["value"])

        sets = build.get("changeSets") or (
            [build["changeSet"]] if build.get("changeSet") else []
        )
        changes = [
            Change(
                str(item.get("commitId") or ""),
                (item.get("author") or {}).get("fullName"),
                item.get("msg") or "",
            )
            for change_set in sets
            for item in change_set.get("items") or []
        ]

        branch = _branch(branch)

        if build.get("building"):
            status = "running"
        else:
            status = RESULTS.get(build.get("result") or "", "unknown")

        return Run(
            number=int(build["number"]),
            status=status,
            url=build.get("url"),
            branch=branch,
            sha=sha,
            trigger=causes[0].description if causes else None,
            started_at=_when(build.get("timestamp")),
            duration_ms=build.get("duration") or None,
            name=build.get("displayName"),
            params=params,
            causes=causes,
            changes=changes,
        )

    def start(self, job: str, ref: str, params: Optional[dict] = None) -> RunRef:
        """Queue a build: `REF` set to `ref` when there is one, plus `params`. The run's id is its queue item, which `run_of` turns into the build."""
        query = {**({"REF": ref} if ref else {}), **(params or {})}
        http = self.http

        try:
            crumb = self._json("/crumbIssuer/api/json", "the crumb issuer") or {}
            http = http.with_headers(
                {
                    crumb.get("crumbRequestField", "Jenkins-Crumb"): crumb.get(
                        "crumb", ""
                    )
                }
            )
        except ProviderError:
            pass

        path = job_path(job) + ("/buildWithParameters" if query else "/build")

        try:
            res = http.send("POST", path, query=query or None)
        except ActionPlatformError as e:
            raise ProviderError(str(e)) from e

        if res.status not in (200, 201, 202):
            raise ProviderError(f"jenkins: starting {job!r} answered {res.status}")

        item = QUEUE_ITEM.search(res.headers.get("location") or "")

        return RunRef(
            id=f"queue:{item.group(1)}" if item else f"{job}@{ref}",
            url=self.http.url(job_path(job)),
        )

    def run_of(self, job: str, ref: RunRef) -> Optional[Run]:
        """The build a queue item became; None while it waits. A cancelled item is an error."""
        if not ref.id.startswith("queue:"):
            raise NotImplementedError(f"{job} was started without a queue item")

        number = self._builds.get(ref.id)

        if number is None:
            item = (
                self._json(
                    f"/queue/item/{ref.id[6:]}/api/json",
                    f"queue item {ref.id[6:]}",
                    "cancelled,executable[number]",
                )
                or {}
            )

            if item.get("cancelled"):
                raise DeployError(f"the build of {job} was cancelled in the queue")

            number = (item.get("executable") or {}).get("number")

            if number is None:
                return None

            self._builds[ref.id] = int(number)

        return self.run(job, int(number))

    def stages(self, job: str, number: int) -> list[Stage]:
        """The pipeline's stages from `wfapi/describe`, in order, parallel stages among them."""
        data = self._json(
            f"{self._build(job, number)}/wfapi/describe",
            f"stages of build {number} of {job!r}",
        )

        return [
            Stage(
                id=str(s.get("id")),
                name=str(s["name"]),
                status=str(s.get("status")),
                started_at=_when(s.get("startTimeMillis")),
                duration_ms=s.get("durationMillis"),
            )
            for s in (data or {}).get("stages") or []
            if s.get("name")
        ]

    def log(
        self,
        job: str,
        number: int,
        start: int = 0,
        node: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> LogChunk:
        """At most `limit` bytes (`LOG_WINDOW` when None) of the console from byte `start` through `logText/progressiveText`, read no further than the window; or of one stage or step through `execution/node/{id}/wfapi/log`, which Jenkins already bounds."""
        base = self._build(job, number)
        limit = max(1, int(limit or LOG_WINDOW))
        start = max(0, int(start))

        if node:
            data = (
                self._json(
                    f"{base}/execution/node/{urllib.parse.quote(str(node), safe='')}/wfapi/log",
                    f"node {node} of build {number} of {job!r}",
                )
                or {}
            )
            raw = str(data.get("text") or "").encode()
            text, end, more = window(
                raw[start : start + limit + 1],
                start,
                len(raw),
                data.get("nodeStatus") in RUNNING_NODES,
                limit,
            )

            return LogChunk(text=text, next_start=end, more=more)

        try:
            text, end, more = self.http.progressive(
                f"{base}/logText/progressiveText", start, limit
            )
        except ActionPlatformError as e:
            raise ProviderError(str(e)) from e

        return LogChunk(text=text, next_start=end, more=more)

    def tests(self, job: str, number: int) -> Optional[TestReport]:
        res = self._send("GET", f"{self._build(job, number)}/testReport/api/json")

        if res.status == 404:
            return None

        if res.status >= 400 or not isinstance(res.data, dict):
            raise ProviderError(
                f"jenkins: the test report of build {number} of {job!r} answered {res.status}"
            )

        data = res.data
        failures = [
            TestFailure(
                name=str(case.get("name") or ""),
                class_name=str(case.get("className") or ""),
                message=str(case.get("errorDetails") or ""),
                stack=str(case.get("errorStackTrace") or ""),
            )
            for suite in data.get("suites") or []
            for case in suite.get("cases") or []
            if case.get("status") in FAILED_CASES
        ]
        failed = int(data.get("failCount") or 0)
        skipped = int(data.get("skipCount") or 0)

        return TestReport(
            total=failed + skipped + int(data.get("passCount") or 0),
            failed=failed,
            skipped=skipped,
            failures=failures,
        )

    def artifacts(self, job: str, number: int) -> list[Artifact]:
        data = self._json(
            f"{self._build(job, number)}/api/json",
            f"artifacts of build {number} of {job!r}",
            "artifacts[relativePath]",
        )

        return [
            Artifact(path=str(a["relativePath"]))
            for a in (data or {}).get("artifacts") or []
            if a.get("relativePath")
        ]

    def artifact(self, job: str, number: int, path: str) -> Iterator[bytes]:
        parts = [p for p in path.split("/") if p]

        if not parts or any(p in (".", "..") for p in parts):
            raise ProviderError(f"jenkins: {path!r} is not an artifact path")

        quoted = "/".join(urllib.parse.quote(p, safe="") for p in parts)

        try:
            yield from self.http.stream(f"{self._build(job, number)}/artifact/{quoted}")
        except ActionPlatformError as e:
            raise ProviderError(str(e)) from e

    def queue(self) -> list[QueueItem]:
        data = self._json("/queue/api/json", "the queue", QUEUE)

        return [
            QueueItem(
                job=(i.get("task") or {}).get("fullName")
                or (i.get("task") or {}).get("name")
                or "",
                since=_when(i.get("inQueueSince")),
                why=i.get("why"),
            )
            for i in (data or {}).get("items") or []
        ]

    def executors(self) -> list[Executor]:
        data = self._json("/computer/api/json", "the executors", COMPUTERS)
        found: list[Executor] = []

        for computer in (data or {}).get("computer") or []:
            node = str(computer.get("displayName") or "")

            for slot in [
                *(computer.get("executors") or []),
                *(computer.get("oneOffExecutors") or []),
            ]:
                running = slot.get("currentExecutable") or {}
                found.append(
                    Executor(
                        node=node,
                        busy=not slot.get("idle", True),
                        job=_job_of(running.get("url") or "") if running else None,
                        number=running.get("number"),
                    )
                )

        return found

    def pending_inputs(self, job: str, number: int) -> list[PendingInput]:
        data = self._json(
            f"{self._build(job, number)}/wfapi/pendingInputActions",
            f"pending inputs of build {number} of {job!r}",
        )

        return [
            PendingInput(id=str(i.get("id")), message=str(i.get("message") or ""))
            for i in data or []
        ]
