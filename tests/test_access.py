from __future__ import annotations

from types import SimpleNamespace

from bot.access import group_name_allowed, is_private
from bot.config import Config
from bot.time_utils import parse_hhmm


def _config(names: str) -> Config:
    return Config(
        telegram_bot_token="x",
        toniva_api_url="https://crm.toniva.net/api/public/v1",
        timezone_name="Europe/Istanbul",
        allowed_group_names={n.strip().casefold() for n in names.split(",") if n.strip()},
        database_path="data/x.sqlite3",
        report_interval_minutes=60,
        request_timeout_seconds=60,
        scheduler_start_time=parse_hhmm("11:30"),
        scheduler_end_time=parse_hhmm("19:00"),
        department_report_delay_seconds=90,
    )


def test_group_name_allowed():
    cfg = _config("Satis Grubu, Destek")
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="supergroup", title="Satis Grubu"))
    assert group_name_allowed(update, cfg) is True
    update.effective_chat.title = "Baska"
    assert group_name_allowed(update, cfg) is False


def test_empty_allowed_names_not_open_to_all():
    cfg = _config("")
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="supergroup", title="Herhangi Grup"))
    assert group_name_allowed(update, cfg) is False


def test_private_detection():
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="private"))
    assert is_private(update) is True
    update.effective_chat.type = "group"
    assert is_private(update) is False
