from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.time_utils import parse_hhmm


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    toniva_api_url: str
    timezone_name: str
    allowed_group_names: set[str]
    database_path: str
    report_interval_minutes: int
    request_timeout_seconds: int
    scheduler_start_time: object
    scheduler_end_time: object
    department_report_delay_seconds: int

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


def _parse_group_names(value: str) -> set[str]:
    names: set[str] = set()
    for item in value.split(","):
        item = item.strip().casefold()
        if item:
            names.add(item)
    return names


def load_config() -> Config:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env degeri zorunludur.")

    database_path = os.getenv("DATABASE_PATH", "data/bot.sqlite3").strip()
    parent = Path(database_path).parent
    if str(parent) and str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)

    allowed = _parse_group_names(os.getenv("ALLOWED_GROUP_NAMES", ""))
    if not allowed:
        import logging

        logging.getLogger(__name__).warning(
            "ALLOWED_GROUP_NAMES bos: yalnizca kayitli departman chat'leri komut kullanabilir. "
            "Yeni grup kurulumu icin grup adlarini env'ye yazin."
        )

    return Config(
        telegram_bot_token=token,
        toniva_api_url=os.getenv(
            "TONIVA_API_URL",
            "https://crm.toniva.net/api/public/v1",
        ).strip().rstrip("/"),
        timezone_name=os.getenv("TIMEZONE", "Europe/Istanbul").strip(),
        allowed_group_names=allowed,
        database_path=database_path,
        report_interval_minutes=max(1, int(os.getenv("REPORT_INTERVAL_MINUTES", "60"))),
        request_timeout_seconds=max(1, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))),
        scheduler_start_time=parse_hhmm(os.getenv("SCHEDULER_START_TIME", "11:00")),
        scheduler_end_time=parse_hhmm(os.getenv("SCHEDULER_END_TIME", "19:00")),
        department_report_delay_seconds=max(
            0, int(os.getenv("DEPARTMENT_REPORT_DELAY_SECONDS", "30"))
        ),
    )
