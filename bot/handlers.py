from __future__ import annotations

from datetime import datetime
import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.access import allowed_only, departments_in_chat, is_private, resolve_department
from bot.config import Config
from bot.database import Database
from bot.personnel_import import parse_personnel_workbook
from bot.service import generate_department_report_payload
from bot.time_utils import format_time, parse_hhmm
from bot.toniva_client import TonivaClient


logger = logging.getLogger(__name__)

# Conversation states
(
    DEPT_ADD_NAME,
    DEPT_ADD_API,
    DEPT_DELETE_ID,
    API_DEPT,
    API_VALUE,
    RULE_DEPT,
    RULE_WORK_START,
    RULE_MAX_GAP,
    RULE_PRE_BREAK,
    RULE_BREAK_INTERVAL,
    RULE_POST_BREAK,
    RULE_WORK_END,
    PERS_ADD_DEPT,
    PERS_ADD_NAME,
    PERS_ADD_EXT,
    PERS_BULK_FILE,
    PERS_LIST_DEPT,
    PERS_DEL_DEPT,
    PERS_DEL_NAME,
    PERS_TOGGLE_DEPT,
    PERS_TOGGLE_NAME,
    LEAVE_DEPT,
    LEAVE_PERS,
    LEAVE_CANCEL_DEPT,
    LEAVE_CANCEL_PERS,
    LEAVE_LIST_DEPT,
    WEEKLY_DEPT,
    WEEKLY_DAY,
    WEEKLY_EDIT_ACTION,
    WEEKLY_CANCEL_DEPT,
    WEEKLY_CANCEL_DAY,
    RESP_ADD_DEPT,
    RESP_ADD_USER,
    RESP_DEL_DEPT,
    RESP_DEL_USER,
    RESP_LIST_DEPT,
) = range(36)

WEEKDAY_MAP = {
    "pazartesi": 0,
    "sali": 1,
    "salı": 1,
    "carsamba": 2,
    "çarşamba": 2,
    "persembe": 3,
    "perşembe": 3,
    "cuma": 4,
    "cumartesi": 5,
    "pazar": 6,
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
}
WEEKDAY_NAMES = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]

HELP_TEXT = """🤖 Toniva Çağrı Denetim Botu

Bu bot yalnızca yetkili Telegram gruplarında çalışır. Özel sohbette yanıt vermez.
Gruptaki herkes yetkilidir.

📌 Kurulum
/departmantanimla — bu gruba departman + API key bağla
/apitanimla — mevcut departmana API key güncelle
/departman_listele /departman_sil /departman_aktif /departman_pasif

⚙️ Kurallar (önce departman adı — aynı grupta 2 departman için)
/kuralayarla — adım adım (uygulanmayacak kural için: boş)
/kurallistele

👤 Personel (önce departman adı sorulur — aynı grupta 2 departman için)
/personelekle /personeltopluekle /personel_listele
/personel_sil /personel_aktif /personel_pasif

🟨 İzin
/izin /iziniptal /izinlistele
/haftalikizin /haftalikizinduzenle /haftalikiziniptal
  (önce departman adı — aynı grupta 2 departman ayrımı; izin günü o departmana rapor yok)

👥 Sorumlu
/sorumluekle /sorumlusil /sorumlulistele

📊 Rapor
/rapor [departman]
/kontroltoniva [departman]

ℹ️ /chat_id /kimim /iptal /help

Not: Aynı Toniva API iki departmanda paylaşılabilir; ayrım personel listesi ile yapılır.
"""


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["database"]


def _cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["config"]


def _client(context: ContextTypes.DEFAULT_TYPE) -> TonivaClient:
    return context.application.bot_data["client"]


def _now(context: ContextTypes.DEFAULT_TYPE) -> datetime:
    return datetime.now(_cfg(context).timezone)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_private(update):
        return ConversationHandler.END
    context.user_data.clear()
    if update.effective_message:
        await update.effective_message.reply_text("İşlem iptal edildi.")
    return ConversationHandler.END


@allowed_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


@allowed_only
async def chat_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"Chat ID: `{chat.id}`\nBaşlık: {chat.title or '-'}",
        parse_mode="Markdown",
    )


@allowed_only
async def kimim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        f"ID: {user.id}\nAd: {user.full_name}\nUsername: @{user.username or '-'}"
    )


@allowed_only
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Bilinmeyen komut. /help yazabilirsiniz.")


# --- Departman tanim ---


