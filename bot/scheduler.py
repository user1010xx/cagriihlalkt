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
    """Saatlik tam rapor: ihlal olsun olmasin her departman grubuna gonderir.

    Rapor icerigi: ozet (ihlal var/yok), ihlal listesi (varsa), personel cagri adedi ve sure.
    Departmanlar sirayla islenir; arada DEPARTMENT_REPORT_DELAY_SECONDS beklenir.
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
    logger.info(
        "Saatlik tam rapor basliyor: %s departman, delay=%ss",
        len(departments),
        delay,
    )

    for index, department in enumerate(departments):
        report = None
        messages: list[str] = []
        chat_id = department.telegram_chat_id
        try:
            # Haftalık izin: kontrol + gruba mesaj yok (service should_send=False)
            if database.is_department_weekly_leave(
                department.id, now.date().weekday(), now.date().isoformat()
            ):
                logger.info("Haftalik izin, sessiz atlandi: %s", department.name)
                if index < len(departments) - 1 and delay > 0:
                    await asyncio.sleep(delay)
                continue
            if not department.api_key:
                logger.info("API key yok, atlandi: %s", department.name)
                continue
            if not database.get_rules(department.id).is_configured:
                logger.info("Kurallar tanimli degil, atlandi: %s", department.name)
                continue

            # suppress_notified=False -> tam rapor (tum ihlaller + personel ozet)
            report = await generate_department_report_payload(
                database,
                client,
                department.id,
                now.date(),
                now,
                suppress_notified=False,
                use_cache=True,
            )
            if not report.should_send:
                logger.info(
                    "Rapor gonderilmedi (should_send=False): %s",
                    department.name,
                )
                if index < len(departments) - 1 and delay > 0:
                    await asyncio.sleep(delay)
                continue
            chat_id = report.chat_id
            messages = [report.message, *report.extra_messages]
            messages = [m for m in messages if m and m.strip()]
        except Exception as exc:
            chat_id = department.telegram_chat_id
            messages = [f"❌ {department.name} saatlik raporu alınamadı: {exc}"]
            report = None
            logger.exception("Departman raporu hatasi: %s", department.name)

        if not messages:
            if index < len(departments) - 1 and delay > 0:
                await asyncio.sleep(delay)
            continue

        try:
            for message in messages:
                for part in split_telegram_message(message):
                    await _send_message_with_retry(application, chat_id, part)
            logger.info("Saatlik rapor gonderildi: %s -> %s", department.name, chat_id)
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
