from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from bot.database import Database
from bot.service import clear_api_call_cache, generate_department_report_payload


TZ = ZoneInfo("Europe/Istanbul")


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def fetch_conversations(self, api_key, report_date, page_size=None):
        self.calls += 1
        return list(self.rows), {"total_count": len(self.rows)}

    async def fetch_performance(self, api_key, report_date):
        return [], {}


@pytest.mark.asyncio
async def test_shared_api_cache_and_personnel_split(tmp_path):
    clear_api_call_cache()
    db = Database(str(tmp_path / "s.sqlite3"))
    a = db.add_department("DeptA", "-100", "tva_shared")
    b = db.add_department("DeptB", "-100", "tva_shared")
    for dept in (a, b):
        db.update_rules(
            dept.id,
            work_start_time="09:00",
            pre_break_leave_time=None,
            break_start_time=None,
            break_end_time=None,
            post_break_start_time=None,
            work_end_time="18:00",
            max_call_gap_minutes=60,
        )
    db.add_personnel(a.id, "Ali", "1")
    db.add_personnel(b.id, "Ayse", "2")

    rows = [
        {"agentName": "Ali", "extension": "1", "startedAt": "2026-07-18T09:05:00", "duration": 90},
        {"agentName": "Ayse", "extension": "2", "startedAt": "2026-07-18T09:05:00", "duration": 90},
    ]
    client = FakeClient(rows)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)

    report_a = await generate_department_report_payload(
        db, client, a.id, date(2026, 7, 18), now, use_cache=True
    )
    report_b = await generate_department_report_payload(
        db, client, b.id, date(2026, 7, 18), now, use_cache=True
    )

    assert client.calls == 1  # shared API cache
    assert "Ali" in report_a.message
    assert "Ayse" not in report_a.message.split("İhlaller")[0] if "İhlaller" in report_a.message else True
    assert "Ayse" in report_b.message
    assert report_a.chat_id == "-100"
    assert report_b.chat_id == "-100"


def test_performance_duration_hours_to_seconds():
    from bot.service import _performance_duration_to_seconds

    assert _performance_duration_to_seconds(0.48) == 1728  # 00:28:48
    assert _performance_duration_to_seconds("0.36") == 1296
    assert _performance_duration_to_seconds("01:02:03") == 3723
    assert _performance_duration_to_seconds(0) == 0


def test_extra_message_when_incomplete_meta():
    from bot.service import _build_extra_messages

    # Ciddi eksik (~4000 kayit)
    msgs = _build_extra_messages({"total_count": 9000}, 5000)
    assert len(msgs) == 1
    assert "9000" in msgs[0]
    # Tam
    assert _build_extra_messages({"total_count": 100}, 100) == ()
    # Kucuk canli fark (4826/4833) -> uyarma
    assert _build_extra_messages({"total_count": 4833}, 4826) == ()
    assert _build_extra_messages({"incomplete": True, "total_count": 4833}, 4826) == ()


@pytest.mark.asyncio
async def test_missing_api_key(tmp_path):
    db = Database(str(tmp_path / "s2.sqlite3"))
    d = db.add_department("NoKey", "-1", None)
    db.update_rules(
        d.id,
        work_start_time="09:00",
        pre_break_leave_time=None,
        break_start_time=None,
        break_end_time=None,
        post_break_start_time=None,
        work_end_time=None,
        max_call_gap_minutes=None,
    )
    client = FakeClient([])
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)
    with pytest.raises(ValueError, match="API"):
        await generate_department_report_payload(db, client, d.id, date(2026, 7, 18), now)


@pytest.mark.asyncio
async def test_weekly_leave_skips_control_and_does_not_send(tmp_path):
    """Haftalık izin günü: API çağrısı yok, mesaj yok, should_send=False."""
    clear_api_call_cache()
    db = Database(str(tmp_path / "weekly.sqlite3"))
    d = db.add_department("Dis As Ekip1", "-100", "tva_key")
    db.update_rules(
        d.id,
        work_start_time="09:00",
        pre_break_leave_time=None,
        break_start_time=None,
        break_end_time=None,
        post_break_start_time=None,
        work_end_time="18:00",
        max_call_gap_minutes=60,
    )
    # 2026-07-18 Cumartesi (weekday=5)
    db.add_department_weekly_leave(d.id, 5)
    client = FakeClient(
        [{"agentName": "Ali", "extension": "1", "startedAt": "2026-07-18T09:05:00", "duration": 90}]
    )
    now = datetime(2026, 7, 18, 14, 0, tzinfo=TZ)

    report = await generate_department_report_payload(
        db, client, d.id, date(2026, 7, 18), now
    )

    assert report.should_send is False
    assert report.message == ""
    assert report.extra_messages == ()
    assert client.calls == 0  # Toniva'ya gidilmez
    assert "izin" not in report.message.casefold()  # gruba izin uyarısı yok