@allowed_only
async def departmantanimla_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(
        "Departman adını yazın.\n"
        "Aynı grupta birden fazla departman olabilir (paylaşılan API senaryosu).\n"
        "İptal: /iptal\n\n"
        "💡 Grupta bot düz metni almayabilir: bu mesaja YANITLAYARAK yazın "
        "(veya BotFather → /setprivacy → Disable)."
    )
    return DEPT_ADD_NAME


@allowed_only
async def departmantanimla_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    if not name:
        await update.effective_message.reply_text("Geçerli bir ad yazın.")
        return DEPT_ADD_NAME
    if _db(context).get_department(name):
        await update.effective_message.reply_text("Bu isimde departman zaten var. Farklı ad deneyin.")
        return DEPT_ADD_NAME
    context.user_data["dept_name"] = name
    await update.effective_message.reply_text(
        "Toniva API key yapıştırın (tva_...).\n"
        "Aynı key'i başka departmanda da kullanabilirsiniz; ayrım personel listesi ile yapılır."
    )
    return DEPT_ADD_API


@allowed_only
async def departmantanimla_api(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    api_key = (update.effective_message.text or "").strip()
    if not api_key:
        await update.effective_message.reply_text("API key boş olamaz.")
        return DEPT_ADD_API
    name = context.user_data.get("dept_name")
    chat_id = str(update.effective_chat.id)
    department = _db(context).add_department(name, chat_id, api_key)
    context.user_data.clear()
    await update.effective_message.reply_text(
        f"✅ Departman kaydedildi: {department.name}\n"
        f"Chat ID: {department.telegram_chat_id}\n"
        f"API: {'tanımlı' if department.api_key else 'yok'}\n\n"
        "Sıradaki adımlar:\n"
        "1) /kuralayarla\n"
        "2) /personelekle veya /personeltopluekle\n"
        "3) İsteğe bağlı /sorumluekle, /haftalikizin"
    )
    return ConversationHandler.END


@allowed_only
async def departman_listele(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Bu grupta tanımlı departman yok. /departmantanimla")
        return
    lines = ["📋 Bu gruptaki departmanlar:"]
    for d in depts:
        rules = _db(context).get_rules(d.id)
        pers = len(_db(context).list_personnel(d.id))
        lines.append(
            f"• {d.name} | aktif={d.is_active} | api={'✓' if d.api_key else '✗'} | "
            f"kural={'✓' if rules.is_configured else '✗'} | personel={pers}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@allowed_only
async def departman_sil_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Silinecek departman adını yazın:")
    return DEPT_DELETE_ID


@allowed_only
async def departman_sil_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), name)
    if department is None:
        await update.effective_message.reply_text("Bu grupta böyle bir departman yok.")
        return ConversationHandler.END
    _db(context).delete_department(department.id)
    await update.effective_message.reply_text(f"🗑️ Silindi: {department.name}")
    return ConversationHandler.END


@allowed_only
async def departman_aktif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _toggle_department(update, context, True)


@allowed_only
async def departman_pasif(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _toggle_department(update, context, False)


async def _toggle_department(update: Update, context: ContextTypes.DEFAULT_TYPE, active: bool) -> None:
    args = context.args or []
    name = " ".join(args).strip() if args else None
    department = resolve_department(update, _db(context), name)
    if department is None:
        depts = departments_in_chat(update, _db(context))
        if not depts:
            await update.effective_message.reply_text("Departman bulunamadı.")
            return
        if len(depts) > 1 and not name:
            await update.effective_message.reply_text(
                "Birden fazla departman var. Örnek: /departman_aktif DepartmanAdı"
            )
            return
        department = depts[0]
    _db(context).set_department_active(department.id, active)
    await update.effective_message.reply_text(
        f"{'✅ Aktif' if active else '⏸ Pasif'}: {department.name}"
    )


# --- API tanim ---


@allowed_only
async def apitanimla_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Önce /departmantanimla ile departman ekleyin.")
        return ConversationHandler.END
    if len(depts) == 1:
        context.user_data["api_dept"] = depts[0].name
        await update.effective_message.reply_text(f"{depts[0].name} için yeni API key yapıştırın:")
        return API_VALUE
    await update.effective_message.reply_text(
        "Hangi departman?\n" + "\n".join(f"• {d.name}" for d in depts)
    )
    return API_DEPT


@allowed_only
async def apitanimla_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), name)
    if department is None:
        await update.effective_message.reply_text("Departman bulunamadı.")
        return ConversationHandler.END
    context.user_data["api_dept"] = department.name
    await update.effective_message.reply_text("Yeni API key yapıştırın (tva_...):")
    return API_VALUE


