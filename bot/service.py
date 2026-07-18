from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from datetime import date, datetime
import logging

from bot.database import Database
from bot.models import Personnel
from bot.reporting import build_department_report
from bot.rules import (
    PersonnelEvaluation,
    duration_to_seconds,
    evaluate_department,
    normalize_calls,
    normalize_extension,
    normalize_key,
)
from bot.toniva_client import TonivaClient
from bot.violation_keys import violation_key


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DepartmentReport:
    chat_id: str
    message: str
    notification_violations: tuple[tuple[str, str], ...]
    should_send: bool
    department_id: int
    department_name: str
    extra_messages: tuple[str, ...] = ()


# Ayni API key ile ayni gun cekilen raporlar icin bellek ici onbellek (scheduler dongusu)
_api_call_cache: dict[tuple[str, str], tuple[list[dict], dict]] = {}


def clear_api_call_cache() -> None:
    _api_call_cache.clear()


async def generate_department_report_payload(
    database: Database,
    client: TonivaClient,
    department_identifier: str | int,
    report_date: date,
    now: datetime,
    suppress_notified: bool = False,
    use_cache: bool = False,
) -> DepartmentReport:
    department = database.get_department(department_identifier)
    if department is None:
        raise ValueError("Departman bulunamadi.")
    if not department.api_key:
        raise ValueError("Departman API anahtari tanimli degil. /apitanimla veya /departmantanimla kullanin.")
    rules = database.get_rules(department.id)
    if not rules.is_configured:
        raise ValueError("Departman kurallari tanimli degil. Once /kuralayarla ile kurallari giriniz.")
    if database.is_department_weekly_leave(department.id, report_date.weekday(), report_date.isoformat()):
        message = (
            f"🟨 {department.name} için bugün haftalık departman izin günü.\n"
            "Otomatik saatlik rapor gönderilmez. Manuel /rapor da bu gün atlandı."
        )
        return DepartmentReport(department.telegram_chat_id, message, (), False, department.id, department.name)

    personnel = database.list_personnel(department.id)
    raw_calls, meta = await _fetch_conversations_cached(
        client, department.api_key, report_date, use_cache=use_cache
    )
    calls = normalize_calls(raw_calls, now.tzinfo)  # type: ignore[arg-type]
    leave_periods = _load_leave_periods(database, department.id, report_date, now.tzinfo)
    responsibles = database.list_responsibles(department.id)

    if len(raw_calls) == 0:
        evaluations: list[PersonnelEvaluation] = []
    else:
        evaluations = evaluate_department(
            calls,
            personnel,
            rules,
            report_date,
            now,
            now.tzinfo,  # type: ignore[arg-type]
            leave_periods=leave_periods,
        )
        evaluations = await _with_performance_totals(
            client, department.api_key, report_date, evaluations, personnel, use_cache=use_cache
        )

    # Saatlik (suppress_notified=True): ayni gun once bildirilen ihlal tipi tekrar gitmez.
    # Manuel /rapor (suppress_notified=False): o ana kadarki TUM ihlaller listelenir.
    notified_violations = (
        database.list_notified_violations(department.id, report_date.isoformat()) if suppress_notified else set()
    )
    report_evaluations = _filter_notified_violations(evaluations, notified_violations)
    notification_violations = tuple(_violation_keys(report_evaluations)) if suppress_notified else ()
    message = build_department_report(
        department=department,
        rules=rules,
        evaluations=report_evaluations,
        report_date=report_date,
        now=now,
        raw_call_count=len(raw_calls),
        processed_call_count=len(calls),
        raw_call_sample_keys=_raw_call_sample_keys(raw_calls),
        personnel=personnel,
        responsibles=responsibles,
        new_violations_only=suppress_notified,
    )
    extra_messages = _build_extra_messages(meta, len(raw_calls))
    should_send = _should_send_report(
        suppress_notified=suppress_notified,
        notification_violations=notification_violations,
        raw_call_count=len(raw_calls),
        processed_call_count=len(calls),
        has_extra=bool(extra_messages),
    )
    return DepartmentReport(
        department.telegram_chat_id,
        message,
        notification_violations,
        should_send,
        department.id,
        department.name,
        extra_messages,
    )


