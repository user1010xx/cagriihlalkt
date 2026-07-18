from __future__ import annotations

from datetime import date, datetime

from bot.models import Department, DepartmentResponsible, DepartmentRules, Personnel
from bot.rules import PersonnelEvaluation
from bot.time_utils import format_time


MAX_DEBUG_KEYS = 12


def build_department_report(
    department: Department,
    rules: DepartmentRules,
    evaluations: list[PersonnelEvaluation],
    report_date: date,
    now: datetime,
    raw_call_count: int,
    personnel: list[Personnel],
    responsibles: list[DepartmentResponsible] | None = None,
    processed_call_count: int | None = None,
    raw_call_sample_keys: list[str] | None = None,
    new_violations_only: bool = False,
) -> str:
    violation_count = sum(len(evaluation.violations) for evaluation in evaluations)
    lines = [
        "📊 Toniva Çağrı Kural Kontrol Raporu",
        f"🏢 Departman: {department.name}",
        f"📅 Tarih: {report_date.strftime('%d.%m.%Y')} | ⏰ Kontrol: {now.strftime('%H:%M')}",
        f"⚙️ Kurallar: {_rules_summary(rules)}",
        _call_count_text(raw_call_count, processed_call_count),
        "",
    ]
    if raw_call_count and processed_call_count == 0 and raw_call_sample_keys:
        lines.extend(
            [
                "⚠️ API veri döndü ama bot işleyemedi.",
                f"Örnek API alanları: {', '.join(raw_call_sample_keys[:MAX_DEBUG_KEYS])}",
                "",
            ]
        )
    if raw_call_count == 0:
        lines.extend(
            [
                "🚨 ALARM: Toniva API 0 çağrı kaydı döndü!",
                "Gerçek çağrı yokluğu veya API/scope/IP sorunu olabilir.",
                f"Lütfen /rapor {department.name} ile manuel kontrol edin.",
                "",
            ]
        )
    if responsibles:
        mentions = " ".join(f"@{responsible.username}" for responsible in responsibles)
        lines.append(f"👥 Sorumlular: {mentions}")
    if not personnel:
        lines.extend(
            [
                "ℹ️ Bu departmanda personel listesi tanımlı değil.",
                "Sadece API'de görünen kişiler kontrol edildi.",
                "Paylaşılan API kullanıyorsanız personel listesi zorunludur.",
                "",
            ]
        )
    leave_count = sum(1 for evaluation in evaluations if evaluation.is_on_leave)
    leave_summary = f" | 🟨 {leave_count} izinli personel" if leave_count else ""
    if new_violations_only:
        lines.append(f"Özet: {'❌' if violation_count else '✅'} {violation_count} yeni ihlal{leave_summary}")
    else:
        lines.append(f"Özet: {'❌' if violation_count else '✅'} {violation_count} ihlal{leave_summary}")
    lines.append("")

    if violation_count:
        lines.append("❌ İhlaller")
        for evaluation in evaluations:
            if not evaluation.violations:
                continue
            extension_text = f" ({evaluation.extension})" if evaluation.extension else ""
            lines.append(f"👤 {evaluation.name}{extension_text}")
            for violation in evaluation.violations:
                lines.append(f"   • {violation}")
        lines.append("")

    _append_leave_people(lines, evaluations)

    if new_violations_only and not violation_count:
        lines.append("✅ Yeni ihlal yok. Önceden bildirilen ihlaller tekrar gönderilmedi.")
    elif not evaluations:
        lines.append("⚠️ Kontrol edilecek personel veya çağrı kaydı bulunamadı.")

    _append_personnel_call_counts(lines, evaluations)
    return "\n".join(lines)


def _append_personnel_call_counts(lines: list[str], evaluations: list[PersonnelEvaluation]) -> None:
    if not evaluations:
        return
    if lines and lines[-1]:
        lines.append("")
    lines.append("☎️ Personel Çağrı Adetleri")
    for evaluation in evaluations:
        extension_text = f" ({evaluation.extension})" if evaluation.extension else ""
        call_count = evaluation.total_call_count if evaluation.total_call_count is not None else len(evaluation.calls)
        duration_seconds = evaluation.total_call_duration_seconds
        if duration_seconds is None:
            duration_seconds = sum(_report_duration_seconds(call) for call in evaluation.calls)
        lines.append(
            f"   • {evaluation.name}{extension_text} - {call_count} çağrı - {_format_duration(duration_seconds)}"
        )


def _append_leave_people(lines: list[str], evaluations: list[PersonnelEvaluation]) -> None:
    leave_people = [evaluation for evaluation in evaluations if evaluation.is_on_leave]
    if not leave_people:
        return
    if lines and lines[-1]:
        lines.append("")
    lines.append("🟨 İzinli Personeller")
    for evaluation in leave_people:
        extension_text = f" ({evaluation.extension})" if evaluation.extension else ""
        lines.append(f"   • {evaluation.name}{extension_text}")


def _rules_summary(rules: DepartmentRules) -> str:
    items = [
        f"mesai başlangıcı {_format_optional_time(rules.work_start_time)}",
        f"max bekleme {_format_optional_minutes(rules.max_call_gap_minutes)}",
        f"mola öncesi bırakma {_format_optional_time(rules.pre_break_leave_time)}",
        f"mola {_format_optional_time(rules.break_start_time)}-{_format_optional_time(rules.break_end_time)}",
        f"mola sonrası başlangıç {_format_optional_time(rules.post_break_start_time)}",
        f"mesai sonu {_format_optional_time(rules.work_end_time)}",
    ]
    return ", ".join(items)


def _format_optional_time(value) -> str:
    return format_time(value) if value else "kapalı"


def _format_optional_minutes(value: int | None) -> str:
    return f"{value} dk" if value is not None else "kapalı"


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _report_duration_seconds(call) -> int:
    talk_duration_seconds = getattr(call, "talk_duration_seconds", None)
    if talk_duration_seconds is not None:
        return max(0, talk_duration_seconds)
    return max(0, call.duration_seconds)


def _call_count_text(raw_call_count: int, processed_call_count: int | None) -> str:
    if processed_call_count is None or processed_call_count == raw_call_count:
        return f"☎️ API görüşme kaydı: {raw_call_count}"
    return f"☎️ API görüşme kaydı: {raw_call_count} | işlenen: {processed_call_count}"


def split_telegram_message(message: str, limit: int = 3900) -> list[str]:
    if len(message) <= limit:
        return [message]
    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in message.splitlines():
        if len(line) + 1 > limit:
            if current:
                parts.append("\n".join(current))
                current = []
                current_length = 0
            parts.extend(_split_long_line(line, limit))
            continue
        line_length = len(line) + 1
        if current and current_length + line_length > limit:
            parts.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += line_length
    if current:
        parts.append("\n".join(current))
    return parts


def _split_long_line(line: str, limit: int) -> list[str]:
    return [line[index : index + limit] for index in range(0, len(line), limit)]