@allowed_only
async def apitanimla_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    api_key = (update.effective_message.text or "").strip()
    name = context.user_data.get("api_dept")
    if not api_key or not name:
        await update.effective_message.reply_text("Eksik bilgi.")
        return ConversationHandler.END
    ok = _db(context).update_department_api_key(name, api_key)
    context.user_data.clear()
    await update.effective_message.reply_text("✅ API key güncellendi." if ok else "❌ Güncellenemedi.")
    return ConversationHandler.END


# --- Kurallar ---


@allowed_only
async def kuralayarla_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Önce departman tanımlayın.")
        return ConversationHandler.END
    # Her zaman departman sor (ayni grupta 2 departman / farkli kurallar)
    await update.effective_message.reply_text(
        _dept_pick_prompt(depts, "Kural ayarlama")
        + "\n\n💡 Grupta bot düz metni almayabilir: bu mesaja YANITLAYARAK yazın."
    )
    return RULE_DEPT


@allowed_only
async def kuralayarla_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), name)
    if department is None:
        depts = departments_in_chat(update, _db(context))
        await update.effective_message.reply_text(
            "Bu grupta böyle bir departman yok. Tekrar yazın (yanıt olarak).\n"
            + "\n".join(f"• {d.name}" for d in depts)
        )
        return RULE_DEPT
    context.user_data["rule_dept"] = department.name
    await update.effective_message.reply_text(
        f"Departman: {department.name}\n"
        "Mesai başlangıç saati (HH:MM). Uygulanmayacaksa: boş\n"
        "💡 Yanıtlayarak yazın."
    )
    return RULE_WORK_START


def _optional_time_text(text: str) -> str | None:
    cleaned = text.strip()
    if not cleaned or cleaned.casefold() in {"bos", "boş", "kapali", "kapalı", "-", "none", "null"}:
        return None
    parse_hhmm(cleaned)
    return cleaned


