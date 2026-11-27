"""The connection to a Jenkins controller — JSON, consoles from an offset, files as they arrive — and the jobs of a folder through its JSON API."""

from __future__ import annotations

import base64
import copy
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from action_platform.core import destination
from action_platform.core.exception import ActionPlatformError
from action_platform.core.service_access import Http

from apx_jenkins.models import Job

LAST_BUILD = "lastBuild[number,result,building,timestamp,duration]"
JOB = f"name,fullName,_class,color,url,{LAST_BUILD}"
JOBS = f"{JOB},jobs[{JOB}]"
STATUS = {
    "blue": "success",
    "red": "failed",
    "yellow": "unstable",
    "aborted": "aborted",
    "disabled": "disabled",
    "notbuilt": "not built",
    "grey": "not built",
}


class JenkinsError(ActionPlatformError):
    """Jenkins refused or could not be reached; the message never carries the credentials."""


class JenkinsHttp(Http):
    def with_headers(self, extra: dict[str, str]) -> "JenkinsHttp":
        """The same connection with `extra` headers on every call — a crumb on a POST."""
        other = copy.copy(self)
        other.headers = {**self.headers, **extra}

        return other

    def _open(self, path: str, query: Optional[dict[str, Any]] = None):
        req = urllib.request.Request(
            self.url(path, query),
            headers={"user-agent": "action-platform (jenkins)", **self.headers},
        )

        try:
            return destination.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise JenkinsError(
                f"jenkins: GET {urllib.parse.urlsplit(self.url(path)).path} answered {e.code}"
            ) from e
        except destination.DestinationError as e:
            raise JenkinsError(f"jenkins: {e}") from e
        except urllib.error.URLError as e:
            raise JenkinsError(
                f"jenkins: cannot reach {urllib.parse.urlsplit(self.base).netloc}: {e.reason}"
            ) from e

    def progressive(self, path: str, start: int) -> tuple[str, int, bool]:
        """A console from byte `start`: its text, the offset to resume from (`X-Text-Size`) and whether the build still writes (`X-More-Data`)."""
        with self._open(path, {"start": start}) as res:
            charset = res.headers.get_content_charset() or "utf-8"
            text = res.read().decode(charset, errors="replace")
            size = int(res.headers.get("X-Text-Size") or start + len(text.encode()))
            more = (res.headers.get("X-More-Data") or "").lower() == "true"

        return text, size, more

    def stream(self, path: str, chunk: int = 65536) -> Iterator[bytes]:
        """A file's bytes as they arrive."""
        with self._open(path) as res:
            while True:
                block = res.read(chunk)

                if not block:
                    return

                yield block


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
