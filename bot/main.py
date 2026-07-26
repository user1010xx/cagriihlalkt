from __future__ import annotations

import asyncio
import logging

from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, filters

from bot.config import load_config
from bot.database import Database
from bot.handlers import (
    API_DEPT,
    API_VALUE,
    DEPT_ADD_API,
    DEPT_ADD_NAME,
    DEPT_DELETE_ID,
    LEAVE_DEPT,
    LEAVE_PERS,
    MEETING_DEPT,
    MEETING_PERS,
    PERS_ADD_DEPT,
    PERS_ADD_EXT,
    PERS_ADD_NAME,
    PERS_BULK_FILE,
    PERS_TOGGLE_DEPT,
    PERS_TOGGLE_NAME,
    RESP_ADD_DEPT,
    RESP_ADD_USER,
    RULE_BREAK_INTERVAL,
    RULE_DEPT,
    RULE_MAX_GAP,
    RULE_POST_BREAK,
    RULE_PRE_BREAK,
    RULE_WORK_END,
    RULE_WORK_START,
    WEEKLY_CANCEL_DAY,
    WEEKLY_CANCEL_DEPT,
    WEEKLY_DAY,
    WEEKLY_DEPT,
    WEEKLY_EDIT_ACTION,
    apitanimla_dept,
    apitanimla_start,
    apitanimla_value,
    cancel,
    chat_id_cmd,
    departman_aktif,
    departman_listele,
    departman_pasif,
    departman_sil_name,
    departman_sil_start,
    departmantanimla_api,
    departmantanimla_name,
    departmantanimla_start,
    haftalikizin_day,
    haftalikizin_dept,
    haftalikizin_start,
    haftalikizinduzenle_action,
    haftalikizinduzenle_start,
    haftalikiziniptal_day,
    haftalikiziniptal_dept,
    haftalikiziniptal_start,
    izin_dept,
    izin_personnel,
    izin_start,
    iziniptal_start,
    izinlistele,
    kimim,
    toplanti_dept,
    toplanti_personnel,
    toplantial_start,
    toplantiiptal_start,
    kontroltoniva,
    kuralayarla_break_interval,
    kuralayarla_dept,
    kuralayarla_max_gap,
    kuralayarla_post_break,
    kuralayarla_pre_break,
    kuralayarla_start,
    kuralayarla_work_end,
    kuralayarla_work_start,
    kurallistele,
    personel_aktif_start,
    personel_listele,
    personel_pasif_start,
    personel_sil_start,
    personel_toggle_dept,
    personel_toggle_name,
    personelekle_dept,
    personelekle_ext,
    personelekle_name,
    personelekle_start,
    personeltopluekle_dept,
    personeltopluekle_file,
    personeltopluekle_start,
    rapor,
    sorumluekle_start,
    sorumlu_dept,
    sorumlu_user,
    sorumlulistele_start,
    sorumlusil_start,
    start,
    unknown,
)
from bot.health import run_health_server
from bot.scheduler import run_scheduler
from bot.startup_checks import validate_runtime_setup
from bot.toniva_client import TonivaClient


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    application.bot_data["scheduler_task"] = asyncio.create_task(
        run_scheduler(application),
        name="hourly-report-scheduler",
    )
    application.bot_data["health_task"] = asyncio.create_task(
        run_health_server(application),
        name="health-server",
    )


async def post_shutdown(application: Application) -> None:
    for task_name in ("scheduler_task", "health_task"):
        task = application.bot_data.get(task_name)
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    health_server = application.bot_data.get("health_server")
    if health_server is not None:
        health_server.close()
        await health_server.wait_closed()


def _conv(entry, states: dict) -> ConversationHandler:
    return ConversationHandler(
        entry_points=entry,
        states=states,
        fallbacks=[CommandHandler("iptal", cancel)],
        allow_reentry=True,
    )