@allowed_only
async def kuralayarla_work_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["work_start"] = _optional_time_text(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return RULE_WORK_START
    await update.effective_message.reply_text("Maks. çağrı arası bekleme (dakika) veya boş:")
    return RULE_MAX_GAP


@allowed_only
async def kuralayarla_max_gap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text or text.casefold() in {"bos", "boş", "-"}:
        context.user_data["max_gap"] = None
    else:
        try:
            context.user_data["max_gap"] = int(text)
        except ValueError:
            await update.effective_message.reply_text("Sayı girin veya boş yazın.")
            return RULE_MAX_GAP
    await update.effective_message.reply_text("Mola öncesi çağrı bırakma saati (HH:MM) veya boş:")
    return RULE_PRE_BREAK


@allowed_only
async def kuralayarla_pre_break(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["pre_break"] = _optional_time_text(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return RULE_PRE_BREAK
    await update.effective_message.reply_text(
        "Mola aralığı HH:MM-HH:MM (örn 13:00-14:00) veya boş:"
    )
    return RULE_BREAK_INTERVAL


@allowed_only
async def kuralayarla_break_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    if not text or text.casefold() in {"bos", "boş", "-"}:
        context.user_data["break_start"] = None
        context.user_data["break_end"] = None
    else:
        if "-" not in text:
            await update.effective_message.reply_text("Format: 13:00-14:00 veya boş")
            return RULE_BREAK_INTERVAL
        start_text, end_text = text.split("-", 1)
        try:
            context.user_data["break_start"] = _optional_time_text(start_text)
            context.user_data["break_end"] = _optional_time_text(end_text)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return RULE_BREAK_INTERVAL
    await update.effective_message.reply_text("Mola sonrası çağrı başlangıç saati (HH:MM) veya boş:")
    return RULE_POST_BREAK


@allowed_only
async def kuralayarla_post_break(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["post_break"] = _optional_time_text(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return RULE_POST_BREAK
    await update.effective_message.reply_text("Mesai bitiş saati (HH:MM) veya boş:")
    return RULE_WORK_END


@allowed_only
async def kuralayarla_work_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        work_end = _optional_time_text(update.effective_message.text or "")
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return RULE_WORK_END
    name = context.user_data.get("rule_dept")
    ok = _db(context).update_rules(
        name,
        work_start_time=context.user_data.get("work_start"),
        pre_break_leave_time=context.user_data.get("pre_break"),
        break_start_time=context.user_data.get("break_start"),
        break_end_time=context.user_data.get("break_end"),
        post_break_start_time=context.user_data.get("post_break"),
        work_end_time=work_end,
        max_call_gap_minutes=context.user_data.get("max_gap"),
    )
    context.user_data.clear()
    await update.effective_message.reply_text("✅ Kurallar kaydedildi." if ok else "❌ Kayıt başarısız.")
    return ConversationHandler.END


@allowed_only
async def kurallistele(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return
    lines = ["⚙️ Kurallar"]
    for d in depts:
        r = _db(context).get_rules(d.id)
        if not r.is_configured:
            lines.append(f"• {d.name}: yapılandırılmamış")
            continue
        lines.append(
            f"• {d.name}: start={_fmt(r.work_start_time)} gap={r.max_call_gap_minutes} "
            f"pre={_fmt(r.pre_break_leave_time)} break={_fmt(r.break_start_time)}-{_fmt(r.break_end_time)} "
            f"post={_fmt(r.post_break_start_time)} end={_fmt(r.work_end_time)}"
        )
    await update.effective_message.reply_text("\n".join(lines))


def _fmt(value) -> str:
    return format_time(value) if value else "-"


# --- Personel ---


def _dept_pick_prompt(depts: list, action: str) -> str:
    lines = [
        f"{action} için departman adını yazın.",
        "(Aynı grupta birden fazla departman varsa personel o departmana bağlanır.)",
        "",
        "Bu gruptaki departmanlar:",
    ]
    for d in depts:
        lines.append(f"• {d.name}")
    return "\n".join(lines)


@allowed_only
async def personelekle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Önce departman tanımlayın.")
        return ConversationHandler.END
    # Her zaman departman sor (tek departman olsa bile; 2 departmanli gruplar icin netlik)
    await update.effective_message.reply_text(_dept_pick_prompt(depts, "Personel ekleme"))
    return PERS_ADD_DEPT


@allowed_only
async def personelekle_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), name)
    if department is None:
        depts = departments_in_chat(update, _db(context))
        await update.effective_message.reply_text(
            "Bu grupta böyle bir departman yok. Tekrar yazın.\n"
            + "\n".join(f"• {d.name}" for d in depts)
        )
        return PERS_ADD_DEPT
    context.user_data["pers_dept"] = department.name
    await update.effective_message.reply_text(
        f"Departman: {department.name}\nPersonel adını yazın:"
    )
    return PERS_ADD_NAME


@allowed_only
async def personelekle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["pers_name"] = (update.effective_message.text or "").strip()
    if not context.user_data["pers_name"]:
        await update.effective_message.reply_text("Ad boş olamaz.")
        return PERS_ADD_NAME
    await update.effective_message.reply_text("Dahili numara (yoksa: boş):")
    return PERS_ADD_EXT


@allowed_only
async def personelekle_ext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    extension = None if not text or text.casefold() in {"bos", "boş", "-"} else text
    dept_name = context.user_data.get("pers_dept")
    person = _db(context).add_personnel(
        dept_name,
        context.user_data.get("pers_name"),
        extension,
    )
    context.user_data.clear()
    if person is None:
        await update.effective_message.reply_text(
            f"❌ Eklenemedi (isim çakışması olabilir). Departman: {dept_name}"
        )
    else:
        ext = f" ({person.extension})" if person.extension else ""
        await update.effective_message.reply_text(
            f"✅ Eklendi: {person.name}{ext}\n🏢 Departman: {dept_name}"
        )
    return ConversationHandler.END


@allowed_only
async def personeltopluekle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Önce departman tanımlayın.")
        return ConversationHandler.END
    await update.effective_message.reply_text(_dept_pick_prompt(depts, "Toplu personel ekleme"))
    return PERS_ADD_DEPT


@allowed_only
async def personeltopluekle_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), name)
    if department is None:
        depts = departments_in_chat(update, _db(context))
        await update.effective_message.reply_text(
            "Bu grupta böyle bir departman yok. Tekrar yazın.\n"
            + "\n".join(f"• {d.name}" for d in depts)
        )
        return PERS_ADD_DEPT
    context.user_data["bulk_dept"] = department.name
    await update.effective_message.reply_text(
        f"Departman: {department.name}\n"
        "Excel (.xlsx) dosyası gönderin. Sütunlar: Ad / Dahili"
    )
    return PERS_BULK_FILE


@allowed_only
async def personeltopluekle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.effective_message.document
    if document is None:
        await update.effective_message.reply_text("Lütfen .xlsx dosyası gönderin.")
        return PERS_BULK_FILE
    file = await document.get_file()
    data = bytes(await file.download_as_bytearray())
    try:
        imported = parse_personnel_workbook(data)
    except Exception as exc:
        await update.effective_message.reply_text(f"Dosya okunamadı: {exc}")
        return ConversationHandler.END
    if not imported:
        await update.effective_message.reply_text(
            "Dosyada personel satırı bulunamadı. "
            "Başlıklı (Ad/Dahili) veya başlıksız (ad | dahili) iki kolon kullanın."
        )
        return ConversationHandler.END
    dept = context.user_data.get("bulk_dept")
    added = 0
    updated = 0
    unchanged = 0
    failed = 0
    failed_names: list[str] = []
    for item in imported:
        try:
            person, action = _db(context).upsert_personnel(dept, item.name, item.extension)
            if action == "added" and person:
                added += 1
            elif action == "updated" and person:
                updated += 1
            elif action == "unchanged":
                unchanged += 1
            else:
                failed += 1
                failed_names.append(item.name)
        except Exception:
            failed += 1
            failed_names.append(item.name)
    dept_label = dept or "?"
    context.user_data.clear()
    msg = (
        f"✅ Departman: {dept_label}\n"
        f"Dosyadan okunan: {len(imported)}\n"
        f"Eklenen: {added} | Dahili güncellenen: {updated} | Aynı kalan: {unchanged} | Hata: {failed}"
    )
    if failed_names:
        sample = ", ".join(failed_names[:10])
        more = f" …(+{len(failed_names)-10})" if len(failed_names) > 10 else ""
        msg += f"\nHatalılar: {sample}{more}"
    # Dahili ornek
    sample_with_ext = [f"{p.name} ({p.extension})" for p in imported if p.extension][:5]
    if sample_with_ext:
        msg += "\nÖrnek dahililer: " + ", ".join(sample_with_ext)
    await update.effective_message.reply_text(msg)
    return ConversationHandler.END


@allowed_only
async def personel_listele(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return
    args = context.args or []
    name = " ".join(args).strip() if args else None
    targets = [resolve_department(update, _db(context), name)] if name else depts
    targets = [t for t in targets if t is not None]
    if not targets:
        await update.effective_message.reply_text("Departman bulunamadı.")
        return
    lines = []
    for d in targets:
        people = _db(context).list_personnel(d.id, only_active=False)
        lines.append(f"👤 {d.name} ({len(people)})")
        if not people:
            lines.append("  (boş)")
            continue
        for p in people:
            mark = "✓" if p.is_active else "✗"
            ext = f" | {p.extension}" if p.extension else ""
            lines.append(f"  {mark} {p.name}{ext}")
    await update.effective_message.reply_text("\n".join(lines))


@allowed_only
async def personel_sil_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _pers_toggle_start(update, context, "sil")


@allowed_only
async def personel_pasif_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["toggle_active"] = False
    return await _pers_toggle_start(update, context, "pasif")


@allowed_only
async def personel_aktif_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["toggle_active"] = True
    return await _pers_toggle_start(update, context, "aktif")


async def _pers_toggle_start(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> int:
    context.user_data["pers_mode"] = mode
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    labels = {"sil": "Personel silme", "pasif": "Personel pasif", "aktif": "Personel aktif"}
    await update.effective_message.reply_text(
        _dept_pick_prompt(depts, labels.get(mode, "Personel işlemi"))
    )
    return PERS_TOGGLE_DEPT


@allowed_only
async def personel_toggle_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), name)
    if department is None:
        depts = departments_in_chat(update, _db(context))
        await update.effective_message.reply_text(
            "Bu grupta böyle bir departman yok. Tekrar yazın.\n"
            + "\n".join(f"• {d.name}" for d in depts)
        )
        return PERS_TOGGLE_DEPT
    context.user_data["pers_dept"] = department.name
    await update.effective_message.reply_text(
        f"Departman: {department.name}\nPersonel adını yazın:"
    )
    return PERS_TOGGLE_NAME


@allowed_only
async def personel_toggle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    dept_name = context.user_data.get("pers_dept")
    person_name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), dept_name)
    if department is None:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    person = _db(context).find_personnel_by_name(department.id, person_name)
    if person is None:
        await update.effective_message.reply_text("Personel bulunamadı.")
        return ConversationHandler.END
    mode = context.user_data.get("pers_mode")
    if mode == "sil":
        _db(context).delete_personnel(person.id)
        await update.effective_message.reply_text(f"🗑️ Silindi: {person.name}")
    else:
        active = bool(context.user_data.get("toggle_active"))
        _db(context).set_personnel_active(person.id, active)
        await update.effective_message.reply_text(
            f"{'✅ Aktif' if active else '⏸ Pasif'}: {person.name}"
        )
    context.user_data.clear()
    return ConversationHandler.END


