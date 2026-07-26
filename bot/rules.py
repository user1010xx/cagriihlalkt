from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
import re
from zoneinfo import ZoneInfo

from bot.models import DepartmentRules, Personnel
from bot.time_utils import format_time, parse_date, parse_datetime, parse_time


EXTENSION_NAME_FIELDS = (
    "agentName",
    "AgentName",
    "agent",
    "Agent",
    "extensionName",
    "ExtensionName",
    "userName",
    "UserName",
    "displayName",
    "DisplayName",
    "name",
    "Name",
    "dahiliAdi",
    "DahiliAdi",
    "DAHİLİ ADI",
    "DAHILI ADI",
    "CompletedExtensionName",
    "Extension Name",
)
EXTENSION_NUMBER_FIELDS = (
    "extension",
    "Extension",
    "extensionNumber",
    "ExtensionNumber",
    "ext",
    "Ext",
    "agentExtension",
    "AgentExtension",
    "dahili",
    "Dahili",
    "DAHİLİ",
    "DAHILI",
)
TALK_DURATION_FIELDS = (
    "talkDuration",
    "TalkDuration",
    "talkTime",
    "TalkTime",
    "talkDurationSeconds",
    "TalkDurationSeconds",
    "talkTimeSecond",
    "TalkTimeSecond",
    "conversationDuration",
    "ConversationDuration",
    "billableSeconds",
    "KONUŞMA SÜRESİ",
    "KONUSMA SURESI",
)
CALL_DURATION_FIELDS = (
    "duration",
    "Duration",
    "durationSeconds",
    "DurationSeconds",
    "callDuration",
    "CallDuration",
    "callTime",
    "CallTime",
    "CALLTIME",
    "callTimeSecond",
    "CallTimeSecond",
    "totalDuration",
    "TotalDuration",
)
RING_DURATION_FIELDS = (
    "ringDuration",
    "RingDuration",
    "ringTime",
    "RingTime",
    "RINGTIME",
    "ringDurationSeconds",
    "RingTimeSecond",
)
WAIT_DURATION_FIELDS = (
    "waitDuration",
    "WaitDuration",
    "waitTime",
    "WaitTime",
    "WAITTIME",
    "waitDurationSeconds",
    "WaitTimeSecond",
)


@dataclass(frozen=True)
class CallRecord:
    extension_name: str
    extension: str | None
    started_at: datetime
    duration_seconds: int
    event_type: str
    call_id: str | None = None
    talk_duration_seconds: int | None = None

    @property
    def ended_at(self) -> datetime:
        return self.started_at + timedelta(seconds=max(0, self.duration_seconds))


@dataclass
class PersonnelEvaluation:
    name: str
    extension: str | None
    calls: list[CallRecord] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    leave_periods: list[tuple[datetime, datetime | None]] = field(default_factory=list)
    meeting_periods: list[tuple[datetime, datetime | None]] = field(default_factory=list)
    total_call_count: int | None = None
    total_call_duration_seconds: int | None = None
    is_on_leave: bool = False
    is_in_meeting: bool = False


def normalize_calls(raw_calls: list[dict[str, object]], timezone: ZoneInfo) -> list[CallRecord]:
    records: list[CallRecord] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        duration = _call_duration_seconds(item)
        if duration <= 0:
            continue
        talk_duration = _call_talk_duration_seconds(item)
        started_at = _call_started_at(item, timezone)
        if started_at is None:
            continue
        raw_extension_name = _first_value(item, *EXTENSION_NAME_FIELDS, fuzzy=False) or "Bilinmeyen"
        extension_name = _clean_extension_name(raw_extension_name)
        extension = _clean_optional_text(_first_value(item, *EXTENSION_NUMBER_FIELDS, fuzzy=False))
        if extension is None:
            extension = _extension_from_text(raw_extension_name)
        event_type = str(_first_value(item, "eventType", "EventType", "direction", "Direction", "type", "Type") or "1").strip()
        records.append(
            CallRecord(
                extension_name=extension_name,
                extension=extension,
                started_at=started_at,
                duration_seconds=duration,
                event_type=event_type or "1",
                call_id=_clean_optional_text(
                    _first_value(item, "callId", "CallId", "callID", "CallID", "id", "Id", "uuid", "UUID")
                ),
                talk_duration_seconds=talk_duration,
            )
        )
    return sorted(records, key=lambda record: record.started_at)


