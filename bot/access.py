from __future__ import annotations

from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.config import Config
from bot.database import Database
from bot.models import Department


def is_private(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type == "private"


def is_group(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type in ("group", "supergroup")


def group_name_allowed(update: Update, config: Config) -> bool:
    """ALLOWED_GROUP_NAMES doluysa yalnizca bu basliklar.

    Bos liste = isimle serbest gecis YOK (tum gruplar acik degil).
    Kayitli departman chat'i is_allowed icinde ayri kontrol edilir.
    """
    chat = update.effective_chat
    if chat is None or chat.type not in ("group", "supergroup"):
        return False
    if not config.allowed_group_names:
        return False
    title = (chat.title or "").strip().casefold()
    return title in config.allowed_group_names


def is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Private: asla.

    Grup: ALLOWED_GROUP_NAMES eslesmesi VEYA bu chat'e bagli kayitli departman.
    Ilk kurulum icin Railway'de grup adlarini tanimlayin.
    """
    if is_private(update):
        return False
    if not is_group(update):
        return False
    config: Config = context.application.bot_data["config"]
    if group_name_allowed(update, config):
        return True
    database: Database | None = context.application.bot_data.get("database")
    chat = update.effective_chat
    if database is not None and chat is not None:
        return any(d.telegram_chat_id == str(chat.id) for d in database.list_departments())
    return False


def can_use_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return is_allowed(update, context)


def departments_in_chat(update: Update, database: Database) -> list[Department]:
    chat = update.effective_chat
    if chat is None:
        return []
    return database.list_departments(chat_id=str(chat.id))


def resolve_department(
    update: Update,
    database: Database,
    identifier: str | None = None,
) -> Department | None:
    chat_departments = departments_in_chat(update, database)
    if identifier:
        department = database.get_department(identifier)
        if department is None:
            return None
        if str(update.effective_chat.id) != department.telegram_chat_id:  # type: ignore[union-attr]
            return None
        return department
    if len(chat_departments) == 1:
        return chat_departments[0]
    return None


def allowed_only(handler: Callable) -> Callable:
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_private(update):
            # Ozel sohbet: tamamen sessiz
            return ConversationHandler.END
        if not is_allowed(update, context):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Bu grup bot icin yetkili degil. Railway ALLOWED_GROUP_NAMES degerini kontrol edin."
                )
            return ConversationHandler.END
        return await handler(update, context)

    return wrapper