# --- Izin ---


@allowed_only
async def izin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _leave_start(update, context, "start")


@allowed_only
async def iziniptal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _leave_start(update, context, "end")


async def _leave_start(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> int:
    context.user_data["leave_mode"] = mode
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    if len(depts) == 1:
        context.user_data["leave_dept"] = depts[0].name
        await update.effective_message.reply_text("Personel adı:")
        return LEAVE_PERS
    await update.effective_message.reply_text(
        "Hangi departman?\n" + "\n".join(f"• {d.name}" for d in depts)
    )
    return LEAVE_DEPT


@allowed_only
async def izin_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    if resolve_department(update, _db(context), name) is None:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    context.user_data["leave_dept"] = name
    await update.effective_message.reply_text("Personel adı:")
    return LEAVE_PERS


@allowed_only
async def izin_personnel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    dept_name = context.user_data.get("leave_dept")
    person_name = (update.effective_message.text or "").strip()
    department = resolve_department(update, _db(context), dept_name)
    if department is None:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    person = _db(context).find_personnel_by_name(department.id, person_name)
    if person is None:
        await update.effective_message.reply_text("Kayıtlı personel adı zorunlu.")
        return ConversationHandler.END
    now = _now(context).isoformat()
    mode = context.user_data.get("leave_mode")
    if mode == "start":
        if _db(context).has_active_leave(department.id, person.name, now):
            await update.effective_message.reply_text("Bu personelin zaten açık izni var.")
        else:
            _db(context).start_leave(department.id, person.name, now)
            await update.effective_message.reply_text(f"🟨 İzin başlatıldı: {person.name}")
    else:
        ok = _db(context).end_leave(department.id, person.name, now)
        await update.effective_message.reply_text(
            f"✅ İzin kapatıldı: {person.name}" if ok else "Açık izin bulunamadı."
        )
    context.user_data.clear()
    return ConversationHandler.END


@allowed_only
async def izinlistele(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return
    now = _now(context).isoformat()
    lines = ["🟨 Aktif izinler"]
    any_leave = False
    for d in depts:
        rows = _db(context).list_active_leave_periods(d.id, now)
        if not rows:
            continue
        any_leave = True
        lines.append(f"• {d.name}")
        for row in rows:
            lines.append(f"  - {row['personnel_name']} (başlangıç: {row['start_at']})")
    if not any_leave:
        lines.append("(yok)")
    await update.effective_message.reply_text("\n".join(lines))


# --- Haftalik izin ---


def _weekly_dept_prompt(depts: list) -> str:
    """Aynı grupta birden fazla departman olabileceği için her zaman ad sorulur."""
    lines = [
        "Hangi departman için haftalık izin? (önce departman adı)",
        "",
        "Bu gruptaki departmanlar:",
    ]
    lines.extend(f"• {d.name}" for d in depts)
    return "\n".join(lines)


@allowed_only
async def haftalikizin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("weekly_mode", None)
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    # Tek departman olsa bile ad sorulur — hangi departmana izin girdiği net olsun.
    await update.effective_message.reply_text(_weekly_dept_prompt(depts))
    return WEEKLY_DEPT


@allowed_only
async def haftalikizin_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    dept = resolve_department(update, _db(context), name)
    if dept is None:
        await update.effective_message.reply_text(
            "Bu grupta böyle bir departman yok. Adı tekrar yazın veya /iptal."
        )
        return WEEKLY_DEPT
    context.user_data["weekly_dept"] = dept.name
    action = context.user_data.get("weekly_mode")
    if action == "edit":
        rows = _db(context).list_department_weekly_leaves(dept.id)
        days = ", ".join(WEEKDAY_NAMES[int(r["weekday"])] for r in rows) or "(yok)"
        await update.effective_message.reply_text(
            f"🏢 {dept.name}\nMevcut günler: {days}\n"
            "Yeni gün eklemek için gün adı yazın veya 'tümünü kaldır':"
        )
        return WEEKLY_EDIT_ACTION
    await update.effective_message.reply_text(
        f"🏢 {dept.name} için haftalık izin günü yazın\n"
        "(pazartesi...pazar veya 0-6):"
    )
    return WEEKLY_DAY


@allowed_only
async def haftalikizin_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    day = _parse_weekday(update.effective_message.text or "")
    if day is None:
        await update.effective_message.reply_text("Geçersiz gün. Örn: pazartesi veya 0")
        return WEEKLY_DAY
    name = context.user_data.get("weekly_dept")
    if not name:
        await update.effective_message.reply_text("Departman seçilmedi. /haftalikizin ile yeniden başlayın.")
        return ConversationHandler.END
    _db(context).add_department_weekly_leave(name, day)
    context.user_data.clear()
    await update.effective_message.reply_text(
        f"✅ Haftalık izin eklendi\n🏢 {name} → {WEEKDAY_NAMES[day]}\n"
        "Bu gün o departman için kontrol ve gruba rapor yapılmaz."
    )
    return ConversationHandler.END


@allowed_only
async def haftalikizinduzenle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["weekly_mode"] = "edit"
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    await update.effective_message.reply_text(_weekly_dept_prompt(depts))
    return WEEKLY_DEPT


@allowed_only
async def haftalikizinduzenle_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip()
    name = context.user_data.get("weekly_dept")
    if not name:
        await update.effective_message.reply_text("Departman seçilmedi. /haftalikizinduzenle ile yeniden başlayın.")
        return ConversationHandler.END
    if text.casefold() in {"tümünü kaldır", "tumunu kaldir", "temizle", "hepsini sil"}:
        _db(context).delete_department_weekly_leave(name)
        await update.effective_message.reply_text(f"✅ {name}: tüm haftalık izinler kaldırıldı.")
        context.user_data.clear()
        return ConversationHandler.END
    day = _parse_weekday(text)
    if day is None:
        await update.effective_message.reply_text("Gün adı veya 'tümünü kaldır' yazın.")
        return WEEKLY_EDIT_ACTION
    _db(context).add_department_weekly_leave(name, day)
    context.user_data.clear()
    await update.effective_message.reply_text(f"✅ {name} → eklendi: {WEEKDAY_NAMES[day]}")
    return ConversationHandler.END


@allowed_only
async def haftalikiziniptal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    await update.effective_message.reply_text(_weekly_dept_prompt(depts))
    return WEEKLY_CANCEL_DEPT


@allowed_only
async def haftalikiziniptal_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    dept = resolve_department(update, _db(context), name)
    if dept is None:
        await update.effective_message.reply_text(
            "Bu grupta böyle bir departman yok. Adı tekrar yazın veya /iptal."
        )
        return WEEKLY_CANCEL_DEPT
    context.user_data["weekly_dept"] = dept.name
    await update.effective_message.reply_text(
        f"🏢 {dept.name}\n"
        "Bugün için tek seferlik iptal: 'bugün'\n"
        "Kalıcı gün silme: gün adı (pazartesi...)"
    )
    return WEEKLY_CANCEL_DAY


@allowed_only
async def haftalikiziniptal_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.effective_message.text or "").strip().casefold()
    name = context.user_data.get("weekly_dept")
    if not name:
        await update.effective_message.reply_text("Departman seçilmedi. /haftalikiziniptal ile yeniden başlayın.")
        return ConversationHandler.END
    if text in {"bugun", "bugün", "today"}:
        leave_date = _now(context).date().isoformat()
        _db(context).cancel_department_weekly_leave(name, leave_date)
        await update.effective_message.reply_text(
            f"✅ {name}: bugünün haftalık izni iptal ({leave_date}) — bugün kontrol yapılır."
        )
    else:
        day = _parse_weekday(text)
        if day is None:
            await update.effective_message.reply_text("Geçersiz. 'bugün' veya gün adı yazın.")
            return WEEKLY_CANCEL_DAY
        _db(context).delete_department_weekly_leave(name, day)
        await update.effective_message.reply_text(f"✅ {name}: kaldırıldı → {WEEKDAY_NAMES[day]}")
    context.user_data.clear()
    return ConversationHandler.END


def _parse_weekday(text: str) -> int | None:
    key = text.strip().casefold()
    return WEEKDAY_MAP.get(key)


# --- Sorumlu ---


@allowed_only
async def sorumluekle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _resp_start(update, context, "add")


@allowed_only
async def sorumlusil_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _resp_start(update, context, "del")


@allowed_only
async def sorumlulistele_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return
    lines = ["👥 Sorumlular"]
    for d in depts:
        people = _db(context).list_responsibles(d.id)
        lines.append(f"• {d.name}: " + (", ".join(f"@{p.username}" for p in people) or "(yok)"))
    await update.effective_message.reply_text("\n".join(lines))


async def _resp_start(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str) -> int:
    context.user_data["resp_mode"] = mode
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    if len(depts) == 1:
        context.user_data["resp_dept"] = depts[0].name
        await update.effective_message.reply_text("Telegram kullanıcı adı (@ olmadan da olur):")
        return RESP_ADD_USER
    await update.effective_message.reply_text(
        "Hangi departman?\n" + "\n".join(f"• {d.name}" for d in depts)
    )
    return RESP_ADD_DEPT


@allowed_only
async def sorumlu_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.effective_message.text or "").strip()
    if resolve_department(update, _db(context), name) is None:
        await update.effective_message.reply_text("Departman yok.")
        return ConversationHandler.END
    context.user_data["resp_dept"] = name
    await update.effective_message.reply_text("Telegram kullanıcı adı:")
    return RESP_ADD_USER