def evaluate_department(
    calls: list[CallRecord],
    personnel: list[Personnel],
    rules: DepartmentRules,
    report_date: date,
    now: datetime,
    timezone: ZoneInfo,
    leave_periods: dict[str, list[tuple[datetime, datetime | None]]] | None = None,
    meeting_periods: dict[str, list[tuple[datetime, datetime | None]]] | None = None,
) -> list[PersonnelEvaluation]:
    """Personel listesi varsa yalnizca o liste degerlendirilir (paylasilan API ayrimi).

    Izin veya acik toplanti sirasinda ihlal kontrolu yapilmaz. Toplanti/izin araliklari
    cagri arasi bekleme hesabindan dusulur (iptal sonrasi gap kurali uygulanir).
    """
    grouped = _group_calls(calls, personnel)
    evaluations: list[PersonnelEvaluation] = []
    if personnel:
        expected_people = list(personnel)
    else:
        expected_people = [
            Personnel(
                id=0,
                department_id=rules.department_id,
                name=name,
                extension=records[0].extension if records else None,
                is_active=True,
            )
            for name, records in grouped.items()
        ]
    for person in expected_people:
        person_calls = _calls_for_person(person, grouped)
        total_call_count = len(person_calls)
        total_call_duration_seconds = sum(_report_duration_seconds(call) for call in person_calls)
        person_leave_periods = _leave_periods_for_person(person, leave_periods or {})
        person_meeting_periods = _leave_periods_for_person(person, meeting_periods or {})
        is_on_leave = _is_on_leave_at(now, person_leave_periods)
        is_in_meeting = _is_on_leave_at(now, person_meeting_periods)
        if is_on_leave:
            evaluations.append(
                PersonnelEvaluation(
                    name=person.name,
                    extension=person.extension,
                    leave_periods=person_leave_periods,
                    meeting_periods=person_meeting_periods,
                    total_call_count=total_call_count,
                    total_call_duration_seconds=total_call_duration_seconds,
                    is_on_leave=True,
                    is_in_meeting=is_in_meeting,
                )
            )
            continue
        if is_in_meeting:
            evaluations.append(
                PersonnelEvaluation(
                    name=person.name,
                    extension=person.extension,
                    leave_periods=person_leave_periods,
                    meeting_periods=person_meeting_periods,
                    total_call_count=total_call_count,
                    total_call_duration_seconds=total_call_duration_seconds,
                    is_on_leave=False,
                    is_in_meeting=True,
                )
            )
            continue
        exempt_periods = person_leave_periods + person_meeting_periods
        person_calls = _filter_calls_by_leave(person_calls, exempt_periods)
        evaluation = PersonnelEvaluation(
            name=person.name,
            extension=person.extension,
            calls=person_calls,
            leave_periods=person_leave_periods,
            meeting_periods=person_meeting_periods,
            total_call_count=total_call_count,
            total_call_duration_seconds=total_call_duration_seconds,
            is_on_leave=False,
            is_in_meeting=False,
        )
        _check_work_start(evaluation, rules, report_date, now, timezone)
        _check_call_gaps(evaluation, rules, report_date, timezone)
        _check_current_idle_gap(evaluation, rules, report_date, now, timezone)
        _check_pre_break_leave(evaluation, rules, report_date, now, timezone)
        _check_post_break_start(evaluation, rules, report_date, now, timezone)
        _check_work_end(evaluation, rules, report_date, now, timezone)
        evaluations.append(evaluation)
    return sorted(evaluations, key=lambda item: item.name.casefold())


def has_violations(evaluations: list[PersonnelEvaluation]) -> bool:
    return any(evaluation.violations for evaluation in evaluations)


def _report_duration_seconds(call: CallRecord) -> int:
    if call.talk_duration_seconds is not None:
        return max(0, call.talk_duration_seconds)
    return max(0, call.duration_seconds)