def build_application() -> Application:
    config = load_config()
    validate_runtime_setup(config.database_path)
    database = Database(config.database_path)
    client = TonivaClient(config.toniva_api_url, config.request_timeout_seconds)
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["config"] = config
    application.bot_data["database"] = database
    application.bot_data["client"] = client

    async def error_handler(update: object, context) -> None:
        logger.error("Exception while handling update: %s", context.error, exc_info=context.error)

    application.add_error_handler(error_handler)

    text = filters.TEXT & ~filters.COMMAND

    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("chat_id", chat_id_cmd))
    application.add_handler(CommandHandler("kimim", kimim))

    application.add_handler(
        _conv(
            [CommandHandler(["departmantanimla", "departman_tanimla", "departmanekle"], departmantanimla_start)],
            {
                DEPT_ADD_NAME: [MessageHandler(text, departmantanimla_name)],
                DEPT_ADD_API: [MessageHandler(text, departmantanimla_api)],
            },
        )
    )
    application.add_handler(CommandHandler("departman_listele", departman_listele))
    application.add_handler(
        _conv(
            [CommandHandler("departman_sil", departman_sil_start)],
            {DEPT_DELETE_ID: [MessageHandler(text, departman_sil_name)]},
        )
    )
    application.add_handler(CommandHandler("departman_aktif", departman_aktif))
    application.add_handler(CommandHandler("departman_pasif", departman_pasif))

    application.add_handler(
        _conv(
            [CommandHandler(["apitanimla", "api_tanimla"], apitanimla_start)],
            {
                API_DEPT: [MessageHandler(text, apitanimla_dept)],
                API_VALUE: [MessageHandler(text, apitanimla_value)],
            },
        )
    )

    application.add_handler(
        _conv(
            [CommandHandler(["kuralayarla", "kural_ayarla"], kuralayarla_start)],
            {
                RULE_DEPT: [MessageHandler(text, kuralayarla_dept)],
                RULE_WORK_START: [MessageHandler(text, kuralayarla_work_start)],
                RULE_MAX_GAP: [MessageHandler(text, kuralayarla_max_gap)],
                RULE_PRE_BREAK: [MessageHandler(text, kuralayarla_pre_break)],
                RULE_BREAK_INTERVAL: [MessageHandler(text, kuralayarla_break_interval)],
                RULE_POST_BREAK: [MessageHandler(text, kuralayarla_post_break)],
                RULE_WORK_END: [MessageHandler(text, kuralayarla_work_end)],
            },
        )
    )
    application.add_handler(CommandHandler("kurallistele", kurallistele))

    application.add_handler(
        _conv(
            [CommandHandler(["personelekle", "personel_ekle"], personelekle_start)],
            {
                PERS_ADD_DEPT: [MessageHandler(text, personelekle_dept)],
                PERS_ADD_NAME: [MessageHandler(text, personelekle_name)],
                PERS_ADD_EXT: [MessageHandler(text, personelekle_ext)],
            },
        )
    )
    application.add_handler(
        _conv(
            [CommandHandler(["personeltopluekle", "personel_toplu_ekle"], personeltopluekle_start)],
            {
                PERS_ADD_DEPT: [MessageHandler(text, personeltopluekle_dept)],
                PERS_BULK_FILE: [MessageHandler(filters.Document.ALL | filters.ATTACHMENT, personeltopluekle_file)],
            },
        )
    )
    application.add_handler(CommandHandler("personel_listele", personel_listele))
    application.add_handler(
        _conv(
            [
                CommandHandler("personel_sil", personel_sil_start),
                CommandHandler("personel_pasif", personel_pasif_start),
                CommandHandler("personel_aktif", personel_aktif_start),
            ],
            {
                PERS_TOGGLE_DEPT: [MessageHandler(text, personel_toggle_dept)],
                PERS_TOGGLE_NAME: [MessageHandler(text, personel_toggle_name)],
            },
        )
    )

    application.add_handler(
        _conv(
            [
                CommandHandler("izin", izin_start),
                CommandHandler("iziniptal", iziniptal_start),
            ],
            {
                LEAVE_DEPT: [MessageHandler(text, izin_dept)],
                LEAVE_PERS: [MessageHandler(text, izin_personnel)],
            },
        )
    )
    application.add_handler(CommandHandler("izinlistele", izinlistele))

    application.add_handler(
        _conv(
            [
                CommandHandler(["toplantial", "toplanti_al"], toplantial_start),
                CommandHandler(["toplantiiptal", "toplanti_iptal"], toplantiiptal_start),
            ],
            {
                MEETING_DEPT: [MessageHandler(text, toplanti_dept)],
                MEETING_PERS: [MessageHandler(text, toplanti_personnel)],
            },
        )
    )

    application.add_handler(
        _conv(
            [CommandHandler("haftalikizin", haftalikizin_start)],
            {
                WEEKLY_DEPT: [MessageHandler(text, haftalikizin_dept)],
                WEEKLY_DAY: [MessageHandler(text, haftalikizin_day)],
            },
        )
    )
    application.add_handler(
        _conv(
            [CommandHandler("haftalikizinduzenle", haftalikizinduzenle_start)],
            {
                WEEKLY_DEPT: [MessageHandler(text, haftalikizin_dept)],
                WEEKLY_EDIT_ACTION: [MessageHandler(text, haftalikizinduzenle_action)],
            },
        )
    )
    application.add_handler(
        _conv(
            [CommandHandler("haftalikiziniptal", haftalikiziniptal_start)],
            {
                WEEKLY_CANCEL_DEPT: [MessageHandler(text, haftalikiziniptal_dept)],
                WEEKLY_CANCEL_DAY: [MessageHandler(text, haftalikiziniptal_day)],
            },
        )
    )

    application.add_handler(
        _conv(
            [
                CommandHandler("sorumluekle", sorumluekle_start),
                CommandHandler("sorumlusil", sorumlusil_start),
            ],
            {
                RESP_ADD_DEPT: [MessageHandler(text, sorumlu_dept)],
                RESP_ADD_USER: [MessageHandler(text, sorumlu_user)],
            },
        )
    )
    application.add_handler(CommandHandler("sorumlulistele", sorumlulistele_start))
    application.add_handler(CommandHandler("rapor", rapor))
    application.add_handler(CommandHandler(["kontroltoniva", "kontrol_toniva"], kontroltoniva))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    return application


def main() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application()
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