@allowed_only
async def sorumlu_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = (update.effective_message.text or "").strip()
    name = context.user_data.get("resp_dept")
    mode = context.user_data.get("resp_mode")
    try:
        if mode == "add":
            ok = _db(context).add_responsible(name, username)
            await update.effective_message.reply_text("✅ Eklendi." if ok else "❌ Hata")
        else:
            ok = _db(context).delete_responsible(name, username)
            await update.effective_message.reply_text("🗑️ Silindi." if ok else "Bulunamadı.")
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
    context.user_data.clear()
    return ConversationHandler.END


# --- Rapor ---


@allowed_only
async def rapor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_report(update, context, suppress_notified=False)


@allowed_only
async def kontroltoniva(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    name = " ".join(args).strip() if args else None
    department = resolve_department(update, _db(context), name)
    depts = departments_in_chat(update, _db(context))
    if department is None:
        if len(depts) == 1:
            department = depts[0]
        elif not depts:
            await update.effective_message.reply_text("Departman yok.")
            return
        else:
            await update.effective_message.reply_text(
                "Departman belirtin: /kontroltoniva Ad\n" + "\n".join(f"• {d.name}" for d in depts)
            )
            return
    if not department.api_key:
        await update.effective_message.reply_text("API key yok. /apitanimla")
        return
    await update.effective_message.reply_text("Toniva API kontrol ediliyor...")
    try:
        result = await _client(context).ping(department.api_key)
        await update.effective_message.reply_text(
            f"✅ Toniva erişimi OK\nDepartman: {department.name}\n"
            f"Örnek satır: {result.get('sample_row_count')}\nMeta: {result.get('meta')}"
        )
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Toniva hata: {exc}")


async def _run_report(update: Update, context: ContextTypes.DEFAULT_TYPE, suppress_notified: bool) -> None:
    args = context.args or []
    name = " ".join(args).strip() if args else None
    depts = departments_in_chat(update, _db(context))
    if not depts:
        await update.effective_message.reply_text("Bu grupta departman yok. /departmantanimla")
        return
    if name:
        targets = [resolve_department(update, _db(context), name)]
        targets = [t for t in targets if t is not None]
        if not targets:
            await update.effective_message.reply_text("Departman bulunamadı.")
            return
    else:
        targets = depts

    await update.effective_message.reply_text(f"Rapor hazırlanıyor ({len(targets)} departman)...")
    now = _now(context)
    for department in targets:
        try:
            # /rapor: suppress_notified=False -> o ana kadarki TUM ihlaller
            report = await generate_department_report_payload(
                _db(context),
                _client(context),
                department.id,
                now.date(),
                now,
                suppress_notified=suppress_notified,
            )
            # Haftalık izin: kontrol yok; kullanıcı komut verdiği için tek satır bilgi
            if not report.should_send:
                await update.effective_message.reply_text(
                    f"🟨 {department.name}: bugün haftalık izin — kontrol yapılmadı, rapor yok."
                )
                continue
            from bot.reporting import split_telegram_message

            for part in split_telegram_message(report.message):
                if part.strip():
                    await update.effective_message.reply_text(part)
            for extra in report.extra_messages:
                for part in split_telegram_message(extra):
                    if part.strip():
                        await update.effective_message.reply_text(part)
        except Exception as exc:
            await update.effective_message.reply_text(f"❌ {department.name}: {exc}")