def _group_calls(calls: list[CallRecord], personnel: list[Personnel]) -> dict[str, list[CallRecord]]:
    extension_to_name = {
        _normalize_extension(person.extension): person.name
        for person in personnel
        if _normalize_extension(person.extension)
    }
    known_names = {_normalize_person_name(person.name): person.name for person in personnel}
    grouped: dict[str, list[CallRecord]] = {}
    for call in calls:
        call_extension = _normalize_extension(call.extension) or _normalize_extension(
            _extension_from_text(call.extension_name)
        )
        name = extension_to_name.get(call_extension)
        if name is None:
            clean_call_name = _clean_extension_name(call.extension_name)
            normalized_call_name = _normalize_person_name(clean_call_name)
            name = known_names.get(normalized_call_name)
            if name is None:
                name = _best_known_name_match(normalized_call_name, known_names) or clean_call_name
        # Paylasilan API: personel listesi varsa sadece bilinen personel gruplanir
        if personnel and name not in {p.name for p in personnel}:
            if name not in known_names.values():
                continue
        grouped.setdefault(name, []).append(call)
    return grouped


def _calls_for_person(person: Personnel, grouped: dict[str, list[CallRecord]]) -> list[CallRecord]:
    if person.name in grouped:
        return grouped[person.name]
    if person.extension:
        person_extension = _normalize_extension(person.extension)
        for records in grouped.values():
            if records and _normalize_extension(records[0].extension) == person_extension:
                return records
    return []


def _leave_periods_for_person(
    person: Personnel,
    leave_periods: dict[str, list[tuple[datetime, datetime | None]]],
) -> list[tuple[datetime, datetime | None]]:
    return leave_periods.get(person.name.casefold(), [])


def _filter_calls_by_leave(
    calls: list[CallRecord],
    leave_periods: list[tuple[datetime, datetime | None]],
) -> list[CallRecord]:
    if not leave_periods:
        return calls
    filtered: list[CallRecord] = []
    for call in calls:
        if any(_call_overlaps_leave(call, leave_start, leave_end) for leave_start, leave_end in leave_periods):
            continue
        filtered.append(call)
    return filtered


def _call_overlaps_leave(call: CallRecord, leave_start: datetime, leave_end: datetime | None) -> bool:
    effective_leave_end = leave_end or datetime.max.replace(tzinfo=call.started_at.tzinfo)
    return call.started_at < effective_leave_end and call.ended_at > leave_start


def _is_on_leave_at(value: datetime, leave_periods: list[tuple[datetime, datetime | None]]) -> bool:
    return _datetime_in_leave(value, leave_periods)


def _exempt_periods(
    evaluation: PersonnelEvaluation,
) -> list[tuple[datetime, datetime | None]]:
    """Izin + toplanti araliklari (gap / kural muafiyeti)."""
    return list(evaluation.leave_periods) + list(evaluation.meeting_periods)


def _check_work_start(
    evaluation: PersonnelEvaluation,
    rules: DepartmentRules,
    report_date: date,
    now: datetime,
    timezone: ZoneInfo,
) -> None:
    if rules.work_start_time is None:
        return
    work_start = datetime.combine(report_date, rules.work_start_time, tzinfo=timezone)
    if now < work_start:
        return
    if _datetime_in_leave(work_start, _exempt_periods(evaluation)):
        return
    if not evaluation.calls:
        evaluation.violations.append(
            f"Mesai başlangıcı ihlali: {format_time(rules.work_start_time)} sonrası çağrı yok"
        )
        return
    first_call = evaluation.calls[0]
    if _minute_floor(first_call.started_at) > work_start:
        evaluation.violations.append(
            f"Mesai başlangıcı ihlali: ilk çağrı {first_call.started_at.strftime('%H:%M')} "
            f"(limit {format_time(rules.work_start_time)})"
        )