async def _fetch_conversations_cached(
    client: TonivaClient,
    api_key: str,
    report_date: date,
    use_cache: bool,
) -> tuple[list[dict], dict]:
    cache_key = (api_key.strip(), report_date.isoformat())
    if use_cache and cache_key in _api_call_cache:
        return _api_call_cache[cache_key]
    rows, meta = await client.fetch_conversations(api_key, report_date)
    if use_cache:
        _api_call_cache[cache_key] = (rows, meta)
    return rows, meta


def _filter_notified_violations(
    evaluations: list[PersonnelEvaluation],
    notified_violations: set[tuple[str, str]],
) -> list[PersonnelEvaluation]:
    if not notified_violations:
        return list(evaluations)
    filtered: list[PersonnelEvaluation] = []
    for evaluation in evaluations:
        next_evaluation = copy(evaluation)
        next_evaluation.calls = list(evaluation.calls)
        next_evaluation.leave_periods = list(evaluation.leave_periods)
        next_evaluation.violations = [
            violation
            for violation in evaluation.violations
            if (evaluation.name.casefold(), violation_key(violation)) not in notified_violations
        ]
        filtered.append(next_evaluation)
    return filtered


def _violation_keys(evaluations: list[PersonnelEvaluation]) -> list[tuple[str, str]]:
    return [
        (evaluation.name.casefold(), violation_key(violation))
        for evaluation in evaluations
        for violation in evaluation.violations
    ]


def _should_send_report(
    *,
    suppress_notified: bool,
    notification_violations: tuple[tuple[str, str], ...],
    raw_call_count: int,
    processed_call_count: int,
    has_extra: bool = False,
) -> bool:
    if not suppress_notified:
        return True
    if notification_violations:
        return True
    if raw_call_count == 0:
        return True
    if raw_call_count > 0 and processed_call_count == 0:
        return True
    if has_extra:
        return True
    return False


def _build_extra_messages(meta: dict, fetched_count: int) -> tuple[str, ...]:
    """Ciddi eksik veri varsa ayri (2.) mesaj olarak uyar.

    Cekim sirasinda canli cagrilar yuzunden total birkaC artabilir
    (or. 4826/4833). Kucuk farki alarm sayma.
    """
    if not meta:
        return ()
    total = meta.get("total_count") or meta.get("totalCount")
    try:
        total_int = int(float(str(total))) if total is not None else 0
    except ValueError:
        total_int = 0
    gap = (total_int - fetched_count) if total_int else 0
    # %2 veya 50 kayittan buyuk acik = gercek problem
    serious = gap > 50 and (total_int == 0 or gap > total_int * 0.02)
    if not serious:
        return ()
    return (
        (
            "⚠️ Toniva görüşme verisi tam çekilememiş olabilir.\n"
            f"Çekilen kayıt: {fetched_count}"
            + (f" / API total: {total_int}" if total_int else "")
            + f" (eksik ~{gap})\n"
            "Sayfalama veya API kesintisi olabilir. İhlal kontrolü kısmi veriye dayanıyor olabilir."
        ),
    )


def _raw_call_sample_keys(raw_calls: list[dict]) -> list[str]:
    if not raw_calls or not isinstance(raw_calls[0], dict):
        return []
    return [str(key) for key in raw_calls[0].keys()]


async def _with_performance_totals(
    client: TonivaClient,
    api_key: str,
    report_date: date,
    evaluations: list[PersonnelEvaluation],
    personnel: list[Personnel],
    use_cache: bool = False,
) -> list[PersonnelEvaluation]:
    try:
        performance_rows, _meta = await client.fetch_performance(api_key, report_date)
    except Exception as exc:
        logger.warning("Performans raporu alinamadi, gorusme toplamlari kullanilacak: %s", exc)
        return evaluations
    return _apply_performance_totals(evaluations, performance_rows, personnel)


