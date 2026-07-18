from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from telegram.ext import Application

from bot.config import Config
from bot.database import Database
from bot.reporting import split_telegram_message
from bot.service import clear_api_call_cache, generate_department_report_payload
from bot.toniva_client import TonivaClient


logger = logging.getLogger(__name__)


async def run_scheduler(application: Application) -> None:
    config: Config = application.bot_data["config"]
    await asyncio.sleep(15)
    try:
        await send_scheduled_reports(application)
    except Exception:
        logger.exception("Zamanlanmis rapor baslangic calistirmasinda hata.")

    while True:
        await asyncio.sleep(_seconds_until_next_run(config))
        try:
            await send_scheduled_reports(application)
        except Exception:
            logger.exception("Zamanlanmis rapor dongusunde beklenmeyen hata.")


async def send_scheduled_reports(application: Application) -> None:
    """Saatlik: yalnizca YENI ihlalleri (ve 0 cagri alarmi) gruba gonderir.

    Ayni gun ayni personel+ihlal tipi bir kez bildirilir; basarili gonderimden
    sonra isaretlenir. Manuel /rapor tum ihlalleri gosterir (suppress yok).
    """
    config: Config = application.bot_data["config"]
    database: Database = application.bot_data["database"]
    client: TonivaClient = application.bot_data["client"]
    now = datetime.now(config.timezone)
    application.bot_data["last_scheduler_run"] = now.isoformat()
    clear_api_call_cache()

    try:
        database.cleanup_old_notified_violations(now.date().isoformat())
    except Exception:
        logger.warning("Eski notified ihlaller temizlenemedi", exc_info=True)

    if not _is_within_report_window(config, now):
        logger.info("Zamanlanmis rapor saati disinda: %s", now.strftime("%H:%M"))
        return

    departments = database.list_departments(only_active=True)
    delay = config.department_report_delay_seconds
    for index, department in enumerate(departments):
        report = None
        try:
            if database.is_department_weekly_leave(department.id, now.weekday(), now.date().isoformat()):
                logger.info("Departman haftalik izinli, atlandi: %s", department.name)
                continue
            if not department.api_key:
                logger.info("API key yok, atlandi: %s", department.name)
                continue
            if not database.get_rules(department.id).is_configured:
                logger.info("Kurallar tanimli degil, atlandi: %s", department.name)
                continue
            report = await generate_department_report_payload(
                database,
                client,
                department.id,
                now.date(),
                now,
                suppress_notified=True,
                use_cache=True,
            )
            if not report.should_send:
                logger.info("Gonderilecek yeni ihlal yok: %s", department.name)
                continue
            chat_id = report.chat_id
            messages = [report.message, *report.extra_messages]
        except Exception as exc:
            chat_id = department.telegram_chat_id
            messages = [f"❌ {department.name} zamanlanmis raporu alinamadi: {exc}"]
            report = None
            logger.exception("Departman raporu hatasi: %s", department.name)

        try:
            for message in messages:
                for part in split_telegram_message(message):
                    await _send_message_with_retry(application, chat_id, part)
            # Basarili gonderimden sonra isaretle (basarisizsa ayni ihlal tekrar denenir)
            if report is not None and report.notification_violations:
                database.mark_notified_violations(
                    department.id, now.date().isoformat(), report.notification_violations
                )
        except Exception:
            logger.exception("Zamanlanmis rapor gonderilemedi: %s", department.name)

        if index < len(departments) - 1 and delay > 0:
            await asyncio.sleep(delay)


async def _send_message_with_retry(application: Application, chat_id: str, text: str) -> None:
    delays = (0, 1, 3)
    last_error: Exception | None = None
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        try:
            await application.bot.send_message(chat_id=chat_id, text=text)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Telegram mesaj parcasi gonderilemedi, tekrar deneniyor.", exc_info=True)
    if last_error is not None:
        raise last_error


def _seconds_until_next_run(config: Config) -> float:
    now = datetime.now(config.timezone)
    if config.report_interval_minutes == 60:
        next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_run = now + timedelta(minutes=config.report_interval_minutes)
    return max(1.0, (next_run - now).total_seconds())


def _is_within_report_window(config: Config, now: datetime) -> bool:
    current_time = now.time()
    return config.scheduler_start_time <= current_time <= config.scheduler_end_time