@pytest.mark.asyncio
async def test_hourly_suppresses_already_notified_violation(tmp_path):
    """Saatlik (suppress_notified=True): once bildirilen ihlal tekrarlanmaz."""
    clear_api_call_cache()
    db = Database(str(tmp_path / "notif.sqlite3"))
    d = db.add_department("Dept", "-100", "tva_key")
    db.update_rules(
        d.id,
        work_start_time="09:00",
        pre_break_leave_time=None,
        break_start_time=None,
        break_end_time=None,
        post_break_start_time=None,
        work_end_time="18:00",
        max_call_gap_minutes=30,
    )
    db.add_personnel(d.id, "Ahmet", "10")
    # 09:00 ve 12:30 arasinda uzun bosluk -> cagri arasi ihlal
    rows = [
        {
            "agentName": "Ahmet",
            "extension": "10",
            "startedAt": "2026-07-18T09:00:00",
            "duration": 60,
        },
        {
            "agentName": "Ahmet",
            "extension": "10",
            "startedAt": "2026-07-18T12:30:00",
            "duration": 60,
        },
    ]
    client = FakeClient(rows)
    now = datetime(2026, 7, 18, 13, 0, tzinfo=TZ)

    first = await generate_department_report_payload(
        db, client, d.id, date(2026, 7, 18), now, suppress_notified=True
    )
    assert first.should_send is True
    assert first.notification_violations
    assert "Ahmet" in first.message
    assert "ihlal" in first.message.casefold()

    db.mark_notified_violations(
        d.id, date(2026, 7, 18).isoformat(), first.notification_violations
    )

    # Ayni kontrol aninda ikinci saatlik: once bildirilen tekrarlanmaz, mesaj yok
    second = await generate_department_report_payload(
        db, client, d.id, date(2026, 7, 18), now, suppress_notified=True
    )
    assert second.should_send is False
    assert second.notification_violations == ()

    # /rapor: tum ihlaller yine gorunur
    full = await generate_department_report_payload(
        db, client, d.id, date(2026, 7, 18), now, suppress_notified=False
    )
    assert full.should_send is True
    assert "Ahmet" in full.message
    assert "ihlal" in full.message.casefold()


@pytest.mark.asyncio
async def test_weekly_leave_only_affects_that_department(tmp_path):
    """Aynı gruptaki iki departmandan yalnızca izinli olan atlanır."""
    clear_api_call_cache()
    db = Database(str(tmp_path / "weekly2.sqlite3"))
    leave_dept = db.add_department("Dis As Ekip1", "-100", "tva_a")
    active_dept = db.add_department("Dis As Ekip2", "-100", "tva_b")
    for dept in (leave_dept, active_dept):
        db.update_rules(
            dept.id,
            work_start_time="09:00",
            pre_break_leave_time=None,
            break_start_time=None,
            break_end_time=None,
            post_break_start_time=None,
            work_end_time="18:00",
            max_call_gap_minutes=60,
        )
        db.add_personnel(dept.id, "Ali", "1")
    # Cumartesi yalnızca Ekip1 izinli
    db.add_department_weekly_leave(leave_dept.id, 5)
    client = FakeClient(
        [{"agentName": "Ali", "extension": "1", "startedAt": "2026-07-18T09:05:00", "duration": 90}]
    )
    now = datetime(2026, 7, 18, 14, 0, tzinfo=TZ)

    leave_report = await generate_department_report_payload(
        db, client, leave_dept.id, date(2026, 7, 18), now
    )
    active_report = await generate_department_report_payload(
        db, client, active_dept.id, date(2026, 7, 18), now
    )

    assert leave_report.should_send is False
    assert leave_report.message == ""
    assert active_report.should_send is True
    assert "Dis As Ekip2" in active_report.message
    assert client.calls == 1  # yalnızca aktif departman