def _apply_performance_totals(
    evaluations: list[PersonnelEvaluation],
    performance_rows: list[dict],
    personnel: list[Personnel],
) -> list[PersonnelEvaluation]:
    if not performance_rows:
        return evaluations
    totals = _performance_totals_by_person(performance_rows, personnel)
    if not totals:
        return evaluations
    updated: list[PersonnelEvaluation] = []
    for evaluation in evaluations:
        key = evaluation.name.casefold()
        if key not in totals:
            updated.append(evaluation)
            continue
        total_call_count, total_duration_seconds = totals[key]
        updated.append(
            replace(
                evaluation,
                total_call_count=total_call_count,
                total_call_duration_seconds=total_duration_seconds,
            )
        )
    return updated


def _performance_totals_by_person(
    performance_rows: list[dict],
    personnel: list[Personnel],
) -> dict[str, tuple[int, int]]:
    extension_to_name = {
        normalize_extension(person.extension): person.name
        for person in personnel
        if normalize_extension(person.extension)
    }
    known_names = {normalize_key(person.name): person.name for person in personnel}
    totals: dict[str, tuple[int, int]] = {}
    for row in performance_rows:
        if not isinstance(row, dict):
            continue
        name = _performance_person_name(row, extension_to_name, known_names)
        if not name:
            continue
        if personnel and name.casefold() not in {p.name.casefold() for p in personnel}:
            continue
        call_count = _to_int(
            _first_value(row, "TotalCall", "totalCall", "total_call", "callCount", "CallCount", "totalCalls")
        )
        # Toniva performance: TotalDuration ondalik SAAT (0.48 -> 28dk 48sn)
        duration_seconds = _performance_duration_to_seconds(
            _first_value(
                row,
                "TotalDuration",
                "totalDuration",
                "total_duration",
                "OutboundCallDuration",
                "outboundCallDuration",
                "TotalCallDuration",
                "talkDuration",
                "TalkDuration",
            )
        )
        totals[name.casefold()] = (call_count, duration_seconds)
    return totals


def _performance_person_name(
    row: dict,
    extension_to_name: dict[str, str],
    known_names: dict[str, str],
) -> str | None:
    extension = normalize_extension(
        _first_value(row, "ExtensionNumber", "extensionNumber", "extension", "Extension", "dahili")
    )
    if extension and extension in extension_to_name:
        return extension_to_name[extension]
    raw_name = _first_value(row, "ExtensionName", "extensionName", "agentName", "AgentName", "name", "Name")
    if raw_name is None:
        return None
    name = " ".join(str(raw_name).strip().split())
    if not name:
        return None
    return known_names.get(normalize_key(name), name)


def _first_value(row: dict, *keys: str) -> object | None:
    casefolded = {str(key).strip().casefold(): value for key, value in row.items()}
    normalized = {normalize_key(key): value for key, value in row.items()}
    for key in keys:
        value = row.get(key)
        if value is None:
            value = casefolded.get(key.casefold())
        if value is None:
            value = normalized.get(normalize_key(key))
        if value not in (None, ""):
            return value
    return None


def _to_int(value: object | None) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _performance_duration_to_seconds(value: object | None) -> int:
    """Toniva /reports/performance sure alanlari ondalik saat dondurur (or. 0.48)."""
    if value is None or value == "":
        return 0
    text = str(value).strip()
    if not text:
        return 0
    if ":" in text:
        return duration_to_seconds(text)
    try:
        hours = float(text.replace(",", "."))
    except ValueError:
        return 0
    return max(0, int(round(hours * 3600)))


def _load_leave_periods(database: Database, department_id: int, report_date: date, timezone) -> dict[str, list[tuple[datetime, datetime | None]]]:
    periods: dict[str, list[tuple[datetime, datetime | None]]] = {}
    for row in database.list_leave_periods(department_id, report_date.isoformat()):
        start_at = datetime.fromisoformat(str(row["start_at"])).astimezone(timezone)
        end_value = row["end_at"]
        end_at = datetime.fromisoformat(str(end_value)).astimezone(timezone) if end_value else None
        periods.setdefault(str(row["personnel_name"]).casefold(), []).append((start_at, end_at))
    return periods
