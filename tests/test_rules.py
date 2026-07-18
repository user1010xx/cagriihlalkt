from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from bot.models import DepartmentRules, Personnel
from bot.rules import evaluate_department, normalize_calls


TZ = ZoneInfo("Europe/Istanbul")
DAY = date(2026, 7, 18)


def _rules(**kwargs) -> DepartmentRules:
    base = dict(
        department_id=1,
        work_start_time=time(9, 0),
        pre_break_leave_time=None,
        break_start_time=None,
        break_end_time=None,
        post_break_start_time=None,
        work_end_time=time(18, 0),
        max_call_gap_minutes=15,
        is_configured=True,
    )
    base.update(kwargs)
    return DepartmentRules(**base)


def _person(name: str, ext: str | None = None) -> Personnel:
    return Personnel(id=1, department_id=1, name=name, extension=ext, is_active=True)


def test_normalize_toniva_like_rows():
    raw = [
        {
            "agentName": "Ali Veli (101)",
            "extension": "101",
            "startedAt": "2026-07-18T09:05:00",
            "talkDuration": 120,
            "callId": "abc",
        },
        {
            "agentName": "Skip Zero",
            "startedAt": "2026-07-18T10:00:00",
            "duration": 0,
        },
    ]
    calls = normalize_calls(raw, TZ)
    assert len(calls) == 1
    assert calls[0].extension_name == "Ali Veli"
    assert calls[0].extension == "101"
    assert calls[0].duration_seconds == 120


def test_normalize_toniva_real_field_names():
    """Toniva conversations: CreateDate/CreateTime + CallTime saniye."""
    raw = [
        {
            "Direction": "OUT",
            "ExtensionName": "umit",
            "ExtensionNumber": "639",
            "CreateDate": "2026-07-18",
            "CreateTime": "14:51:35",
            "RingTime": 6,
            "WaitTime": 0,
            "CallTime": 27,
            "CallID": "x",
        }
    ]
    calls = normalize_calls(raw, TZ)
    assert len(calls) == 1
    assert calls[0].extension_name == "umit"
    assert calls[0].extension == "639"
    assert calls[0].duration_seconds == 27
    assert calls[0].talk_duration_seconds == 27
    assert calls[0].started_at.hour == 14
    assert calls[0].started_at.minute == 51


def test_shared_api_filters_by_personnel():
    """Ayni API verisi; personel listesi sadece kendi ekibini degerlendirir."""
    raw = [
        {"agentName": "Ali", "extension": "1", "startedAt": "2026-07-18T09:30:00", "duration": 60},
        {"agentName": "Ayse", "extension": "2", "startedAt": "2026-07-18T09:30:00", "duration": 60},
    ]
    calls = normalize_calls(raw, TZ)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)
    rules = _rules()

    eval_a = evaluate_department(calls, [_person("Ali", "1")], rules, DAY, now, TZ)
    eval_b = evaluate_department(calls, [_person("Ayse", "2")], rules, DAY, now, TZ)

    assert len(eval_a) == 1 and eval_a[0].name == "Ali"
    assert len(eval_b) == 1 and eval_b[0].name == "Ayse"
    assert len(eval_a[0].calls) == 1
    assert len(eval_b[0].calls) == 1


def test_work_start_violation_no_calls():
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)
    evaluations = evaluate_department([], [_person("Ali")], _rules(), DAY, now, TZ)
    assert any("Mesai başlangıcı" in v for v in evaluations[0].violations)


def test_late_first_call():
    raw = [{"agentName": "Ali", "startedAt": "2026-07-18T09:20:00", "duration": 30}]
    calls = normalize_calls(raw, TZ)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)
    evaluations = evaluate_department(calls, [_person("Ali")], _rules(), DAY, now, TZ)
    assert any("ilk çağrı 09:20" in v for v in evaluations[0].violations)


def test_call_gap_violation():
    raw = [
        {"agentName": "Ali", "startedAt": "2026-07-18T09:00:00", "duration": 60},
        {"agentName": "Ali", "startedAt": "2026-07-18T10:00:00", "duration": 60},
    ]
    calls = normalize_calls(raw, TZ)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)
    evaluations = evaluate_department(calls, [_person("Ali")], _rules(max_call_gap_minutes=15), DAY, now, TZ)
    assert any("Çağrı arası bekleme" in v for v in evaluations[0].violations)


def test_ring_only_call_counts_as_activity():
    """Olumlu politika: sadece RingTime olan arama da gecerli kayit."""
    raw = [
        {
            "ExtensionName": "adem",
            "ExtensionNumber": "583",
            "CreateDate": "2026-07-18",
            "CreateTime": "09:05:00",
            "CallTime": 0,
            "RingTime": 20,
        }
    ]
    calls = normalize_calls(raw, TZ)
    assert len(calls) == 1
    assert calls[0].duration_seconds == 20


def test_leave_skips_person():
    raw = [{"agentName": "Ali", "startedAt": "2026-07-18T09:30:00", "duration": 60}]
    calls = normalize_calls(raw, TZ)
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)
    leave = {
        "ali": [(datetime(2026, 7, 18, 8, 0, tzinfo=TZ), None)],
    }
    evaluations = evaluate_department(calls, [_person("Ali")], _rules(), DAY, now, TZ, leave_periods=leave)
    assert evaluations[0].is_on_leave
    assert evaluations[0].violations == []
