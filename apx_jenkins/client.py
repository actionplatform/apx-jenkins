"""Reads on a Jenkins controller through its JSON API: jobs of a folder, one build with the tail of its console, the queue."""

from __future__ import annotations

import base64
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from action_platform.core.exception import ActionPlatformError
from action_platform.core.service_access import Http

from apx_jenkins.models import Build, Job, QueueItem

LAST = (
    "number,result,building,timestamp,duration,url,fullDisplayName,"
    "actions[causes[shortDescription],lastBuiltRevision[SHA1,branch[name]]]"
)
LAST_BUILD = "lastBuild[number,result,building,timestamp,duration]"
JOB = f"name,fullName,_class,color,url,{LAST_BUILD}"
JOBS = f"{JOB},jobs[{JOB}]"
QUEUE = "items[id,why,inQueueSince,blocked,stuck,buildable,task[name,fullName,url]]"
STATUS = {
    "blue": "success",
    "red": "failed",
    "yellow": "unstable",
    "aborted": "aborted",
    "disabled": "disabled",
    "notbuilt": "not built",
    "grey": "not built",
}
MAX_LINES = 2000


class JenkinsError(ActionPlatformError):
    """Jenkins refused or could not be reached; the message never carries the credentials."""


class JenkinsHttp(Http):
    def lines(self, path: str, limit: int) -> tuple[list[str], int]:
        """The last `limit` lines of a text answer and how many it had."""
        req = urllib.request.Request(
            self.url(path),
            headers={"user-agent": "action-platform (jenkins)", **self.headers},
        )
        tail: deque[str] = deque(maxlen=limit)
        total = 0

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                for raw in res:
                    total += 1
                    tail.append(raw.decode(errors="replace").rstrip("\r\n"))
        except urllib.error.HTTPError as e:
            raise JenkinsError(
                f"jenkins: GET {urllib.parse.urlsplit(self.url(path)).path} answered {e.code}"
            ) from e
        except urllib.error.URLError as e:
            raise JenkinsError(
                f"jenkins: cannot reach {urllib.parse.urlsplit(self.base).netloc}: {e.reason}"
            ) from e

        return list(tail), total


def connection(url: str, user: str, token: str) -> JenkinsHttp:
    basic = base64.b64encode(f"{user}:{token}".encode()).decode()

    return JenkinsHttp("jenkins", url, {"authorization": f"Basic {basic}"})


def job_path(job: str) -> str:
    parts = [p for p in job.strip("/").split("/") if p]

    if any(p in (".", "..") for p in parts):
        raise JenkinsError(f"jenkins: {job!r} is not a job path")

    return "".join(f"/job/{urllib.parse.quote(p, safe='')}" for p in parts)


def moment(millis: Any) -> Optional[str]:
    if not millis:
        return None

    return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).isoformat()


def seconds(millis: Any) -> Optional[float]:
    return None if millis is None else round(int(millis) / 1000, 3)


class Jenkins:
    def __init__(self, http: JenkinsHttp) -> None:
        self.http = http

    def _get(self, path: str, tree: str, what: str) -> dict[str, Any]:
        res = self.http.send("GET", f"{path}/api/json", query={"tree": tree})

        if res.status == 404:
            raise JenkinsError(f"jenkins: {what} not found")

        if res.status in (401, 403):
            raise JenkinsError(
                f"jenkins: the credentials were refused for {what} ({res.status})"
            )

        if res.status >= 400 or not isinstance(res.data, dict):
            raise JenkinsError(f"jenkins: {what} answered {res.status}")

        return res.data

    def jobs(self, folder: str = "") -> list[Job]:
        data = self._get(
            job_path(folder), JOBS, f"folder {folder!r}" if folder else "root"
        )
        items = data["jobs"] if "jobs" in data else [data]

        return [_job(i) for i in items]

    def build(self, job: str, number: int = 0, lines: int = 100) -> Build:
        if not job.strip("/"):
            raise JenkinsError("jenkins: name the job, as folder/job")

        path = job_path(job)
        ref = str(number) if number > 0 else "lastBuild"
        data = self._get(f"{path}/{ref}", LAST, f"build {ref} of {job!r}")
        n = int(data["number"])
        wanted = max(0, min(lines, MAX_LINES))
        console, total = (
            self.http.lines(f"{path}/{n}/consoleText", wanted) if wanted else ([], 0)
        )
        causes: list[str] = []
        revision = branch = None

        for action in data.get("actions") or []:
            causes += [
                c["shortDescription"]
                for c in action.get("causes") or []
                if c.get("shortDescription")
            ]
            found = action.get("lastBuiltRevision")

            if found and not revision:
                revision = found.get("SHA1")
                names = found.get("branch") or []
                branch = names[0].get("name") if names else None

        building = bool(data.get("building"))

        return Build(
            job=job.strip("/"),
            number=n,
            name=data.get("fullDisplayName") or f"#{n}",
            result="BUILDING" if building else data.get("result") or "UNKNOWN",
            building=building,
            started_at=moment(data.get("timestamp")),
            duration_s=None if building else seconds(data.get("duration")),
            causes=causes,
            branch=branch,
            revision=revision,
            url=data.get("url") or "",
            console=console,
            console_lines=total,
            console_truncated=total > len(console),
        )

    def queue(self) -> list[QueueItem]:
        data = self._get("/queue", QUEUE, "the queue")

        return [
            QueueItem(
                id=int(i["id"]),
                job=(i.get("task") or {}).get("fullName")
                or (i.get("task") or {}).get("name")
                or "",
                why=i.get("why"),
                since=moment(i.get("inQueueSince")),
                blocked=bool(i.get("blocked")),
                stuck=bool(i.get("stuck")),
                buildable=bool(i.get("buildable")),
                url=(i.get("task") or {}).get("url"),
            )
            for i in data.get("items") or []
        ]


def _job(item: dict[str, Any]) -> Job:
    kind = str(item.get("_class", "")).rsplit(".", 1)[-1]
    color = item.get("color")
    last = item.get("lastBuild") or {}
    building = bool(last.get("building"))
    status = None

    if color:
        status = "building" if str(color).endswith("_anime") else None
        status = status or STATUS.get(str(color), str(color))

    return Job(
        name=item.get("fullName") or item.get("name") or "",
        kind=kind,
        folder="jobs" in item or kind.endswith("Folder"),
        status=status,
        last_number=last.get("number"),
        last_result="BUILDING" if building else last.get("result"),
        last_at=moment(last.get("timestamp")),
        last_duration_s=None if building else seconds(last.get("duration")),
        url=item.get("url") or "",
    )
