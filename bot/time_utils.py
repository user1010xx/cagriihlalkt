from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


def parse_hhmm(value: str) -> time:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Saat bos olamaz.")
    parts = text.replace(".", ":").split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Gecersiz saat formati: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"Gecersiz saat degeri: {value}")
    return time(hour=hour, minute=minute, second=second)


def format_time(value: time) -> str:
    return value.strftime("%H:%M")


def parse_date(value: object) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Tarih bos olamaz.")
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Gecersiz tarih: {value}")


def parse_time(value: object) -> time:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Saat bos olamaz.")
    if "T" in text:
        text = text.split("T", 1)[1]
    text = text.replace("Z", "").split("+")[0].split(".")[0]
    return parse_hhmm(text[:8] if len(text) >= 5 else text)


def parse_datetime(date_value: object, time_value: object, timezone: ZoneInfo) -> datetime:
    return datetime.combine(parse_date(date_value), parse_time(time_value), tzinfo=timezone)
