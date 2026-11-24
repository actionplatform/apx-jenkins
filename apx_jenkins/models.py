"""What the tools and commands answer."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Job(BaseModel):
    name: str
    kind: str
    folder: bool
    status: Optional[str] = None
    last_number: Optional[int] = None
    last_result: Optional[str] = None
    last_at: Optional[str] = None
    last_duration_s: Optional[float] = None
    url: str


class Build(BaseModel):
    job: str
    number: int
    name: str
    result: str
    building: bool
    started_at: Optional[str] = None
    duration_s: Optional[float] = None
    causes: list[str]
    branch: Optional[str] = None
    revision: Optional[str] = None
    url: str
    console: list[str]
    console_lines: int
    console_truncated: bool


class QueueItem(BaseModel):
    id: int
    job: str
    why: Optional[str] = None
    since: Optional[str] = None
    blocked: bool
    stuck: bool
    buildable: bool
    url: Optional[str] = None