def _check_call_gaps(
    evaluation: PersonnelEvaluation,
    rules: DepartmentRules,
    report_date: date,
    timezone: ZoneInfo,
) -> None:
    if len(evaluation.calls) < 2 or rules.max_call_gap_minutes is None:
        return
    work_start = (
        datetime.combine(report_date, rules.work_start_time, tzinfo=timezone)
        if rules.work_start_time
        else datetime.combine(report_date, time.min, tzinfo=timezone)
    )
    work_end = (
        datetime.combine(report_date, rules.work_end_time, tzinfo=timezone)
        if rules.work_end_time
        else datetime.combine(report_date, time.max, tzinfo=timezone)
    )
    for previous, current in zip(evaluation.calls, evaluation.calls[1:]):
        idle_start = previous.ended_at
        idle_end = min(current.started_at, work_end)
        if previous.started_at < work_start and _minute_floor(current.started_at) <= work_start:
            continue
        if previous.started_at < work_start and idle_start < work_start and _minute_floor(current.started_at) > work_start:
            idle_start = max(previous.started_at, work_start - timedelta(minutes=rules.max_call_gap_minutes))
        if idle_end <= idle_start:
            continue
        idle_seconds = (idle_end - idle_start).total_seconds()
        idle_seconds -= _ignored_gap_seconds(idle_start, idle_end, rules, report_date, timezone, previous)
        idle_seconds -= _leave_overlap_seconds(idle_start, idle_end, _exempt_periods(evaluation))
        idle_minutes = int(idle_seconds // 60)
        if idle_minutes > rules.max_call_gap_minutes:
            evaluation.violations.append(
                f"Çağrı arası bekleme ihlali: {previous.started_at.strftime('%H:%M')} - "
                f"{current.started_at.strftime('%H:%M')} arası {idle_minutes} dk "
                f"(limit {rules.max_call_gap_minutes} dk)"
            )


def _check_current_idle_gap(
    evaluation: PersonnelEvaluation,
    rules: DepartmentRules,
    report_date: date,
    now: datetime,
    timezone: ZoneInfo,
) -> None:
    if not evaluation.calls or rules.max_call_gap_minutes is None:
        return
    work_start = (
        datetime.combine(report_date, rules.work_start_time, tzinfo=timezone)
        if rules.work_start_time
        else datetime.combine(report_date, time.min, tzinfo=timezone)
    )
    work_end = (
        datetime.combine(report_date, rules.work_end_time, tzinfo=timezone)
        if rules.work_end_time
        else datetime.combine(report_date, time.max, tzinfo=timezone)
    )
    idle_end = min(now, work_end)
    if idle_end <= work_start:
        return
    last_call = max(
        (call for call in evaluation.calls if call.started_at <= idle_end),
        default=None,
        key=lambda call: call.started_at,
    )
    if last_call is None:
        return
    idle_start = max(last_call.ended_at, work_start)
    if last_call.started_at < work_start and last_call.ended_at < work_start:
        idle_start = max(last_call.started_at, work_start - timedelta(minutes=rules.max_call_gap_minutes))
    if idle_end <= idle_start:
        return
    idle_seconds = (idle_end - idle_start).total_seconds()
    idle_seconds -= _ignored_gap_seconds(idle_start, idle_end, rules, report_date, timezone, last_call)
    idle_seconds -= _leave_overlap_seconds(idle_start, idle_end, _exempt_periods(evaluation))
    idle_minutes = int(idle_seconds // 60)
    if idle_minutes > rules.max_call_gap_minutes:
        evaluation.violations.append(
            f"Güncel bekleme ihlali: son çağrı {last_call.started_at.strftime('%H:%M')} sonrası "
            f"{idle_minutes} dk bekleme var (limit {rules.max_call_gap_minutes} dk)"
        )


def _check_pre_break_leave(
    evaluation: PersonnelEvaluation,
    rules: DepartmentRules,
    report_date: date,
    now: datetime,
    timezone: ZoneInfo,
) -> None:
    if rules.pre_break_leave_time is None:
        return
    leave_time = datetime.combine(report_date, rules.pre_break_leave_time, tzinfo=timezone)
    if now < leave_time:
        return
    if _datetime_in_leave(leave_time, _exempt_periods(evaluation)):
        return
    latest_allowed_window = (
        datetime.combine(report_date, rules.break_start_time, tzinfo=timezone)
        if rules.break_start_time
        else leave_time
    )
    relevant_calls = [call for call in evaluation.calls if call.started_at <= latest_allowed_window]
    has_call_until_leave_time = any(
        call.started_at >= leave_time or call.ended_at >= leave_time for call in relevant_calls
    )
    if not has_call_until_leave_time:
        last_call_text = relevant_calls[-1].started_at.strftime("%H:%M") if relevant_calls else "yok"
        evaluation.violations.append(
            f"Mola öncesi çağrı bırakma ihlali: {format_time(rules.pre_break_leave_time)} "
            f"saatine kadar çağrı yok (son çağrı: {last_call_text})"
        )


def _check_post_break_start(
    evaluation: PersonnelEvaluation,
    rules: DepartmentRules,
    report_date: date,
    now: datetime,
    timezone: ZoneInfo,
) -> None:
    if rules.post_break_start_time is None:
        return
    start_limit = datetime.combine(report_date, rules.post_break_start_time, tzinfo=timezone)
    if now < start_limit:
        return
    if _datetime_in_leave(start_limit, _exempt_periods(evaluation)):
        return
    break_end = (
        datetime.combine(report_date, rules.break_end_time, tzinfo=timezone)
        if rules.break_end_time
        else start_limit
    )
    calls_after_break = [
        call for call in evaluation.calls if call.started_at >= break_end or call.ended_at >= break_end
    ]
    if not calls_after_break:
        evaluation.violations.append(
            f"Mola sonrası çağrı başlangıç ihlali: {format_time(rules.post_break_start_time)} "
            "saatine kadar çağrı yok"
        )
        return
    first_call = calls_after_break[0]
    if _minute_floor(first_call.started_at) > start_limit:
        evaluation.violations.append(
            f"Mola sonrası çağrı başlangıç ihlali: ilk çağrı {first_call.started_at.strftime('%H:%M')} "
            f"(limit {format_time(rules.post_break_start_time)})"
        )


def _check_work_end(
    evaluation: PersonnelEvaluation,
    rules: DepartmentRules,
    report_date: date,
    now: datetime,
    timezone: ZoneInfo,
) -> None:
    if rules.work_end_time is None:
        return
    work_end = datetime.combine(report_date, rules.work_end_time, tzinfo=timezone)
    if now < work_end:
        return
    if _datetime_in_leave(work_end, _exempt_periods(evaluation)):
        return
    has_call_at_or_after_end = any(
        call.started_at >= work_end or call.ended_at >= work_end for call in evaluation.calls
    )
    if not has_call_at_or_after_end:
        last_call_text = evaluation.calls[-1].started_at.strftime("%H:%M") if evaluation.calls else "yok"
        evaluation.violations.append(
            f"Mesai bitişi ihlali: {format_time(rules.work_end_time)} ve sonrasında çağrı yok "
            f"(son çağrı: {last_call_text})"
        )


def _overlap_seconds(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> float:
    overlap_start = max(start, other_start)
    overlap_end = min(end, other_end)
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds()


def _leave_overlap_seconds(
    start: datetime,
    end: datetime,
    leave_periods: list[tuple[datetime, datetime | None]],
) -> float:
    total = 0.0
    for leave_start, leave_end in leave_periods:
        effective_leave_end = leave_end or end
        total += _overlap_seconds(start, end, leave_start, effective_leave_end)
    return total


def _datetime_in_leave(
    value: datetime,
    leave_periods: list[tuple[datetime, datetime | None]],
) -> bool:
    for leave_start, leave_end in leave_periods:
        effective_leave_end = leave_end or datetime.max.replace(tzinfo=value.tzinfo)
        if leave_start <= value <= effective_leave_end:
            return True
    return False


def _ignored_gap_seconds(
    start: datetime,
    end: datetime,
    rules: DepartmentRules,
    report_date: date,
    timezone: ZoneInfo,
    previous_call: CallRecord,
) -> float:
    ignored_seconds = 0.0
    intervals: list[tuple[datetime, datetime]] = []
    if rules.pre_break_leave_time and rules.break_start_time:
        intervals.append(
            (
                datetime.combine(report_date, rules.pre_break_leave_time, tzinfo=timezone),
                datetime.combine(report_date, rules.break_start_time, tzinfo=timezone),
            )
        )
    if rules.break_start_time and rules.break_end_time:
        intervals.append(
            (
                datetime.combine(report_date, rules.break_start_time, tzinfo=timezone),
                datetime.combine(report_date, rules.break_end_time, tzinfo=timezone),
            )
        )
    if rules.break_end_time and rules.post_break_start_time:
        break_end = datetime.combine(report_date, rules.break_end_time, tzinfo=timezone)
        post_break_start = datetime.combine(report_date, rules.post_break_start_time, tzinfo=timezone)
        if previous_call.started_at < break_end and end > break_end:
            intervals.append((break_end, post_break_start))
    for interval_start, interval_end in intervals:
        ignored_seconds += _overlap_seconds(start, end, interval_start, interval_end)
    return ignored_seconds


def _to_int(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _first_value(item: dict[str, object], *keys: str, fuzzy: bool = True) -> object | None:
    casefolded = {str(key).strip().casefold(): value for key, value in item.items()}
    normalized = {_normalize_key(key): value for key, value in item.items()}
    for key in keys:
        value = item.get(key)
        if value is None:
            value = casefolded.get(key.casefold())
        if value is None:
            value = normalized.get(_normalize_key(key))
        if value is None and fuzzy:
            value = _fuzzy_value(normalized, _normalize_key(key))
        if value not in (None, ""):
            return value
    return None


def _call_duration_seconds(item: dict[str, object]) -> int:
    """Cagri suresi (sn).

    Olumlu politika: CallTime=0 olsa bile RingTime>0 ise kayit gecerli sayilir
    (arama denemesi = aktivite; mesai/gap kontrolune dahil).
    """
    conversation_seconds = _duration_field_seconds(item, CALL_DURATION_FIELDS)[1]
    talk_seconds = _call_talk_duration_seconds(item)
    ring_seconds = _duration_field_seconds(item, RING_DURATION_FIELDS)[1]
    if conversation_seconds > 0:
        return conversation_seconds
    if talk_seconds is not None and talk_seconds > 0:
        return talk_seconds
    return conversation_seconds if conversation_seconds > 0 else ring_seconds


def _call_talk_duration_seconds(item: dict[str, object]) -> int | None:
    has_talk_value, talk_seconds = _duration_field_seconds(item, TALK_DURATION_FIELDS)
    if has_talk_value:
        return talk_seconds
    # Toniva conversations: CallTime = konusma suresi (sn), RingTime/WaitTime ayri alanlar
    has_call_time, call_time_seconds = _duration_field_seconds(
        item, ("CallTime", "callTime", "CALLTIME", "TalkTime", "talkTime")
    )
    if has_call_time:
        return max(0, call_time_seconds)
    has_total_value, total_seconds = _duration_field_seconds(item, CALL_DURATION_FIELDS)
    has_ring_value, ring_seconds = _duration_field_seconds(item, RING_DURATION_FIELDS)
    has_wait_value, wait_seconds = _duration_field_seconds(item, WAIT_DURATION_FIELDS)
    if has_total_value and (has_ring_value or has_wait_value):
        return max(0, total_seconds - ring_seconds - wait_seconds)
    return None


def _duration_field_seconds(item: dict[str, object], fields: tuple[str, ...]) -> tuple[bool, int]:
    value = _first_value(item, *fields, fuzzy=False)
    if value is None:
        return False, 0
    return True, _duration_to_seconds(value)


def _call_started_at(item: dict[str, object], timezone: ZoneInfo) -> datetime | None:
    date_value = _first_value(
        item,
        "date",
        "Date",
        "callDate",
        "CallDate",
        "startDate",
        "StartDate",
        "createDate",
        "CreateDate",
        "createdDate",
        "CreatedDate",
        fuzzy=False,
    )
    time_value = _first_value(
        item,
        "time",
        "Time",
        "startTime",
        "StartTime",
        "callStartTime",
        "CallStartTime",
        "createTime",
        "CreateTime",
        "createdTime",
        "CreatedTime",
        fuzzy=False,
    )
    if date_value is not None and time_value is not None:
        try:
            return parse_datetime(date_value, time_value, timezone)
        except Exception:
            pass
    combined_value = _first_value(
        item,
        "startedAt",
        "StartedAt",
        "startAt",
        "StartAt",
        "dateTime",
        "DateTime",
        "callDateTime",
        "CallDateTime",
        "startDateTime",
        "StartDateTime",
        "createdAt",
        "CreatedAt",
        "timestamp",
        "Timestamp",
        fuzzy=False,
    )
    if combined_value is None:
        return None
    return _parse_combined_datetime(combined_value, timezone)


def _parse_combined_datetime(value: object, timezone: ZoneInfo) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("T", " ").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed is None:
        for separator in (" ", "  "):
            if separator not in text:
                continue
            date_text, time_text = text.split(separator, 1)
            try:
                parsed = datetime.combine(parse_date(date_text), parse_time(time_text))
                break
            except Exception:
                pass
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _duration_to_seconds(value: object) -> int:
    """Saat:dakika:saniye veya saniye sayisi. Ondalik saat icin performance_hours_to_seconds kullanin."""
    text = str(value or "").strip()
    if not text:
        return 0
    if ":" not in text:
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            return 0
        # 12.5 gibi ondalik ve kucuk degerler genelde saat (performans raporu)
        # ama conversations CallTime tamsayi saniyedir (27).
        if "." in text.replace(",", ".") and abs(number) < 1000 and number != int(number):
            return int(round(number * 3600))
        return int(number)
    parts = text.split(":")
    if len(parts) not in (2, 3):
        return 0
    try:
        values = [int(float(part.replace(",", "."))) for part in parts]
    except ValueError:
        return 0
    if len(values) == 2:
        minutes, seconds = values
        return minutes * 60 + seconds
    hours, minutes, seconds = values
    return hours * 3600 + minutes * 60 + seconds


def _clean_extension_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*\(\s*\d+(?:[.,]0+)?\s*\)\s*", " ", text)
    if " -" in text:
        text = text.split(" -", 1)[0].strip()
    text = " ".join(text.split())
    return text or "Bilinmeyen"


def _clean_optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_extension(value: object | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace(",", ".")
    try:
        number = float(normalized)
    except ValueError:
        return "".join(normalized.split())
    if number.is_integer():
        return str(int(number))
    return "".join(normalized.split())


def _extension_from_text(value: object) -> str | None:
    text = str(value or "")
    parenthesized = re.search(r"\(\s*(\d+(?:[.,]0+)?)\s*\)", text)
    if parenthesized:
        return _normalize_extension(parenthesized.group(1))
    text_without_dates = re.sub(r"\d{1,4}[-./]\d{1,2}[-./]\d{1,4}", " ", text)
    standalone = re.search(r"(?<!\d)(\d{3,6}(?:[.,]0+)?)(?!\d)", text_without_dates)
    if standalone:
        return _normalize_extension(standalone.group(1))
    return None


def _normalize_person_name(value: object) -> str:
    return _normalize_key(value)


def _best_known_name_match(normalized_call_name: str, known_names: dict[str, str]) -> str | None:
    if not normalized_call_name or not known_names:
        return None
    for normalized_person_name, person_name in known_names.items():
        if normalized_call_name in normalized_person_name or normalized_person_name in normalized_call_name:
            return person_name
    best_name: str | None = None
    best_score = 0.0
    for normalized_person_name, person_name in known_names.items():
        if len(normalized_person_name) < 4 or len(normalized_call_name) < 4:
            continue
        score = SequenceMatcher(None, normalized_call_name, normalized_person_name).ratio()
        if score >= 0.88 and score > best_score:
            best_name = person_name
            best_score = score
    return best_name


def _normalize_key(value: object) -> str:
    replacements = str.maketrans(
        {
            "ç": "c",
            "Ç": "c",
            "ğ": "g",
            "Ğ": "g",
            "ı": "i",
            "I": "i",
            "İ": "i",
            "ö": "o",
            "Ö": "o",
            "ş": "s",
            "Ş": "s",
            "ü": "u",
            "Ü": "u",
        }
    )
    text = str(value).translate(replacements).casefold()
    return "".join(character for character in text if character.isalnum())


def _fuzzy_value(normalized: dict[str, object], target: str) -> object | None:
    if len(target) < 6:
        return None
    best_value: object | None = None
    best_score = 0.0
    for key, value in normalized.items():
        if not key:
            continue
        if target in key:
            return value
        if len(key) >= 6:
            score = SequenceMatcher(None, key, target).ratio()
            if score >= 0.78 and score > best_score:
                best_value = value
                best_score = score
    return best_value


# service.py icin re-export benzeri yardimcilar
normalize_extension = _normalize_extension
normalize_key = _normalize_key
duration_to_seconds = _duration_to_seconds
