from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Department:
    id: int
    name: str
    api_key: str | None
    telegram_chat_id: str
    is_active: bool


@dataclass(frozen=True)
class DepartmentRules:
    department_id: int
    work_start_time: time | None
    pre_break_leave_time: time | None
    break_start_time: time | None
    break_end_time: time | None
    post_break_start_time: time | None
    work_end_time: time | None
    max_call_gap_minutes: int | None
    is_configured: bool = True


@dataclass(frozen=True)
class Personnel:
    id: int
    department_id: int
    name: str
    extension: str | None
    is_active: bool


@dataclass(frozen=True)
class DepartmentResponsible:
    id: int
    department_id: int
    username: str
