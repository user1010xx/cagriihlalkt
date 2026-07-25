from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from bot.config import Config
from bot.scheduler import _is_within_report_window, _seconds_until_next_run
from bot.time_utils import parse_hhmm


def _cfg() -> Config:
    return Config(
        telegram_bot_token="x",
        toniva_api_url="https://example",
        timezone_name="Europe/Istanbul",
        allowed_group_names=set(),
        database_path="x",
        report_interval_minutes=60,
        request_timeout_seconds=60,
        scheduler_start_time=parse_hhmm("11:30"),
        scheduler_end_time=parse_hhmm("19:00"),
        department_report_delay_seconds=90,
    )


def test_report_window():
    cfg = _cfg()
    tz = ZoneInfo("Europe/Istanbul")
    assert _is_within_report_window(cfg, datetime(2026, 7, 18, 12, 0, tzinfo=tz))
    assert not _is_within_report_window(cfg, datetime(2026, 7, 18, 10, 0, tzinfo=tz))
    assert not _is_within_report_window(cfg, datetime(2026, 7, 18, 19, 30, tzinfo=tz))


def test_seconds_until_next_hour():
    cfg = _cfg()
    seconds = _seconds_until_next_run(cfg)
    assert 1.0 <= seconds <= 3600.0


def test_manual_report_mode_always_should_send():
    """suppress_notified=False (/rapor): her zaman gonder."""
    from bot.service import _should_send_report

    assert _should_send_report(
        suppress_notified=False,
        notification_violations=(),
        raw_call_count=10,
        processed_call_count=10,
    ) is True
    assert _should_send_report(
        suppress_notified=False,
        notification_violations=(),
        raw_call_count=0,
        processed_call_count=0,
    ) is True


def test_hourly_mode_always_sends():
    """suppress_notified=True (saatlik): ihlal olsun/olmasın her zaman gönder."""
    from bot.service import _should_send_report

    # Yeni ihlal yok -> yine gonder (ozet + personel adetleri)
    assert _should_send_report(
        suppress_notified=True,
        notification_violations=(),
        raw_call_count=10,
        processed_call_count=10,
    ) is True
    # Yeni ihlal var
    assert _should_send_report(
        suppress_notified=True,
        notification_violations=(("ahmet", "çağrı arası bekleme ihlali"),),
        raw_call_count=10,
        processed_call_count=10,
    ) is True
    # API 0 kayit
    assert _should_send_report(
        suppress_notified=True,
        notification_violations=(),
        raw_call_count=0,
        processed_call_count=0,
    ) is True
