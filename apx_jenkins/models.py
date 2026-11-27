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
