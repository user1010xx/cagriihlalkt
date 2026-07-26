from __future__ import annotations

from bot.database import Database


def test_department_api_and_personnel(tmp_path):
    db = Database(str(tmp_path / "t.sqlite3"))
    d1 = db.add_department("Satis A", "-1001", "tva_key_shared")
    d2 = db.add_department("Satis B", "-1001", "tva_key_shared")
    d3 = db.add_department("Destek", "-1002", "tva_key_other")

    assert d1.api_key == "tva_key_shared"
    assert d2.api_key == "tva_key_shared"
    assert d3.api_key == "tva_key_other"

    same_chat = db.list_departments(chat_id="-1001")
    assert {d.name for d in same_chat} == {"Satis A", "Satis B"}

    p1 = db.add_personnel(d1.id, "Ali Veli", "101")
    p2 = db.add_personnel(d2.id, "Ayse Yilmaz", "202")
    assert p1 is not None and p2 is not None
    assert len(db.list_personnel(d1.id)) == 1
    assert len(db.list_personnel(d2.id)) == 1

    assert db.update_department_api_key(d3.id, "Bearer tva_new")
    assert db.get_department(d3.id).api_key == "tva_new"


def test_rules_and_leave(tmp_path):
    db = Database(str(tmp_path / "t2.sqlite3"))
    d = db.add_department("Op", "-1", "tva_x")
    assert not db.get_rules(d.id).is_configured

    assert db.update_rules(
        d.id,
        work_start_time="09:00",
        pre_break_leave_time=None,
        break_start_time="13:00",
        break_end_time="14:00",
        post_break_start_time=None,
        work_end_time="18:00",
        max_call_gap_minutes=15,
    )
    rules = db.get_rules(d.id)
    assert rules.is_configured
    assert rules.max_call_gap_minutes == 15
    assert rules.work_start_time.hour == 9

    db.add_personnel(d.id, "Zeynep", None)
    assert db.start_leave(d.id, "Zeynep", "2026-07-18T10:00:00+03:00")
    assert db.has_active_leave(d.id, "Zeynep", "2026-07-18T12:00:00+03:00")
    assert db.end_leave(d.id, "Zeynep", "2026-07-18T13:00:00+03:00")
    assert not db.has_active_leave(d.id, "Zeynep", "2026-07-18T14:00:00+03:00")

    assert db.add_department_weekly_leave(d.id, 6)
    assert db.is_department_weekly_leave(d.id, 6, "2026-07-19")
    db.cancel_department_weekly_leave(d.id, "2026-07-19")
    assert not db.is_department_weekly_leave(d.id, 6, "2026-07-19")


def test_meeting_start_and_end(tmp_path):
    db = Database(str(tmp_path / "meeting.sqlite3"))
    d = db.add_department("Op", "-1", "tva_x")
    db.add_personnel(d.id, "Ahmet", "10")
    assert db.start_meeting(d.id, "Ahmet", "2026-07-18T10:00:00+03:00")
    assert db.has_active_meeting(d.id, "Ahmet", "2026-07-18T10:30:00+03:00")
    rows = db.list_active_meeting_periods(d.id, "2026-07-18T10:30:00+03:00")
    assert len(rows) == 1
    assert rows[0]["personnel_name"] == "Ahmet"
    assert db.end_meeting(d.id, "Ahmet", "2026-07-18T11:00:00+03:00")
    assert not db.has_active_meeting(d.id, "Ahmet", "2026-07-18T11:05:00+03:00")
    day_rows = db.list_meeting_periods(d.id, "2026-07-18")
    assert len(day_rows) == 1
    assert day_rows[0]["end_at"] is not None


def test_upsert_personnel_updates_extension(tmp_path):
    db = Database(str(tmp_path / "t4.sqlite3"))
    d = db.add_department("Op", "-1", "tva")
    p1, action1 = db.upsert_personnel(d.id, "Ali", None)
    assert action1 == "added" and p1 is not None and p1.extension is None
    p2, action2 = db.upsert_personnel(d.id, "Ali", "101")
    assert action2 == "updated" and p2 is not None and p2.extension == "101"
    p3, action3 = db.upsert_personnel(d.id, "Ali", "101")
    assert action3 == "unchanged"


def test_notified_violations(tmp_path):
    db = Database(str(tmp_path / "t3.sqlite3"))
    d = db.add_department("X", "-9", "tva")
    db.mark_notified_violations(d.id, "2026-07-18", [("ali", "mesai başlangıcı ihlali")])
    keys = db.list_notified_violations(d.id, "2026-07-18")
    assert ("ali", "mesai başlangıcı ihlali") in keys
    assert db.cleanup_old_notified_violations("2026-07-18") == 0
    db.mark_notified_violations(d.id, "2026-07-17", [("ali", "x")])
    assert db.cleanup_old_notified_violations("2026-07-18") == 1
